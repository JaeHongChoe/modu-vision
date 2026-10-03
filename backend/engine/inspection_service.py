"""Standalone, durable HTTP inspection service for a verified flow package.

This module is bundled into exported flow packages. It deliberately has no
Electron or studio API dependency: the package, incoming image and SQLite
state are enough to restart an inspection worker on a CPU edge machine.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sqlite3
from backend.engine.sqlite_wal import use_wal  # a concurrent WAL switch is retried, not failed
import tempfile
import threading
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import cv2
import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from backend.engine.flow_package_runtime import run_flow_package, verify_flow_package


MAX_UPLOAD_BYTES = 32 * 1024 * 1024
VALID_VERDICTS = {"OK", "NG", "REVIEW"}
MAX_INTERRUPTED_ATTEMPTS = 3
INBOX_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_release_policy(package_dir: Path, checkpoints: dict[str, Path], policy_path: Path, *, device: str | None = None) -> None:
    """Check a separately provisioned approval policy before any service state exists."""
    path = Path(policy_path).expanduser()
    if path.is_symlink() or not path.is_file() or path.resolve().is_relative_to(package_dir):
        raise ValueError("Release policy must be a trusted file outside the package")
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
        manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("Release policy or package manifest is unreadable") from exc
    if (not isinstance(policy, dict) or policy.get("schema_version") != 1
            or not isinstance(policy.get("manifest_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", policy["manifest_sha256"])):
        raise ValueError("Invalid release policy")
    if _sha256(package_dir / "manifest.json") != policy["manifest_sha256"]:
        raise ValueError("Release policy manifest SHA-256 does not match the package")
    if manifest.get('runtime_acceptance_sha256') and policy.get('runtime_acceptance_sha256')!=manifest['runtime_acceptance_sha256']:
        raise ValueError('Release policy must bind the explicit precision acceptance receipt')
    revisions = policy.get("approval_revisions")
    if (not isinstance(revisions, list) or len(revisions) != len(checkpoints)
            or manifest.get("release") != {"approval_revisions": revisions}):
        raise ValueError("Package has no matching approved release")
    models = {row["job_id"]: row["task"] for row in manifest["models"]}
    seen: set[str] = set()
    for revision in revisions:
        if (not isinstance(revision, dict)
                or set(revision) != {"revision_id", "job_id", "task", "checkpoint_sha256"}):
            raise ValueError("Invalid approved release revision")
        job_id = revision["job_id"]
        if (not isinstance(job_id, str) or job_id in seen or job_id not in checkpoints
                or revision["task"] != models.get(job_id)
                or not isinstance(revision["revision_id"], str)
                or not re.fullmatch(r"[0-9a-f]{32}", revision["revision_id"])
                or not isinstance(revision["checkpoint_sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", revision["checkpoint_sha256"])
                or _sha256(checkpoints[job_id]) != revision["checkpoint_sha256"]):
            raise ValueError("Approved release checkpoint or revision does not match the package")
        seen.add(job_id)
    from backend.engine.runtime_release_evidence import verify_release_evidence
    accepted_device=policy.get('device')
    if not isinstance(accepted_device,str) or (device is not None and accepted_device!=device):
        raise ValueError('Release policy device differs from actual runtime device')
    parity_sha=policy.get('runtime_acceptance_sha256') if manifest.get('runtime_acceptance_sha256') else policy.get('parity_receipt_sha256')
    if not isinstance(parity_sha,str) or not re.fullmatch('[0-9a-f]{64}',parity_sha):
        raise ValueError('Release policy must bind a completed cohort acceptance receipt checksum')
    verify_release_evidence(package_dir,accepted_device,expected_receipt_sha256=parity_sha)


class FileJob(BaseModel):
    image_path: str = Field(min_length=1)
    image_id: str | None = None


class DeviceEvent(BaseModel):
    device_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    event_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    image_path: str = Field(min_length=1)


class InspectionStore:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.database = state_dir / "inspection_service.sqlite3"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "uploads").mkdir(exist_ok=True)
        with self._connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    image_path TEXT NOT NULL,
                    image_id TEXT,
                    image_sha256 TEXT NOT NULL,
                    source TEXT NOT NULL,
                    state TEXT NOT NULL,
                    verdict TEXT,
                    model_verdict TEXT,
                    result_json TEXT,
                    error TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id),
                    state TEXT NOT NULL,
                    message TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS jobs_state_created ON jobs(state, created_at);
                CREATE TABLE IF NOT EXISTS inbox_items (
                    image_path TEXT NOT NULL,
                    image_sha256 TEXT NOT NULL,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id),
                    PRIMARY KEY(image_path, image_sha256)
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    job_id TEXT PRIMARY KEY REFERENCES jobs(job_id),
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS device_events (
                    device_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id),
                    PRIMARY KEY(device_id, event_id)
                );
            """)
            # Databases created before result delivery was added remain readable.
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
            if "model_verdict" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN model_verdict TEXT")
            # Counts only attempts interrupted by a worker stop, not operator retries.
            if "interrupted" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN interrupted INTEGER NOT NULL DEFAULT 0")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.database, timeout=10)
        conn.row_factory = sqlite3.Row
        use_wal(conn)
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _event(self, conn: sqlite3.Connection, job_id: str, state: str, message: str | None = None) -> None:
        conn.execute(
            "INSERT INTO events(job_id,state,message,created_at) VALUES(?,?,?,?)",
            (job_id, state, message, _now()),
        )

    def recover(self) -> None:
        """A killed process leaves running rows retryable with an audit event.

        An input that kills the worker on every attempt would otherwise loop
        forever; after MAX_INTERRUPTED_ATTEMPTS interrupted attempts it becomes
        REVIEW for an operator. Operator retries start a new count.
        """
        with self._connection() as conn:
            rows = conn.execute("SELECT job_id, interrupted FROM jobs WHERE state='running'").fetchall()
            for row in rows:
                interrupted = row["interrupted"] + 1
                if interrupted >= MAX_INTERRUPTED_ATTEMPTS:
                    message = (f"Worker stopped during {interrupted} consecutive inspection attempts; "
                               "quarantined for operator review")
                    conn.execute(
                        "UPDATE jobs SET state='error',verdict='REVIEW',error=?,interrupted=?,updated_at=? WHERE job_id=?",
                        (message, interrupted, _now(), row["job_id"]),
                    )
                    self._event(conn, row["job_id"], "error", message)
                    continue
                conn.execute("UPDATE jobs SET state='queued', interrupted=?, updated_at=? WHERE job_id=?",
                             (interrupted, _now(), row["job_id"]))
                self._event(conn, row["job_id"], "queued", "Worker restarted before the prior result was committed")
            pending = conn.execute(
                "SELECT job_id FROM deliveries WHERE state IN ('sending','failed')",
            ).fetchall()
            for row in pending:
                conn.execute("UPDATE deliveries SET state='pending', updated_at=? WHERE job_id=?", (_now(), row["job_id"]))
                conn.execute(
                    "UPDATE jobs SET state='delivery_pending',verdict='REVIEW',updated_at=? WHERE job_id=?",
                    (_now(), row["job_id"]),
                )
                self._event(conn, row["job_id"], "delivery_pending", "Result delivery resumed after restart")

    def enqueue(self, image_path: Path, source: str, image_id: str | None = None) -> str:
        if image_path.is_symlink() or not image_path.is_file():
            raise ValueError("Inspection image is missing or is a symbolic link")
        digest = _sha256(image_path)
        job_id = uuid.uuid4().hex
        timestamp = _now()
        with self._connection() as conn:
            if source == "inbox":
                existing = conn.execute(
                    "SELECT job_id FROM inbox_items WHERE image_path=? AND image_sha256=?",
                    (str(image_path.resolve()), digest),
                ).fetchone()
                if existing:
                    return existing["job_id"]
            conn.execute(
                """INSERT INTO jobs(job_id,image_path,image_id,image_sha256,source,state,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (job_id, str(image_path.resolve()), image_id, digest, source, "queued", timestamp, timestamp),
            )
            if source == "inbox":
                conn.execute(
                    "INSERT INTO inbox_items(image_path,image_sha256,job_id) VALUES(?,?,?)",
                    (str(image_path.resolve()), digest, job_id),
                )
            self._event(conn, job_id, "queued")
        return job_id

    def enqueue_device_event(self, device_id: str, event_id: str, image_path: Path) -> str:
        """Keep one durable row per device trigger, including a missing capture."""
        resolved = image_path.expanduser().resolve()
        try:
            if image_path.is_symlink() or not resolved.is_file():
                raise ValueError("Device capture is missing or is a symbolic link")
            digest = _sha256(resolved)
            error = None
        except (OSError, ValueError) as exc:
            digest = ""
            error = str(exc)
        job_id = uuid.uuid4().hex
        stamp = _now()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT job_id FROM device_events WHERE device_id=? AND event_id=?",
                (device_id, event_id),
            ).fetchone()
            if existing:
                return existing["job_id"]
            state = "error" if error else "queued"
            conn.execute(
                """INSERT INTO jobs(job_id,image_path,image_id,image_sha256,source,state,verdict,error,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (job_id, str(resolved), event_id, digest, f"device:{device_id}", state,
                 "REVIEW" if error else None, error, stamp, stamp),
            )
            conn.execute(
                "INSERT INTO device_events(device_id,event_id,job_id) VALUES(?,?,?)",
                (device_id, event_id, job_id),
            )
            self._event(conn, job_id, state, error)
        return job_id

    def claim(self) -> dict[str, Any] | None:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created_at, rowid LIMIT 1").fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE jobs SET state='running', attempts=attempts+1, updated_at=? WHERE job_id=?",
                (_now(), row["job_id"]),
            )
            self._event(conn, row["job_id"], "running")
            return dict(row)

    def finish(
        self, job_id: str, *, result: dict[str, Any] | None = None,
        error: str | None = None, require_delivery: bool = False,
    ) -> None:
        state = "error" if error else "delivery_pending" if require_delivery else "completed"
        model_verdict = result["final_verdict"] if result else None
        verdict = "REVIEW" if error or require_delivery else model_verdict
        with self._connection() as conn:
            conn.execute(
                """UPDATE jobs SET state=?,verdict=?,model_verdict=?,result_json=?,error=?,updated_at=?
                   WHERE job_id=? AND state='running'""",
                (state, verdict, model_verdict, json.dumps(result, ensure_ascii=False) if result else None,
                 error, _now(), job_id),
            )
            self._event(conn, job_id, state, error)
            if require_delivery and not error:
                conn.execute(
                    "INSERT INTO deliveries(job_id,state,updated_at) VALUES(?,?,?)",
                    (job_id, "pending", _now()),
                )

    def claim_delivery(self) -> dict[str, Any] | None:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT job_id FROM deliveries WHERE state='pending' ORDER BY updated_at LIMIT 1",
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE deliveries SET state='sending',attempts=attempts+1,updated_at=? WHERE job_id=?",
                (_now(), row["job_id"]),
            )
            return dict(conn.execute("SELECT * FROM jobs WHERE job_id=?", (row["job_id"],)).fetchone())

    def finish_delivery(self, job_id: str, error: str | None) -> None:
        with self._connection() as conn:
            if error:
                conn.execute(
                    "UPDATE deliveries SET state='failed',last_error=?,updated_at=? WHERE job_id=?",
                    (error, _now(), job_id),
                )
                conn.execute(
                    "UPDATE jobs SET state='delivery_error',verdict='REVIEW',error=?,updated_at=? WHERE job_id=?",
                    (error, _now(), job_id),
                )
                self._event(conn, job_id, "delivery_error", error)
            else:
                conn.execute(
                    "UPDATE deliveries SET state='sent',last_error=NULL,updated_at=? WHERE job_id=?",
                    (_now(), job_id),
                )
                conn.execute(
                    "UPDATE jobs SET state='completed',verdict=model_verdict,error=NULL,updated_at=? WHERE job_id=?",
                    (_now(), job_id),
                )
                self._event(conn, job_id, "completed", "Result delivery acknowledged")

    def retry_delivery(self, job_id: str) -> bool:
        with self._connection() as conn:
            changed = conn.execute(
                "UPDATE deliveries SET state='pending',last_error=NULL,updated_at=? WHERE job_id=? AND state='failed'",
                (_now(), job_id),
            ).rowcount
            if changed:
                conn.execute(
                    "UPDATE jobs SET state='delivery_pending',error=NULL,updated_at=? WHERE job_id=?",
                    (_now(), job_id),
                )
                self._event(conn, job_id, "delivery_pending", "Operator requested result redelivery")
            return bool(changed)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        result_json = item.pop("result_json")
        item["result"] = json.loads(result_json) if result_json else None
        return item

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connection() as conn:
            ids = [row["job_id"] for row in conn.execute(
                "SELECT job_id FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,),
            ).fetchall()]
        return [item for job_id in ids if (item := self.get(job_id)) is not None]

    def events(self, job_id: str) -> list[dict[str, Any]]:
        with self._connection() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT state,message,created_at FROM events WHERE job_id=? ORDER BY event_id", (job_id,),
            ).fetchall()]

    def retry(self, job_id: str) -> bool:
        with self._connection() as conn:
            cursor = conn.execute(
                "UPDATE jobs SET state='queued',verdict=NULL,result_json=NULL,error=NULL,interrupted=0,updated_at=? "
                "WHERE job_id=? AND state='error'", (_now(), job_id),
            )
            if cursor.rowcount:
                self._event(conn, job_id, "queued", "Operator requested retry")
            return bool(cursor.rowcount)

    def pending_count(self) -> int:
        with self._connection() as conn:
            return int(conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running')",
            ).fetchone()[0])


