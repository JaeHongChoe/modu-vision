"""Read-only impact queries settle their original SQLite handle before return."""
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from backend.engine import workflow_impact


@pytest.fixture
def owned_project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    project = {
        "id": "owned-read-only-impact",
        "project_dir": str(root),
        "models_dir": str(root / "models"),
        "dataset_dir": str(root / "dataset"),
        "annotations_dir": str(root / "annotations"),
        "source_dataset_dir": str(tmp_path / "source"),
        "active_labelset_id": "default",
        "task": "classification",
    }
    from backend.api.routes_model_deployments import _store
    with _store(project) as connection:
        connection.execute(
            "INSERT INTO revisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("owned-revision", project["source_dataset_dir"], "classification",
             "owned-job", "checkpoint-hash", "training-fingerprint", "evaluation-fingerprint",
             "comparison-owned", "comparison-hash", None, None, "approve",
             "owned-reviewer", "original approval reason", "2026-10-01"),
        )
        connection.execute("INSERT INTO active_revisions VALUES (?,?,?)",
                           (project["source_dataset_dir"], "classification", "owned-revision"))
    # SQLite can materialize empty WAL/SHM files on a genuine read-only query.
    # Initialize that normal read state before freezing all file bytes; no
    # sidecar is removed or exempted from the before/after comparison.
    database = root / "model_deployments.sqlite3"
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection, connection:
        assert connection.execute("SELECT revision_id FROM revisions").fetchall() == [("owned-revision",)]
    return project


def retain_real_connections(monkeypatch, *, query_error=False):
    connect = sqlite3.connect
    retained = []

    class RetainedConnection:
        """Keep the actual connection alive so GC cannot mask a missing close."""
        def __init__(self, actual):
            self.actual = actual
            self.close_count = 0

        @property
        def row_factory(self):
            return self.actual.row_factory

        @row_factory.setter
        def row_factory(self, value):
            self.actual.row_factory = value

        def execute(self, *args):
            if query_error:
                raise sqlite3.OperationalError("Controlled original read query failure")
            return self.actual.execute(*args)

        def __enter__(self):
            self.actual.__enter__()
            return self

        def __exit__(self, *args):
            return self.actual.__exit__(*args)

        def close(self):
            self.close_count += 1
            self.actual.close()

    def capture(database_uri, **options):
        assert database_uri.endswith("?mode=ro") and options == {"uri": True}
        original = RetainedConnection(connect(database_uri, **options))
        retained.append(original)
        return original

    monkeypatch.setattr(workflow_impact.sqlite3, "connect", capture)
    return retained


def assert_closed_without_gc(retained):
    assert len(retained) == 1
    assert retained[0].close_count == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        retained[0].actual.execute("SELECT 1")


def all_file_bytes(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("reader", [workflow_impact.analyze, workflow_impact.legacy_impact])
def test_impact_reads_close_exact_original_connection_and_preserve_records(owned_project, monkeypatch, reader):
    root = Path(owned_project["project_dir"])
    before = all_file_bytes(root)
    retained = retain_real_connections(monkeypatch)
    try:
        report = reader(owned_project)
        assert report["quality_approved"] is False
        if reader is workflow_impact.analyze:
            assert report["approvals"][0]["revision_id"] == "owned-revision"
        else:
            assert any(row["source_ids"].get("revision_id") == "owned-revision"
                       for row in report["unknown"] + report["unaffected"] + report["affected"])
        assert_closed_without_gc(retained)
        assert all_file_bytes(root) == before
    finally:
        # A red test preserves its failure yet closes only this test's original
        # retained connection; no collector or inferred handle is used.
        for original in retained:
            if original.close_count == 0:
                original.actual.close()


@pytest.mark.parametrize("reader", [workflow_impact.analyze, workflow_impact.legacy_impact])
def test_impact_query_error_closes_exact_original_connection(owned_project, monkeypatch, reader):
    database = Path(owned_project["project_dir"]) / "model_deployments.sqlite3"
    before = all_file_bytes(database.parent)
    retained = retain_real_connections(monkeypatch, query_error=True)
    try:
        if reader is workflow_impact.analyze:
            with pytest.raises(sqlite3.OperationalError, match="Controlled original read query failure"):
                reader(owned_project)
        else:
            report = reader(owned_project)
            assert report["quality_approved"] is False
            assert any(row["issue"] == "unreadable_evidence" for row in report["unknown"])
        assert_closed_without_gc(retained)
        assert all_file_bytes(database.parent) == before
    finally:
        for original in retained:
            if original.close_count == 0:
                original.actual.close()


@pytest.mark.parametrize("reader", [workflow_impact.analyze, workflow_impact.legacy_impact])
def test_closed_read_keeps_committed_live_wal_revision_visible(owned_project, monkeypatch, reader):
    from backend.api.routes_model_deployments import _store
    root = Path(owned_project["project_dir"])
    database = root / "model_deployments.sqlite3"
    # Hold only this fixture's original writer, with a committed revision still
    # in its live WAL. Immutable/no-lock reads would lose this real evidence.
    with _store(owned_project) as writer:
        writer.execute("UPDATE revisions SET revision_id = ? WHERE revision_id = ?",
                       ("owned-live-revision", "owned-revision"))
        writer.execute("UPDATE active_revisions SET revision_id = ?",
                       ("owned-live-revision",))
        writer.commit()
        assert database.with_name(database.name + "-wal").stat().st_size > 0
        with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as prime, prime:
            assert prime.execute("SELECT revision_id FROM revisions").fetchall() == [("owned-live-revision",)]
        before = all_file_bytes(root)
        retained = retain_real_connections(monkeypatch)
        try:
            report = reader(owned_project)
            assert report["quality_approved"] is False
            if reader is workflow_impact.analyze:
                assert report["approvals"][0]["revision_id"] == "owned-live-revision"
            else:
                assert any(row["source_ids"].get("revision_id") == "owned-live-revision"
                           for row in report["unknown"] + report["unaffected"] + report["affected"])
            assert_closed_without_gc(retained)
            assert all_file_bytes(root) == before
        finally:
            for original in retained:
                if original.close_count == 0:
                    original.actual.close()
