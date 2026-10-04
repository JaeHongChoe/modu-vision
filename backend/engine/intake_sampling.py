"""Field intake sampling (E06): which inspected images are kept for people to review, within a budget.

A policy names what makes an image worth keeping (its reasons: an NG or REVIEW verdict, an operator flag, a model
disagreement, or a low-confidence score of one declared score type) and how much may be kept per window: items, bytes,
items per product / lot / camera, and a baseline of ordinary images, so the kept set never becomes only the unusual
cases.

- Windows are ``window_seconds`` long, counted from the Unix epoch plus ``window_offset_seconds`` (an offset of 54,000 s
  starts a daily window at 00:00 in UTC+9). A window's budget is the window's, whatever policy revision decided its
  images: a revision changed within a window continues from what the earlier one kept (the limits are the current
  revision's).
- The baseline: ``normal_baseline_fraction`` of the item budget (rounded up) is reserved for ordinary images, room for
  every baseline slot in each product / lot / camera quota (a line with one product, lot and camera can fill them all),
  and the same fraction of the byte budget; each reserve is capped so eligible images keep at least one item or byte.
  Eligible images never spend the baseline's reserves. The window is cut into as many
  equal slots as baseline items, and each slot keeps at most one: the first ordinary image in it whose
  sha256(seed:event_id) falls below the same fraction, while the baseline's reserved key and byte share used so far stays
  within its pace (by slot s, (s + 1) / slots of the reserve). The kept baseline is spread over the window even on a line
  with one product, lot and camera, so it is not only its first minutes; a slot with no such image keeps none, and a
  reserve left unused is not given to eligible images. The pick inside a slot is its first such image (a systematic
  sample from fixed clock instants: a line whose cycle aligns with the slots biases it). An image is ordinary when it
  carries no sign at all: no NG or REVIEW verdict, no flag, and no score that is inside the band or could not be compared
  (a score of another type is not ordinary, it is unknown).
- Every decision is a receipt: selected or skipped, why (every quota that bound, or ``too_large`` for an image bigger
  than the whole byte budget), under which policy revision, with the run, node and recipe the image came from. An
  event id belongs to one image for the life of the store: the same event again (compared canonically: 1 and 1.0, a
  repeated flag and a score spec's extra keys are the same event) returns its first receipt and spends nothing; another
  image under a used id is refused (``EventIdReused``). A store keeps one window geometry (its length, offset and number
  of baseline slots): a revision that changes it is refused.
- The same policy, seed and events in the same order give the same receipts.
- A selected image is only proposed for review: nothing here labels it, and it never becomes test truth by being kept.
  Its retention is ``pending`` until the caller marks it ``retained`` or ``failed`` (a later mark replaces an earlier
  one, so a retry can store a failed image; no history is kept); ``status`` counts failed images and images still
  pending after a grace period (a writer that died before storing them). A failed or pending image keeps the budget it
  was selected under.
- Old windows are removed with ``prune`` (the retention policy that calls it is S5-09's), one window per transaction. A
  window that still has an image pending retention is not pruned. The store remembers the oldest window it kept: an
  event of a pruned window (a replay after an outage, or a late image) is skipped as ``window_pruned`` and spends
  nothing, so no pruned window is decided again with a fresh budget.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from backend.engine.score_contract import compatible_scores, validate_score_spec
from backend.engine.sqlite_wal import use_wal

REASONS = ('ng_verdict', 'review_verdict', 'operator_flag', 'model_disagreement', 'low_confidence')
VERDICTS = (None, 'OK', 'NG', 'REVIEW')
FLAGS = ('operator_flag', 'model_disagreement')
SCHEMA_VERSION = 3
MAX_SECONDS = 1e11  # capture times beyond this (the year 5138) are refused
RETENTION_GRACE_SECONDS = 300.0


class EventIdReused(ValueError):
    """Another image arrived under an event id the store already decided."""


def _real(value) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


@dataclass(frozen=True)
class IntakeSamplingPolicy:
    revision: int
    seed: int
    eligibility_reasons: tuple[str, ...]
    window_seconds: int
    max_items_per_window: int
    max_bytes_per_window: int
    per_product_lot_camera_quota: int
    normal_baseline_fraction: float
    score_spec: Optional[dict] = None             # the score type a low-confidence band applies to
    low_confidence_band: Optional[tuple[float, float]] = None
    window_offset_seconds: int = 0
    _ref: str = field(init=False, repr=False, compare=False, default='')

    def __post_init__(self):
        for name in ('revision', 'window_seconds', 'max_items_per_window', 'max_bytes_per_window', 'per_product_lot_camera_quota'):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f'{name} must be a whole number of at least 1')
        if type(self.seed) is not int:
            raise ValueError('seed must be a whole number')
        if type(self.window_offset_seconds) is not int or not 0 <= self.window_offset_seconds < self.window_seconds:
            raise ValueError('window_offset_seconds must be a whole number from 0 to below window_seconds')
        reasons = tuple(self.eligibility_reasons) if isinstance(self.eligibility_reasons, (tuple, list)) else ()
        if not reasons or len(set(reasons)) != len(reasons) or any(reason not in REASONS for reason in reasons):
            raise ValueError(f"eligibility_reasons must be distinct values of {', '.join(REASONS)}")
        object.__setattr__(self, 'eligibility_reasons', reasons)
        fraction = self.normal_baseline_fraction
        if not _real(fraction) or not 0 <= fraction < 1:
            raise ValueError('normal_baseline_fraction must be from 0 to below 1')
        object.__setattr__(self, 'normal_baseline_fraction', float(fraction))
        if fraction and math.ceil(self.max_items_per_window * fraction) >= self.max_items_per_window:
            raise ValueError('the baseline share must leave items for the eligible images')
        if self.score_spec is not None:
            object.__setattr__(self, 'score_spec', validate_score_spec(self.score_spec))
        if self.low_confidence_band is not None:
            band = self.low_confidence_band
            if not isinstance(band, (tuple, list)) or len(band) != 2 or not all(_real(edge) for edge in band) or not band[0] < band[1]:
                raise ValueError('the low-confidence band must be two finite numbers with low < high')
            if self.score_spec is None:
                raise ValueError('a low-confidence band needs its score type')
            low, high = float(band[0]), float(band[1])
            if low < 0 or (self.score_spec['domain'] == 'probability' and high > 1):
                raise ValueError(f"the low-confidence band is outside the {self.score_spec['domain']} score's range")
            object.__setattr__(self, 'low_confidence_band', (low, high))
        if 'low_confidence' in reasons and (self.score_spec is None or self.low_confidence_band is None):
            raise ValueError('a low-confidence reason needs the score type and its band')
        # The reference is taken once, from canonical values (1 and 1.0 are one policy), so a later change to a
        # caller's dict cannot change it.
        body = json.dumps(self.to_json(), sort_keys=True, separators=(',', ':'))
        object.__setattr__(self, '_ref', 'intake-policy:' + hashlib.sha256(body.encode()).hexdigest()[:16] + f':r{self.revision}')

    @property
    def baseline_items(self) -> int:
        """The share of the item budget reserved for ordinary images (and the number of baseline slots per window)."""
        return math.ceil(self.max_items_per_window * self.normal_baseline_fraction) if self.normal_baseline_fraction else 0

    @property
    def baseline_key_items(self) -> int:
        """The places of each product / lot / camera quota eligible images leave to the baseline: one per baseline slot."""
        return min(self.baseline_items, self.per_product_lot_camera_quota - 1)

    @property
    def baseline_bytes(self) -> int:
        """The share of the byte budget eligible images leave to the baseline."""
        if not self.normal_baseline_fraction:
            return 0
        return min(math.floor(self.max_bytes_per_window * self.normal_baseline_fraction), self.max_bytes_per_window - 1)

    def geometry(self) -> dict:
        """What the store's usage rows are counted in: windows and baseline slots."""
        return {'window_seconds': self.window_seconds, 'window_offset_seconds': self.window_offset_seconds,
                'baseline_slots': self.baseline_items}

    def to_json(self) -> dict:
        return {'revision': self.revision, 'seed': self.seed, 'eligibility_reasons': list(self.eligibility_reasons),
                'window_seconds': self.window_seconds, 'window_offset_seconds': self.window_offset_seconds,
                'max_items_per_window': self.max_items_per_window, 'max_bytes_per_window': self.max_bytes_per_window,
                'per_product_lot_camera_quota': self.per_product_lot_camera_quota,
                'normal_baseline_fraction': self.normal_baseline_fraction,
                'score_spec': {**self.score_spec, 'threshold': float(self.score_spec['threshold'])} if self.score_spec else None,
                'low_confidence_band': list(self.low_confidence_band) if self.low_confidence_band else None}

    @property
    def ref(self) -> str:
        return self._ref

    def window_of(self, captured_at: float) -> int:
        return math.floor((captured_at - self.window_offset_seconds) / self.window_seconds)

    def slot_of(self, captured_at: float, window: int) -> Optional[int]:
        if not self.baseline_items:
            return None
        start = window * self.window_seconds + self.window_offset_seconds
        return min(self.baseline_items - 1, math.floor((captured_at - start) * self.baseline_items / self.window_seconds))