def _inspect_job(store: InspectionStore, package_dir: Path, row: dict[str, Any], require_delivery: bool, device: str = "cpu", runtime_identity: dict | None = None, deadline_ms: int | None = None) -> None:
    path = Path(row["image_path"])
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError("Inspection image is missing")
        if _sha256(path) != row["image_sha256"]:
            raise ValueError("Inspection image changed after it was queued")
        if deadline_ms is not None:
            result=run_flow_package(package_dir,path,row['image_id'],device=device,deadline_ms=deadline_ms)
        else:
            result = run_flow_package(package_dir, path, row["image_id"], device=device)
        if runtime_identity and isinstance(result, dict): result["runtime_identity"] = runtime_identity
        if not isinstance(result, dict) or result.get("final_verdict") not in VALID_VERDICTS:
            raise ValueError("Package returned no valid final verdict")
        if result.get('status')=='timeout':
            store.finish(row['job_id'],result=result,error='INFERENCE_DEADLINE_EXCEEDED')
            return
        if result.get("status") in {"error", "failed"} or result.get("error_message"):
            raise ValueError(f"Package execution failed: {result.get('error_message') or result.get('status')}")
        store.finish(row["job_id"], result=result, require_delivery=require_delivery)
    except Exception as exc:  # A model or device error must become durable REVIEW evidence.
        store.finish(row["job_id"], error=f"{type(exc).__name__}: {exc}")


