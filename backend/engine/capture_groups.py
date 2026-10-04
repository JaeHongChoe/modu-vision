"""One part seen by several views (E01): frames of the same part and trigger are joined into one capture group.

Each view is judged on its own (any one-image flow stays usable as the view's subflow); the group gives one verdict for
the whole part.

- The join policy names the required views, the basis of every frame's capture time, the allowed skew, the deadline and
  the late window. ``shared_clock``: capture times are milliseconds on one clock shared by every view (the host's, or
  camera clocks synchronized with each other), and the captures that join may span at most ``max_skew_ms``.
  ``trigger_offset``: capture times are milliseconds after the part's trigger, and a capture joins within
  ``max_skew_ms`` of it.
- (part_id, trigger_id) names one part's capture for the lifetime of the store. A trigger that fires twice for one part
  under two trigger ids gives two groups; debouncing it belongs to the trigger source (S5-03).
- A group is OPEN from its first frame (of any view) until every required view joined (COMPLETE) or its deadline
  passed. A group that reaches its deadline with a view missing closes as INCOMPLETE when a missing view did deliver a
  frame outside the skew, and as EXPIRED when it never delivered one; both are REVIEW. A closed group's verdict never
  changes: it was already given.
- Every frame is recorded with its disposition: accepted (joined), duplicate (the same frame again, no effect),
  conflict (another frame of a view that already joined), out_of_skew, unknown_view (not a required view), late (after
  the group closed) or refused (more than ``late_window_ms`` after it closed, or after a clock change since it closed: a
  straggler past any use, or another part reusing the ids; CaptureKeyReused is raised, also for every retry of that
  frame). Before the group closes, a conflict, an out-of-skew frame or an unknown view keeps it from OK: it is REVIEW
  (NG stays NG), and the reason names those frames. A conflict recorded after the group closed (``after_close``) cannot
  change the verdict already given; it is returned as an alarm on the group, for the runtime to raise.
- Times are the system's monotonic clock in milliseconds, read under the store's write lock, so the writes of every
  process of one boot see it in order; wall clock steps do not move it. Every write also records the boot's wall-clock
  epoch (wall minus monotonic time). When the monotonic clock is below the last write's (another boot or computer), or
  the epoch moved more than ``EPOCH_TOLERANCE_MS`` plus ``EPOCH_DRIFT_PPM`` of the monotonic time since the last write
  (a clock step or time correction, or a sleep the monotonic clock did not count), the time that passed for pending
  groups is unknown: each is marked due (deadline -1) with that cause, and the next sweep closes it (REVIEW) and reports
  it, instead of guessing how much time passed. A shorter uncounted sleep is not detected, so a frame can join up to
  that much after its deadline. One store belongs to one computer.

Groups, frames, deadlines and the policy are stored in SQLite (WAL; every write under BEGIN IMMEDIATE, every read in one
snapshot), so a restart of the app keeps pending groups with their deadlines, and a store reopened under another policy
or schema is refused. Closed groups are kept (retention belongs to the runtime that owns the store).
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Literal, Optional

from backend.engine.sqlite_wal import use_wal

VERDICTS = ('OK', 'NG', 'REVIEW')
TIMESTAMP_BASES = ('shared_clock', 'trigger_offset')
MAX_WINDOW_MS = 86_400_000
SCHEMA_VERSION = '3'
EPOCH_TOLERANCE_MS = 2_000    # the boot epoch read twice differs by scheduling noise and clock resolution
EPOCH_DRIFT_PPM = 1_000       # plus the wall clock's rate correction (time sync) since the last write
CLOCK_CHANGED = ('the clock changed while this part was pending (a restart or sleep of the computer, another computer or '
                 'a clock step), so its timing is unknown')
GroupState = Literal['OPEN', 'COMPLETE', 'EXPIRED', 'INCOMPLETE']
Disposition = Literal['accepted', 'duplicate', 'conflict', 'out_of_skew', 'unknown_view', 'late', 'refused']
_UNJOINED = ('conflict', 'out_of_skew', 'unknown_view')
_NOTE_FRAMES = 8


class CaptureKeyReused(Exception):
    """A frame for a part and trigger whose group closed more than the late window ago: a straggler past any use, or
    another part reusing the ids. The frame is recorded. The part keeps the verdict it was given; the caller raises an
    alarm and never judges that part a second time."""


@dataclass(frozen=True)
class CapturePolicy:
    """How the views of one part are joined (see the module notes)."""
    required_view_ids: tuple[str, ...]
    timestamp_basis: Literal['shared_clock', 'trigger_offset']
    max_skew_ms: int
    deadline_ms: int
    late_window_ms: Optional[int] = None   # after a group closed; the deadline when not given
    completeness_policy: Literal['all_required'] = 'all_required'

    def __post_init__(self):
        if not isinstance(self.required_view_ids, (tuple, list)):
            raise ValueError('required_view_ids must be a list of view names')
        views = tuple(self.required_view_ids)
        if not views or len(set(views)) != len(views) or not all(isinstance(view, str) and view.strip() for view in views):
            raise ValueError('required_view_ids must be distinct, non-empty view names')
        if self.timestamp_basis not in TIMESTAMP_BASES:
            raise ValueError(f"timestamp_basis must be one of {', '.join(TIMESTAMP_BASES)}")
        late = self.deadline_ms if self.late_window_ms is None else self.late_window_ms
        for name, value, lowest in (('max_skew_ms', self.max_skew_ms, 0), ('deadline_ms', self.deadline_ms, 1), ('late_window_ms', late, 0)):
            if type(value) is not int or not lowest <= value <= MAX_WINDOW_MS:
                raise ValueError(f'{name} must be a whole number of milliseconds from {lowest} to {MAX_WINDOW_MS}')
        if self.completeness_policy != 'all_required':
            raise ValueError('completeness_policy must be all_required')
        object.__setattr__(self, 'required_view_ids', views)
        object.__setattr__(self, 'late_window_ms', late)

    def canonical(self) -> dict:
        """The policy as stored; the order of the views does not matter."""
        return {'version': 2, 'required_view_ids': sorted(self.required_view_ids), 'timestamp_basis': self.timestamp_basis,
                'max_skew_ms': self.max_skew_ms, 'deadline_ms': self.deadline_ms, 'late_window_ms': self.late_window_ms,
                'completeness_policy': self.completeness_policy}


@dataclass
class GroupResult:
    part_id: str
    trigger_id: str
    state: GroupState
    verdict: Optional[str]
    missing_view_ids: list[str] = field(default_factory=list)
    frame_refs: dict[str, str] = field(default_factory=dict)  # the frame that joined for each view
    disposition: Optional[Disposition] = None                  # the added frame's (add_frame only)
    reason: Optional[str] = None
    alarms: list[str] = field(default_factory=list)            # conflicts that arrived after the verdict was given


@dataclass(frozen=True)
class _Frame:
    view_id: str
    frame_ref: str
    captured_at_ms: int
    verdict: Optional[str]
    disposition: str
    after_close: int


def joined_verdict(view_verdicts: list[Optional[str]]) -> str:
    """The whole part's verdict from its views: NG if any view is NG, REVIEW if any view is REVIEW or has no verdict,
    otherwise OK."""
    if any(verdict == 'NG' for verdict in view_verdicts):
        return 'NG'
    if any(verdict not in ('OK', 'NG') for verdict in view_verdicts):
        return 'REVIEW'
    return 'OK'


SCHEMA = (
    'CREATE TABLE IF NOT EXISTS meta(name TEXT PRIMARY KEY, value TEXT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS groups(part_id TEXT NOT NULL, trigger_id TEXT NOT NULL, state TEXT NOT NULL,'
    ' opened_at_ms INTEGER NOT NULL, deadline_at_ms INTEGER NOT NULL, verdict TEXT, reason TEXT, closed_at_ms INTEGER,'
    ' closed_era INTEGER, PRIMARY KEY(part_id, trigger_id))',
    'CREATE INDEX IF NOT EXISTS groups_due ON groups(state, deadline_at_ms)',
    'CREATE TABLE IF NOT EXISTS frames(seq INTEGER PRIMARY KEY AUTOINCREMENT, part_id TEXT NOT NULL, trigger_id TEXT NOT NULL,'
    ' view_id TEXT NOT NULL, frame_ref TEXT NOT NULL, captured_at_ms INTEGER NOT NULL, verdict TEXT,'
    ' received_at_ms INTEGER NOT NULL, received_wall_ms INTEGER NOT NULL, disposition TEXT NOT NULL,'
    ' after_close INTEGER NOT NULL)',
    'CREATE INDEX IF NOT EXISTS frames_group ON frames(part_id, trigger_id)',
)


def _ms(clock_ns: Callable[[], int]) -> Callable[[], int]:
    return lambda: clock_ns() // 1_000_000


class CaptureGroups:
    def __init__(self, path: Path | str, policy: CapturePolicy, *, wall_ms: Optional[Callable[[], int]] = None,
                 monotonic_ms: Optional[Callable[[], int]] = None, timeout: float = 10.0):
        self.path = Path(path)
        self.policy = policy
        self.timeout = timeout
        self._monotonic = monotonic_ms or _ms(time.monotonic_ns)
        self._wall = wall_ms or _ms(time.time_ns)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as db:
            for statement in SCHEMA:
                db.execute(statement)
            meta = dict(db.execute('SELECT name, value FROM meta').fetchall())
            if 'policy' in meta and meta.get('schema_version') != SCHEMA_VERSION:
                raise ValueError(f"{self.path} was written by another version of the capture group store "
                                 f"(schema {meta.get('schema_version', '1')}, this one reads {SCHEMA_VERSION})")
            wanted = json.dumps(policy.canonical(), sort_keys=True)
            if 'policy' not in meta:
                now = self._monotonic()
                epoch = self._wall() - now
                db.executemany('INSERT INTO meta(name, value) VALUES(?, ?)',
                               [('schema_version', SCHEMA_VERSION), ('policy', wanted), ('clock_floor_ms', str(now)), ('clock_epoch_ms', str(epoch)),
                                ('clock_era', '0')])
                return
            if json.loads(meta['policy']) != json.loads(wanted):
                # The groups already stored were joined under that policy; another one would judge them differently.
                raise ValueError(f"{self.path} keeps its capture groups under another join policy: {meta['policy']}")
            self._clock(db)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=self.timeout, isolation_level=None)
        try:
            use_wal(db, self.timeout)
            yield db
        finally:
            db.close()

    @contextmanager
    def _transaction(self, begin: str = 'BEGIN IMMEDIATE') -> Iterator[sqlite3.Connection]:
        with self._connect() as db:
            db.execute(begin)
            try:
                yield db
            except BaseException:
                try:
                    db.execute('ROLLBACK')
                except sqlite3.Error:
                    pass  # the original error is the one to report
                raise
            db.execute('COMMIT')

    def _snapshot(self):
        """A read of several statements that sees one state of the store."""
        return self._transaction('BEGIN')

    def _clock(self, db) -> tuple[int, int, int]:
        """Read the clocks and check them against the store's last write; returns the monotonic and wall milliseconds
        and the clock era (counted up at every change: times of two eras cannot be compared).

        Called first in every write, under the write lock (BEGIN IMMEDIATE): every value stored by another process of
        this boot was read earlier on the same clock, so a monotonic time below it is another boot or computer. When the
        time that passed for pending groups is unknown (that, or the epoch moved: a clock step, or a sleep the monotonic
        clock did not count), each is marked due with the cause, for the next sweep to close and report."""
        now = self._monotonic()
        wall = self._wall()
        meta = dict(db.execute("SELECT name, value FROM meta WHERE name IN ('clock_floor_ms', 'clock_epoch_ms', 'clock_era')").fetchall())
        floor, stored, era = int(meta['clock_floor_ms']), int(meta['clock_epoch_ms']), int(meta['clock_era'])
        epoch = wall - now
        if now < floor or abs(epoch - stored) > EPOCH_TOLERANCE_MS + (now - floor) * EPOCH_DRIFT_PPM // 1_000_000:
            db.execute("UPDATE groups SET deadline_at_ms=-1, reason=? WHERE state='OPEN'", (CLOCK_CHANGED,))
            era += 1
            db.execute("UPDATE meta SET value=? WHERE name='clock_era'", (str(era),))
        # Re-anchored on every write, so a slow rate correction never adds up to a change.
        db.execute("UPDATE meta SET value=? WHERE name='clock_floor_ms'", (str(now),))
        db.execute("UPDATE meta SET value=? WHERE name='clock_epoch_ms'", (str(epoch),))
        return now, wall, era

    @staticmethod
    def _frames(db, part_id, trigger_id) -> list[_Frame]:
        rows = db.execute('SELECT view_id, frame_ref, captured_at_ms, verdict, disposition, after_close FROM frames'
                          ' WHERE part_id=? AND trigger_id=? ORDER BY seq', (part_id, trigger_id)).fetchall()
        return [_Frame(*row) for row in rows]

    @staticmethod
    def _joined(frames: list[_Frame]) -> dict[str, _Frame]:
        return {frame.view_id: frame for frame in frames if frame.disposition == 'accepted'}

    @staticmethod
    def _describe(frames: list[_Frame]) -> str:
        shown = ', '.join(f"{frame.view_id} {frame.frame_ref} ({frame.disposition}{', ' + frame.verdict if frame.verdict else ''})"
                          for frame in frames[:_NOTE_FRAMES])
        return shown + (f' and {len(frames) - _NOTE_FRAMES} more' if len(frames) > _NOTE_FRAMES else '')

    def _unjoined_note(self, frames: list[_Frame]) -> Optional[str]:
        odd = [frame for frame in frames if frame.disposition in _UNJOINED]
        return 'frames that did not join: ' + self._describe(odd) if odd else None

    def _in_skew(self, captured_at_ms: int, joined: dict[str, _Frame]) -> bool:
        if self.policy.timestamp_basis == 'trigger_offset':
            return abs(captured_at_ms) <= self.policy.max_skew_ms
        times = [frame.captured_at_ms for frame in joined.values()] + [captured_at_ms]
        return max(times) - min(times) <= self.policy.max_skew_ms

    def _result(self, db, part_id, trigger_id, disposition=None) -> Optional[GroupResult]:
        row = db.execute('SELECT state, verdict, reason FROM groups WHERE part_id=? AND trigger_id=?',
                         (part_id, trigger_id)).fetchone()
        if row is None:
            return None
        frames = self._frames(db, part_id, trigger_id)
        joined = self._joined(frames)
        missing = [view for view in self.policy.required_view_ids if view not in joined]
        refs = {view: joined[view].frame_ref for view in self.policy.required_view_ids if view in joined}
        after = [frame for frame in frames if frame.after_close and frame.disposition == 'conflict']
        alarms = [f'after the verdict {row[1]} was given, another frame of a view that had joined arrived: {self._describe(after)}'] if after else []
        return GroupResult(part_id, trigger_id, row[0], row[1], missing, refs, disposition, row[2], alarms)

    def _close(self, db, part_id, trigger_id, now, era) -> None:
        # An open group's reason is the cause it was marked due for (the clock changed), if any.
        cause = db.execute('SELECT reason FROM groups WHERE part_id=? AND trigger_id=?', (part_id, trigger_id)).fetchone()[0]
        frames = self._frames(db, part_id, trigger_id)
        joined = self._joined(frames)
        missing = [view for view in self.policy.required_view_ids if view not in joined]
        skewed = [view for view in missing if any(frame.view_id == view and frame.disposition == 'out_of_skew' for frame in frames)]
        reason = '; '.join(filter(None, [cause, f"missing views: {', '.join(missing)}" if missing else None, self._unjoined_note(frames)]))
        db.execute('UPDATE groups SET state=?, verdict=?, reason=?, closed_at_ms=?, closed_era=? WHERE part_id=? AND trigger_id=?',
                   ('INCOMPLETE' if skewed else 'EXPIRED', 'REVIEW', reason, now, era, part_id, trigger_id))

    def _close_if_due(self, db, part_id, trigger_id, now, era) -> None:
        row = db.execute("SELECT deadline_at_ms FROM groups WHERE part_id=? AND trigger_id=? AND state='OPEN'", (part_id, trigger_id)).fetchone()
        if row is not None and now > row[0]:
            self._close(db, part_id, trigger_id, now, era)

    def _disposition(self, frames, state, closed_since, view_id, frame_ref, captured_at_ms, verdict) -> Disposition:
        same = [frame for frame in frames if (frame.view_id, frame.frame_ref, frame.captured_at_ms, frame.verdict) == (view_id, frame_ref, captured_at_ms, verdict)]
        if same:
            # A retry of a refused frame is refused again; any other retry has no effect.
            return 'refused' if any(frame.disposition == 'refused' for frame in same) else 'duplicate'
        joined = self._joined(frames)
        if state != 'OPEN':
            if closed_since is None or closed_since > self.policy.late_window_ms:
                return 'refused'  # past the late window, or closed before the clock changed (the time since is unknown)
            return 'conflict' if view_id in joined else 'late'
        if view_id not in self.policy.required_view_ids:
            return 'unknown_view'
        if view_id in joined:
            return 'conflict'
        return 'accepted' if self._in_skew(captured_at_ms, joined) else 'out_of_skew'

    def reserve(self,part_id: str,trigger_id: str) -> GroupResult:
        """Start the durable deadline at admission, before view inference returns."""
        if not all(isinstance(value,str) and value.strip() for value in (part_id,trigger_id)):
            raise ValueError('part_id and trigger_id are required')
        with self._transaction() as db:
            now,_,era=self._clock(db)
            db.execute("INSERT OR IGNORE INTO groups(part_id,trigger_id,state,opened_at_ms,deadline_at_ms) VALUES(?,?,'OPEN',?,?)",
                       (part_id,trigger_id,now,now+self.policy.deadline_ms))
            self._close_if_due(db,part_id,trigger_id,now,era)
            return self._result(db,part_id,trigger_id)

    def add_frame(self, part_id: str, trigger_id: str, view_id: str, frame_ref: str, captured_at_ms: int,
                  verdict: Optional[str]) -> GroupResult:
        """Record one view's frame and its own verdict; returns the group after it, with this frame's disposition.
        Arrival is the moment of this call."""
        if not all(isinstance(value, str) and value for value in (part_id, trigger_id, view_id, frame_ref)):
            raise ValueError('part_id, trigger_id, view_id and frame_ref are required')
        if type(captured_at_ms) is not int:
            raise ValueError(f'captured_at_ms must be whole milliseconds ({self.policy.timestamp_basis}), not {captured_at_ms!r}')
        if verdict is not None and verdict not in VERDICTS:
            raise ValueError(f'a view verdict is OK, NG, REVIEW or none, not {verdict!r}')
        with self._transaction() as db:
            now, wall, era = self._clock(db)
            if db.execute('SELECT 1 FROM groups WHERE part_id=? AND trigger_id=?', (part_id, trigger_id)).fetchone() is None:
                db.execute("INSERT INTO groups(part_id, trigger_id, state, opened_at_ms, deadline_at_ms) VALUES(?,?,'OPEN',?,?)",
                           (part_id, trigger_id, now, now + self.policy.deadline_ms))
            self._close_if_due(db, part_id, trigger_id, now, era)
            state, closed_at_ms, closed_era = db.execute('SELECT state, closed_at_ms, closed_era FROM groups WHERE part_id=? AND trigger_id=?',
                                                         (part_id, trigger_id)).fetchone()
            closed_since = now - closed_at_ms if state != 'OPEN' and closed_era == era else None
            disposition = self._disposition(self._frames(db, part_id, trigger_id), state, closed_since,
                                            view_id, frame_ref, captured_at_ms, verdict)
            db.execute('INSERT INTO frames(part_id, trigger_id, view_id, frame_ref, captured_at_ms, verdict, received_at_ms, received_wall_ms,'
                       ' disposition, after_close) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (part_id, trigger_id, view_id, frame_ref, captured_at_ms, verdict, now, wall, disposition, int(state != 'OPEN')))
            if disposition == 'accepted':
                frames = self._frames(db, part_id, trigger_id)
                joined = self._joined(frames)
                if all(view in joined for view in self.policy.required_view_ids):
                    whole = joined_verdict([joined[view].verdict for view in self.policy.required_view_ids])
                    note = self._unjoined_note(frames)
                    if note and whole == 'OK':
                        whole = 'REVIEW'
                    db.execute("UPDATE groups SET state='COMPLETE', verdict=?, reason=?, closed_at_ms=?, closed_era=? WHERE part_id=? AND trigger_id=?",
                               (whole, note, now, era, part_id, trigger_id))
            result = self._result(db, part_id, trigger_id, disposition)
        if disposition == 'refused':
            when = (f'{closed_since} ms after' if closed_since is not None else 'after a clock change since')
            raise CaptureKeyReused(f'a frame for part {part_id} trigger {trigger_id} arrived {when} the part was judged {result.state} '
                                   f'{result.verdict}, past the {self.policy.late_window_ms} ms late window: a straggler or another part '
                                   'reusing the ids. The part keeps its verdict.')
        return result

    def expire_due(self) -> list[GroupResult]:
        """Close every open group whose deadline has passed, or that was marked due because the clock changed (EXPIRED,
        or INCOMPLETE after a skewed frame); returns them."""
        with self._transaction() as db:
            now, _, era = self._clock(db)
            due = db.execute("SELECT part_id, trigger_id FROM groups WHERE state='OPEN' AND deadline_at_ms < ? ORDER BY deadline_at_ms",
                             (now,)).fetchall()
            for part_id, trigger_id in due:
                self._close_if_due(db, part_id, trigger_id, now, era)
            return [self._result(db, part_id, trigger_id) for part_id, trigger_id in due]

    def group(self, part_id: str, trigger_id: str) -> Optional[GroupResult]:
        with self._snapshot() as db:
            return self._result(db, part_id, trigger_id)

    def pending(self) -> list[dict]:
        """Open groups with their deadlines (kept across a restart of the app; -1 when marked due)."""
        with self._snapshot() as db:
            rows = db.execute("SELECT part_id, trigger_id, deadline_at_ms FROM groups WHERE state='OPEN' ORDER BY deadline_at_ms").fetchall()
        return [{'part_id': part, 'trigger_id': trigger, 'deadline_at_ms': deadline} for part, trigger, deadline in rows]

    def frames(self, part_id: str, trigger_id: str) -> list[dict]:
        with self._snapshot() as db:
            return [{'view_id': frame.view_id, 'frame_ref': frame.frame_ref, 'captured_at_ms': frame.captured_at_ms,
                     'verdict': frame.verdict, 'disposition': frame.disposition, 'after_close': bool(frame.after_close)}
                    for frame in self._frames(db, part_id, trigger_id)]