@dataclass(frozen=True)
class IntakeEvent:
    event_id: str                    # unique per store: one id is one image
    captured_at: float               # seconds (the window clock)
    size_bytes: int
    product: str
    lot: str
    camera: str
    run_ref: dict                    # run_id, node_id, recipe (version) the image came from
    verdict: Optional[str] = None    # OK, NG or REVIEW
    flags: tuple[str, ...] = ()      # operator_flag, model_disagreement
    score: Optional[dict] = None     # {'value': finite number, 'spec': score spec}

    def __post_init__(self):
        if not isinstance(self.event_id, str) or not self.event_id.strip() or len(self.event_id) > 200:
            raise ValueError('event_id is required (up to 200 characters)')
        if not _real(self.captured_at) or abs(self.captured_at) > MAX_SECONDS:
            raise ValueError('captured_at must be finite seconds within the store range')
        if type(self.size_bytes) is not int or self.size_bytes < 0:
            raise ValueError('size_bytes must be a whole number of bytes')
        if not all(isinstance(value, str) and value.strip() for value in (self.product, self.lot, self.camera)):
            raise ValueError('product, lot and camera are required')
        if (not isinstance(self.run_ref, dict) or not {'run_id', 'node_id', 'recipe'} <= set(self.run_ref)
                or not all(isinstance(self.run_ref[name], str) and self.run_ref[name].strip() for name in ('run_id', 'node_id', 'recipe'))):
            raise ValueError('run_ref must name the run, node and recipe')
        try:
            json.dumps(self.run_ref, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ValueError('run_ref must be plain JSON') from exc
        if self.verdict not in VERDICTS:
            raise ValueError('verdict must be OK, NG, REVIEW or absent')
        if not isinstance(self.flags, (tuple, list)) or any(flag not in FLAGS for flag in self.flags):
            raise ValueError(f"flags must be a list of {', '.join(FLAGS)}")
        object.__setattr__(self, 'flags', tuple(self.flags))
        if self.score is not None:
            if not isinstance(self.score, dict) or not _real(self.score.get('value')) or not isinstance(self.score.get('spec'), dict):
                raise ValueError('score must be a finite value with its score spec')

    @property
    def fingerprint(self) -> str:
        """The event's identity for a repeat, from canonical values: a resend that writes 1 for 1.0, repeats a flag or
        adds a key a score spec ignores is the same event."""
        score = None
        if self.score is not None:
            try:
                spec = validate_score_spec(self.score['spec'])
                spec = {**spec, 'threshold': float(spec['threshold'])}
            except (ValueError, KeyError, TypeError, AttributeError):
                spec = self.score['spec']
            score = {'value': float(self.score['value']), 'spec': spec}
        body = {'captured_at': float(self.captured_at), 'size_bytes': self.size_bytes, 'product': self.product, 'lot': self.lot,
                'camera': self.camera, 'run_ref': self.run_ref, 'verdict': self.verdict, 'flags': sorted(set(self.flags)),
                'score': score}
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


@dataclass
class SamplingReceipt:
    event_id: str
    decision: str            # selected or skipped
    reason: str
    policy_ref: str
    window: int
    run_ref: dict
    key: str
    size_bytes: int
    note: Optional[str] = None
    status: str = 'proposed_for_review'   # proposed_for_review, retention_failed or not_kept
    retention: Optional[str] = None       # pending, retained or failed (selected images only)
    failure: Optional[str] = None


SCHEMA = (
    'CREATE TABLE IF NOT EXISTS receipts(event_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, decision TEXT NOT NULL,'
    ' reason TEXT NOT NULL, policy_ref TEXT NOT NULL, window INTEGER NOT NULL, run_ref TEXT NOT NULL, key TEXT NOT NULL,'
    ' size_bytes INTEGER NOT NULL, baseline INTEGER NOT NULL, note TEXT, status TEXT NOT NULL, retention TEXT,'
    ' failure TEXT, decided_at REAL NOT NULL)',
    'CREATE INDEX IF NOT EXISTS receipts_window ON receipts(window, policy_ref)',
    # Per-window and per-key usage, kept with each decision so a decision never scans the window.
    'CREATE TABLE IF NOT EXISTS window_usage(window INTEGER PRIMARY KEY, items INTEGER NOT NULL, bytes INTEGER NOT NULL,'
    ' baseline INTEGER NOT NULL, baseline_bytes INTEGER NOT NULL)',
    'CREATE TABLE IF NOT EXISTS key_usage(window INTEGER NOT NULL, key TEXT NOT NULL, items INTEGER NOT NULL,'
    ' baseline INTEGER NOT NULL, PRIMARY KEY(window, key))',
    # The store's window geometry and the oldest window it still keeps (windows before it were pruned).
    'CREATE TABLE IF NOT EXISTS store_meta(name TEXT PRIMARY KEY, value TEXT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS baseline_slots(window INTEGER NOT NULL, slot INTEGER NOT NULL, event_id TEXT NOT NULL,'
    ' PRIMARY KEY(window, slot))',
)


def _fraction(seed: int, event_id: str) -> float:
    digest = hashlib.sha256(f'{seed}:{event_id}'.encode()).digest()
    return int.from_bytes(digest[:8], 'big') / 2 ** 64


class IntakeSampler:
    def __init__(self, path: Path | str, policy: IntakeSamplingPolicy, timeout: float = 10.0, clock=time.time):
        self.path = Path(path)
        self.policy = policy
        self.timeout = timeout
        self.clock = clock
        self._ready = False

    def _refuse_foreign(self, db: sqlite3.Connection) -> None:
        # One read statement: another opener may commit the owned schema between autocommit reads.
        # Classify one snapshot before WAL; the write transaction below still rechecks before creating tables.
        version, has_tables = db.execute(
            "SELECT user_version, EXISTS(SELECT 1 FROM sqlite_master WHERE type='table') FROM pragma_user_version"
        ).fetchone()
        if version not in (0, SCHEMA_VERSION) or (version == 0 and has_tables):
            raise ValueError(f'intake store {self.path.name} has another schema (version {version}); use a new store')

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=self.timeout, isolation_level=None)
        if not self._ready:
            try:
                self._refuse_foreign(db)  # before anything changes the file (a foreign file is left as it was)
            except BaseException:
                db.close()
                raise
        use_wal(db, self.timeout)
        if not self._ready:
            # One write transaction: a second process opening a new store waits and then sees the finished schema.
            db.execute('BEGIN IMMEDIATE')
            try:
                self._refuse_foreign(db)
                for statement in SCHEMA:
                    db.execute(statement)
                db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
                geometry = json.dumps(self.policy.geometry(), sort_keys=True)
                stored = db.execute("SELECT value FROM store_meta WHERE name='geometry'").fetchone()
                if stored is None:
                    db.execute("INSERT INTO store_meta VALUES('geometry', ?)", (geometry,))
                elif stored[0] != geometry:
                    raise ValueError(f'intake store {self.path.name} counts windows and baseline slots as {stored[0]}; a policy '
                                     f'revision cannot change them ({geometry}); use a new store')
                db.execute('COMMIT')
            except BaseException:
                db.execute('ROLLBACK')
                db.close()
                raise
            self._ready = True
        return db

    @staticmethod
    def _pruned_before(db: sqlite3.Connection) -> Optional[int]:
        row = db.execute("SELECT value FROM store_meta WHERE name='pruned_before'").fetchone()
        return int(row[0]) if row else None

    def _signals(self, event: IntakeEvent) -> tuple[list[str], Optional[str], bool]:
        """The declared reasons the event carries, a note, and whether it is ordinary (carries no sign at all)."""
        reasons, note, unknown_score, low = [], None, False, False
        wanted = set(self.policy.eligibility_reasons)
        if event.verdict == 'NG' and 'ng_verdict' in wanted:
            reasons.append('ng_verdict')
        if event.verdict == 'REVIEW' and 'review_verdict' in wanted:
            reasons.append('review_verdict')
        reasons += [flag for flag in FLAGS if flag in event.flags and flag in wanted]
        if event.score is not None and self.policy.score_spec is not None:
            try:
                spec = validate_score_spec(event.score.get('spec'))
            except (ValueError, AttributeError):
                spec = None
            if spec is None or not compatible_scores(spec, self.policy.score_spec):
                note, unknown_score = 'score of another type: not compared with the low-confidence band', True
            elif self.policy.low_confidence_band is not None:
                band_low, band_high = self.policy.low_confidence_band
                low = band_low <= float(event.score['value']) <= band_high
                if low and 'low_confidence' in wanted:
                    reasons.append('low_confidence')
        elif event.score is not None:
            unknown_score = 'low_confidence' in wanted
        ordinary = event.verdict in (None, 'OK') and not event.flags and not unknown_score and not low
        if not reasons and not ordinary and note is None:
            note = 'not ordinary: it carries a sign the policy does not keep (not a baseline image)'
        return reasons, note, ordinary

    def decide(self, event: IntakeEvent) -> SamplingReceipt:
        policy = self.policy
        window = policy.window_of(event.captured_at)
        key = json.dumps([event.product, event.lot, event.camera], ensure_ascii=False)
        fingerprint = event.fingerprint
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                row = db.execute('SELECT * FROM receipts WHERE event_id=?', (event.event_id,)).fetchone()
                if row is not None:
                    db.execute('COMMIT')
                    if row[1] != fingerprint:
                        raise EventIdReused(f'event_id {event.event_id!r} was already decided for another image; event ids must be '
                                            'unique for the life of the store')
                    return self._receipt(row)
                pruned = self._pruned_before(db)
                if pruned is not None and window < pruned:
                    db.execute('COMMIT')
                    return SamplingReceipt(event.event_id, 'skipped', 'window_pruned', policy.ref, window, event.run_ref, key,
                                           event.size_bytes, f'window {window} was pruned (the store keeps windows from {pruned}); nothing was spent',
                                           'not_kept', None)
                reasons, note, ordinary = self._signals(event)
                slot = policy.slot_of(event.captured_at, window)
                baseline = (not reasons and ordinary and policy.baseline_items > 0
                            and _fraction(policy.seed, event.event_id) < policy.normal_baseline_fraction)
                if reasons or baseline:
                    used = db.execute('SELECT items, bytes, baseline, baseline_bytes FROM window_usage WHERE window=?',
                                      (window,)).fetchone() or (0, 0, 0, 0)
                    items, size, baseline_used, baseline_size = used
                    key_items, key_baseline = db.execute('SELECT items, baseline FROM key_usage WHERE window=? AND key=?',
                                                         (window, key)).fetchone() or (0, 0)
                    binding = []
                    if event.size_bytes > policy.max_bytes_per_window:
                        binding = None
                    elif baseline:
                        # The whole budgets bind, and the baseline's reserved key and byte shares keep their pace (by slot s,
                        # (s + 1) / slots of each), so an early slot never spends what a later one needs.
                        pace = (slot + 1) / policy.baseline_items
                        byte_pace = policy.baseline_bytes * pace if policy.baseline_bytes else policy.max_bytes_per_window
                        if size + event.size_bytes > policy.max_bytes_per_window or baseline_size + event.size_bytes > byte_pace:
                            binding.append('bytes')
                        if (key_items >= policy.per_product_lot_camera_quota
                                or (policy.baseline_key_items and key_baseline >= math.ceil(policy.baseline_key_items * pace))):
                            binding.append('product_lot_camera')
                        # A revision may reduce the total item cap while retaining the same slot geometry.
                        if items >= policy.max_items_per_window:
                            binding.append('items')
                        if baseline_used >= policy.baseline_items:
                            binding.append('baseline')
                        elif db.execute('SELECT 1 FROM baseline_slots WHERE window=? AND slot=?', (window, slot)).fetchone():
                            binding.append('baseline_slot')
                    else:
                        # Eligible images spend only what the baseline's shares leave, and never exceed the
                        # current whole caps when an earlier revision's baseline used larger reserved shares.
                        if (size + event.size_bytes > policy.max_bytes_per_window
                                or (size - baseline_size) + event.size_bytes > policy.max_bytes_per_window - policy.baseline_bytes):
                            binding.append('bytes')
                        if (key_items >= policy.per_product_lot_camera_quota
                                or key_items - key_baseline >= policy.per_product_lot_camera_quota - policy.baseline_key_items):
                            binding.append('product_lot_camera')
                        if items - baseline_used >= policy.max_items_per_window - policy.baseline_items:
                            binding.append('items')
                    if binding is None:
                        decision, reason = 'skipped', 'too_large'
                    elif binding:
                        decision, reason = 'skipped', 'quota_exhausted:' + '+'.join(binding)
                    else:
                        decision, reason = 'selected', 'baseline' if baseline else '+'.join(reasons)
                else:
                    decision, reason = 'skipped', 'not_eligible'
                selected = decision == 'selected'
                status = 'proposed_for_review' if selected else 'not_kept'
                db.execute('INSERT INTO receipts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (event.event_id, fingerprint, decision, reason, policy.ref, window,
                            json.dumps(event.run_ref, sort_keys=True), key, event.size_bytes, int(baseline and selected), note,
                            status, 'pending' if selected else None, None, float(self.clock())))
                if selected:
                    db.execute('INSERT INTO window_usage VALUES(?,1,?,?,?) ON CONFLICT(window) DO UPDATE SET items=items+1,'
                               ' bytes=bytes+excluded.bytes, baseline=baseline+excluded.baseline,'
                               ' baseline_bytes=baseline_bytes+excluded.baseline_bytes',
                               (window, event.size_bytes, int(baseline), event.size_bytes if baseline else 0))
                    db.execute('INSERT INTO key_usage VALUES(?,?,1,?) ON CONFLICT(window, key) DO UPDATE SET items=items+1,'
                               ' baseline=baseline+excluded.baseline', (window, key, int(baseline)))
                    if baseline:
                        db.execute('INSERT INTO baseline_slots VALUES(?,?,?)', (window, slot, event.event_id))
                db.execute('COMMIT')
            except EventIdReused:
                raise
            except BaseException:
                try:
                    db.execute('ROLLBACK')
                except sqlite3.Error:
                    pass
                raise
        return SamplingReceipt(event.event_id, decision, reason, policy.ref, window, event.run_ref, key, event.size_bytes, note,
                               status, 'pending' if selected else None)

    @staticmethod
    def _receipt(row) -> SamplingReceipt:
        return SamplingReceipt(row[0], row[2], row[3], row[4], row[5], json.loads(row[6]), row[7], row[8], row[10], row[11],
                               row[12], row[13])

    def _retention(self, event_id: str, retention: str, status: str, failure: Optional[str]) -> None:
        with closing(self._connect()) as db:
            changed = db.execute("UPDATE receipts SET retention=?, status=?, failure=? WHERE event_id=? AND decision='selected'",
                                 (retention, status, failure, event_id)).rowcount
        if not changed:
            raise ValueError('no selected receipt for that event')

    def mark_retained(self, event_id: str) -> None:
        """The selected image was stored for review."""
        self._retention(event_id, 'retained', 'proposed_for_review', None)

    def mark_retention_failed(self, event_id: str, detail: str) -> None:
        """A selected image that could not be stored: the receipt keeps its note and records the failure on its own."""
        self._retention(event_id, 'failed', 'retention_failed', str(detail)[:500])

    def status(self, window: int, *, grace_seconds: float = RETENTION_GRACE_SECONDS, now: Optional[float] = None) -> dict:
        """The window's use of its budget across every policy revision that decided in it, with a breakdown per revision."""
        policy = self.policy
        moment = self.clock() if now is None else now
        with closing(self._connect()) as db:
            rows = db.execute('SELECT policy_ref, decision, reason, size_bytes, baseline, retention, decided_at FROM receipts'
                              ' WHERE window=?', (window,)).fetchall()
        selected = [row for row in rows if row[1] == 'selected']
        exhausted = sorted({part for row in rows if row[2].startswith('quota_exhausted:') for part in row[2].split(':', 1)[1].split('+')})
        by_ref: dict = {}
        for row in rows:
            entry = by_ref.setdefault(row[0], {'selected': 0, 'skipped': 0, 'bytes': 0})
            if row[1] == 'selected':
                entry['selected'] += 1
                entry['bytes'] += row[3]
            else:
                entry['skipped'] += 1
        return {'policy_ref': policy.ref, 'window': window,
                'window_starts_at': window * policy.window_seconds + policy.window_offset_seconds,
                'selected': len(selected), 'baseline': sum(row[4] for row in selected), 'bytes': sum(row[3] for row in selected),
                'skipped': len(rows) - len(selected), 'quota_exhausted': exhausted,
                'too_large': sum(row[2] == 'too_large' for row in rows),
                'retained': sum(row[5] == 'retained' for row in selected),
                'retention_failed': sum(row[5] == 'failed' for row in selected),
                'retention_pending': sum(row[5] == 'pending' for row in selected),
                'retention_overdue': sum(row[5] == 'pending' and moment - row[6] > grace_seconds for row in selected),
                'by_policy': by_ref,
                'budget': {'items': policy.max_items_per_window, 'bytes': policy.max_bytes_per_window, 'baseline_items': policy.baseline_items}}

    def prune(self, before_window: int) -> int:
        """Remove the receipts and usage of every window before ``before_window``, one window per transaction (decisions
        are not held up by a long delete), and remember that they were pruned. Refused, before anything is removed, while
        one of those windows has an image pending retention. Returns the receipts removed."""
        if type(before_window) is not int:
            raise ValueError('before_window is a window number')
        with closing(self._connect()) as db:
            pending = db.execute("SELECT window, COUNT(*) FROM receipts WHERE window<? AND retention='pending' GROUP BY window"
                                 ' ORDER BY window', (before_window,)).fetchall()
            if pending:
                listed = ', '.join(f'window {window}: {count}' for window, count in pending[:10])
                raise ValueError(f'images are still pending retention ({listed}); mark them retained or failed before pruning')
            windows = [row[0] for row in db.execute(
                'SELECT window FROM window_usage WHERE window<? UNION SELECT DISTINCT window FROM receipts WHERE window<? ORDER BY 1',
                (before_window, before_window))]
            removed = 0
            for window in [*windows, None]:
                db.execute('BEGIN IMMEDIATE')
                try:
                    if window is not None:
                        if db.execute("SELECT 1 FROM receipts WHERE window=? AND retention='pending'", (window,)).fetchone():
                            raise ValueError(f'window {window} got an image pending retention while pruning; prune it later')
                        removed += db.execute('DELETE FROM receipts WHERE window=?', (window,)).rowcount
                        for table in ('window_usage', 'key_usage', 'baseline_slots'):
                            db.execute(f'DELETE FROM {table} WHERE window=?', (window,))
                    horizon = before_window if window is None else window + 1
                    current = self._pruned_before(db)
                    if current is None or horizon > current:
                        db.execute("INSERT INTO store_meta VALUES('pruned_before', ?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                                   (str(horizon),))
                    db.execute('COMMIT')
                except BaseException:
                    db.execute('ROLLBACK')
                    raise
        return removed


__all__ = ['EventIdReused', 'IntakeEvent', 'IntakeSampler', 'IntakeSamplingPolicy', 'REASONS', 'SamplingReceipt']