def _deliver_job(store: InspectionStore, row: dict[str, Any], url: str, token: str | None) -> None:
    payload = {
        "job_id": row["job_id"],
        "model_verdict": row["model_verdict"],
        "image_sha256": row["image_sha256"],
        "result": json.loads(row["result_json"]),
    }
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        response = httpx.post(url, json=payload, headers=headers, timeout=5)
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(f"Result destination returned HTTP {response.status_code}")
        store.finish_delivery(row["job_id"], None)
    except Exception as exc:
        store.finish_delivery(row["job_id"], f"{type(exc).__name__}: {exc}")


def _scan_inbox(store: InspectionStore, inbox_dir: Path) -> str:
    """Enqueue settled image files; path+content hash prevents duplicate restart runs."""
    if not inbox_dir.is_dir():
        return "disconnected"
    now = time.time()
    try:
        for path in sorted(inbox_dir.rglob("*")):
            if path.suffix.lower() not in INBOX_EXTENSIONS or path.is_symlink() or not path.is_file():
                continue
            if not path.resolve().is_relative_to(inbox_dir) or now - path.stat().st_mtime < 1:
                continue
            try:
                store.enqueue(path, "inbox")
            except (OSError, ValueError):
                # A changing or unreadable file will be picked up on a later scan.
                continue
    except OSError:
        return "disconnected"
    return "connected"


def create_service_app(
    package_dir: Path,
    state_dir: Path,
    *,
    token: str,
    auto_worker: bool = True,
    allowed_input_root: Path | None = None,
    inbox_dir: Path | None = None,
    result_webhook_url: str | None = None,
    result_webhook_token: str | None = None,
    camera_source: str | int | None = None,
    camera_frame_interval: float = 1.0,
    require_approved_release: bool = False,
    release_policy: Path | None = None,
    device: str | None = None,
    runtime_root: Path | None = None,
    adapter_config_path: Path | None = None,
    deadline_ms: int | None = None,
) -> FastAPI:
    """Verify a package before creating mutable state or loading a checkpoint."""
    if not token:
        raise ValueError("An API token is required")
    from backend.engine.runtime_deadline import validate_deadline
    validate_deadline(deadline_ms)
    package_dir = Path(package_dir).expanduser().resolve()
    pipeline, checkpoints = verify_flow_package(package_dir)
    device=device or json.loads((package_dir/'manifest.json').read_text(encoding='utf-8')).get('runtime',{}).get('device','cpu')
    from backend.engine.edge_runtime import enforce_edge_device
    enforce_edge_device(package_dir, device)
    if require_approved_release and release_policy is None:
        raise ValueError("Approved release policy is required")
    if release_policy is not None:
        _verify_release_policy(package_dir, checkpoints, release_policy,device=device)
    from backend.engine.runtime_device import resolve_package_device as resolve_runtime_device
    resolve_runtime_device(device)
    store = InspectionStore(Path(state_dir).expanduser().resolve())
    from backend.engine.service_runtime import ServiceRuntime
    from backend.engine.field_adapters import load_adapter_config, deliver_configured, ModbusTCPAdapter
    runtime = ServiceRuntime(package_dir, store.state_dir, device, release_policy, runtime_root, _verify_release_policy)
    field_config = load_adapter_config(adapter_config_path)
    require_delivery = bool(result_webhook_url) or bool(field_config.enabled and (field_config.modbus or field_config.mes))
    input_root = Path(allowed_input_root).expanduser().resolve() if allowed_input_root else None
    inbox = Path(inbox_dir).expanduser().resolve() if inbox_dir else None
    if result_webhook_url and not result_webhook_url.startswith(("http://", "https://")):
        raise ValueError("Result webhook must be an HTTP(S) URL")
    if camera_frame_interval < 0.1:
        raise ValueError("Camera frame interval must be at least 0.1 seconds")
    adapter_state = {
        "file_inbox": "disabled" if inbox is None else "checking",
        "camera": "disabled" if camera_source is None else "checking",
        "modbus": "configured" if field_config.modbus else "disabled",
        "mes": "configured" if field_config.mes else "disabled",
        "field_enabled": field_config.enabled,
    }
    stop = threading.Event()

    def worker() -> None:
        last_scan = 0.0
        while not stop.is_set():
            if inbox is not None and time.monotonic() - last_scan >= 0.5:
                adapter_state["file_inbox"] = _scan_inbox(store, inbox)
                last_scan = time.monotonic()
            if field_config.enabled and field_config.modbus and field_config.modbus.trigger_register is not None and field_config.modbus.trigger_image_path:
                try:
                    config = field_config.modbus
                    event = ModbusTCPAdapter(config).read_register(config.trigger_register)
                    image = Path(config.trigger_image_path).resolve(strict=True)
                    if input_root is None or not image.is_relative_to(input_root): raise ValueError("PLC trigger image must be under configured input root")
                    if event: store.enqueue_device_event(f"modbus:{config.host}:{config.port}", str(event), image)
                    adapter_state["modbus"] = "connected"
                except Exception: adapter_state["modbus"] = "disconnected"
            row = store.claim()
            if row is None:
                delivery = store.claim_delivery() if require_delivery else None
                if delivery:
                    if field_config.enabled and (field_config.modbus or field_config.mes):
                        try:
                            deliver_configured(field_config, {"job_id": delivery["job_id"], "model_verdict": delivery["model_verdict"], "image_sha256": delivery["image_sha256"], "result": json.loads(delivery["result_json"])})
                            if result_webhook_url: _deliver_job(store, delivery, result_webhook_url, result_webhook_token)
                            else: store.finish_delivery(delivery["job_id"], None)
                            adapter_state["delivery"] = "acknowledged"
                        except Exception as exc:
                            adapter_state["delivery"] = "disconnected"
                            store.finish_delivery(delivery["job_id"], f"{type(exc).__name__}: {exc}")
                    else: _deliver_job(store, delivery, result_webhook_url, result_webhook_token)
                else:
                    stop.wait(0.1)
            else:
                with runtime.lock:
                    identity = runtime.read()
                    _inspect_job(store, Path(identity["package_path"]), row, require_delivery, identity["device"], identity,deadline_ms)

    def camera_worker() -> None:
        if camera_source is None:
            return
        source = int(camera_source) if isinstance(camera_source, str) and camera_source.isdecimal() else camera_source
        while not stop.is_set():
            capture = None
            try:
                capture = cv2.VideoCapture(source)
                if not capture.isOpened():
                    adapter_state["camera"] = "disconnected"
                else:
                    adapter_state["camera"] = "connected"
                    while not stop.is_set():
                        ok, frame = capture.read()
                        if not ok or frame is None:
                            adapter_state["camera"] = "disconnected"
                            break
                        if store.pending_count() >= 100:
                            adapter_state["camera"] = "backpressure"
                            stop.wait(camera_frame_interval)
                            continue
                        output = store.state_dir / "uploads" / f"camera-{uuid.uuid4().hex}.png"
                        if not cv2.imwrite(str(output), frame):
                            adapter_state["camera"] = "error"
                            break
                        store.enqueue(output, "camera")
                        adapter_state["camera"] = "connected"
                        stop.wait(camera_frame_interval)
            except Exception:
                adapter_state["camera"] = "error"
            finally:
                if capture is not None:
                    capture.release()
            stop.wait(5)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        store.recover()
        thread = threading.Thread(target=worker, daemon=True, name="inspection-service-worker") if auto_worker else None
        camera_thread = threading.Thread(target=camera_worker, daemon=True, name="inspection-camera") if camera_source is not None and auto_worker else None
        if thread:
            thread.start()
        if camera_thread:
            camera_thread.start()
        try:
            yield
        finally:
            stop.set()
            if thread:
                thread.join(timeout=10)
            if camera_thread:
                camera_thread.join(timeout=10)

    app = FastAPI(title="Modu Vision Inspection Service", version="1", lifespan=lifespan)
    app.state.inspection_store = store
    app.state.pipeline_id = pipeline.id

    def authorized(request: Request) -> None:
        supplied = request.headers.get("x-vision-token", "")
        if not secrets.compare_digest(supplied, token):
            raise HTTPException(status_code=401, detail="Inspection service token required")

    @app.get("/health")
    def health():
        return runtime.read()

    @app.get("/v1/runtime", dependencies=[Depends(authorized)])
    def runtime_readback():
        return runtime.read()

    @app.post("/v1/runtime/apply", dependencies=[Depends(authorized)])
    def runtime_apply(payload: dict):
        try:
            return runtime.apply(payload["package_path"], payload.get("release_policy"), payload.get("device", "cpu"), payload["manifest_sha256"])
        except (KeyError, ValueError, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/v1/adapters", dependencies=[Depends(authorized)])
    def adapters():
        return dict(adapter_state)

    @app.post("/v1/jobs/file", status_code=202, dependencies=[Depends(authorized)])
    def enqueue_file(payload: FileJob):
        path = Path(payload.image_path).expanduser()
        if input_root and not path.resolve().is_relative_to(input_root):
            raise HTTPException(status_code=403, detail="Image is outside the allowed input root")
        try:
            job_id = store.enqueue(path, "file", payload.image_id)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"job_id": job_id, "state": "queued"}

    @app.post("/v1/jobs/upload", status_code=202, dependencies=[Depends(authorized)])
    async def enqueue_upload(request: Request):
        upload_dir = store.state_dir / "uploads"
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=upload_dir, prefix=".upload-", delete=False) as handle:
                temp_path = Path(handle.name)
                size = 0
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise HTTPException(status_code=413, detail="Image exceeds the upload limit")
                    handle.write(chunk)
            try:
                with Image.open(temp_path) as image:
                    if image.width * image.height > 100_000_000:
                        raise ValueError("Image dimensions exceed the service limit")
                    image.verify()
            except (UnidentifiedImageError, OSError, ValueError) as exc:
                raise HTTPException(status_code=422, detail="Upload is not a readable image") from exc
            final_path = upload_dir / f"{uuid.uuid4().hex}.image"
            os.replace(temp_path, final_path)
            temp_path = None
            job_id = store.enqueue(final_path, "http")
            return {"job_id": job_id, "state": "queued"}
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    @app.post("/v1/device-events", status_code=202, dependencies=[Depends(authorized)])
    def receive_device_event(payload: DeviceEvent):
        path = Path(payload.image_path).expanduser()
        if input_root and not path.resolve().is_relative_to(input_root):
            raise HTTPException(status_code=403, detail="Device capture is outside the allowed input root")
        job_id = store.enqueue_device_event(payload.device_id, payload.event_id, path)
        item = store.get(job_id)
        return {"job_id": job_id, "state": item["state"], "verdict": item["verdict"]}

    @app.get("/v1/jobs", dependencies=[Depends(authorized)])
    def list_jobs(limit: int = 100):
        return {"jobs": store.list(max(1, min(limit, 500)))}

    @app.get("/v1/jobs/{job_id}", dependencies=[Depends(authorized)])
    def get_job(job_id: str):
        item = store.get(job_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Inspection job not found")
        return item

    @app.get("/v1/jobs/{job_id}/events", dependencies=[Depends(authorized)])
    def get_events(job_id: str):
        if store.get(job_id) is None:
            raise HTTPException(status_code=404, detail="Inspection job not found")
        return {"events": store.events(job_id)}

    @app.post("/v1/jobs/{job_id}/retry", status_code=202, dependencies=[Depends(authorized)])
    def retry_job(job_id: str):
        if not store.retry(job_id):
            raise HTTPException(status_code=409, detail="Only failed inspections can be retried")
        return {"job_id": job_id, "state": "queued"}

    @app.post("/v1/jobs/{job_id}/retry-delivery", status_code=202, dependencies=[Depends(authorized)])
    def retry_delivery(job_id: str):
        if not store.retry_delivery(job_id):
            raise HTTPException(status_code=409, detail="Only failed result deliveries can be retried")
        return {"job_id": job_id, "state": "delivery_pending"}

    return app


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a verified flow package as a persistent inspection service")
    parser.add_argument("--package", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--token", default=os.environ.get("VISION_INSPECTION_TOKEN"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--input-root", type=Path)
    parser.add_argument("--inbox", type=Path, help="Watch a file inbox for settled images")
    parser.add_argument("--result-webhook-url", help="MES/device endpoint for verdict delivery")
    parser.add_argument("--result-webhook-token", default=os.environ.get("VISION_RESULT_WEBHOOK_TOKEN"))
    parser.add_argument("--camera-source", help="OpenCV device index, video path, or RTSP URI")
    parser.add_argument("--camera-frame-interval", type=float, default=1.0)
    parser.add_argument("--require-approved-release", action="store_true")
    parser.add_argument("--release-policy", type=Path, help="Trusted approval policy outside the package")
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--device", default=None)
    parser.add_argument("--adapter-config", type=Path)
    parser.add_argument('--deadline-ms',type=int)
    args = parser.parse_args()
    if not args.token:
        parser.error("--token or VISION_INSPECTION_TOKEN is required")
    app = create_service_app(
        args.package, args.state_dir, token=args.token,
        allowed_input_root=args.input_root, inbox_dir=args.inbox,
        result_webhook_url=args.result_webhook_url, result_webhook_token=args.result_webhook_token,
        camera_source=args.camera_source, camera_frame_interval=args.camera_frame_interval,
        require_approved_release=args.require_approved_release, release_policy=args.release_policy,
        runtime_root=args.runtime_root, device=args.device, adapter_config_path=args.adapter_config,deadline_ms=args.deadline_ms,
    )
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
