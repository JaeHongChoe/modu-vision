"""E01: one part seen by several views joins into one capture group and one whole-part verdict.

A simulator feeds frames out of order and interleaved between parts; duplicates, conflicting, unknown, skewed, late and
refused frames, clock steps, two processes, a restart and a policy change are checked against the group store itself.
"""
import itertools
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import closing
from pathlib import Path

import pytest

from backend.engine.capture_groups import CaptureGroups, CaptureKeyReused, CapturePolicy, joined_verdict

POLICY = CapturePolicy(required_view_ids=('top', 'side'), timestamp_basis='shared_clock', max_skew_ms=200, deadline_ms=1_000)


class Clock:
    def __init__(self, start=1_000_000):
        self.now = start

    def __call__(self):
        return self.now


def store(tmp_path, clock, policy=POLICY, wall=None):
    return CaptureGroups(tmp_path / 'groups.sqlite3', policy, wall_ms=wall or clock, monotonic_ms=clock)


def outcome(result):
    return result.state, result.verdict


def test_reordered_views_of_two_interleaved_parts_join_without_mixing(tmp_path):
    clock = Clock()
    groups = store(tmp_path, clock)
    # Part A's side view arrives before its top view; part B's views arrive in between.
    assert groups.add_frame('A', 't1', 'side', 'a-side.png', 1_000, 'OK').state == 'OPEN'
    assert groups.add_frame('B', 't2', 'top', 'b-top.png', 1_010, 'NG').missing_view_ids == ['side']
    done_a = groups.add_frame('A', 't1', 'top', 'a-top.png', 1_020, 'OK')
    done_b = groups.add_frame('B', 't2', 'side', 'b-side.png', 1_030, 'OK')
    assert outcome(done_a) == ('COMPLETE', 'OK') and done_a.frame_refs == {'top': 'a-top.png', 'side': 'a-side.png'}
    assert outcome(done_b) == ('COMPLETE', 'NG'), 'one NG view makes the whole part NG'
    assert [row['frame_ref'] for row in groups.frames('A', 't1')] == ['a-side.png', 'a-top.png']
    assert [row['frame_ref'] for row in groups.frames('B', 't2')] == ['b-top.png', 'b-side.png'], 'no frame of A joined B'
    assert groups.pending() == []


def test_the_same_frame_again_has_no_effect_but_another_frame_of_a_joined_view_keeps_the_part_from_ok(tmp_path):
    clock = Clock()
    groups = store(tmp_path, clock)
    groups.add_frame('A', 't1', 'top', 'top-1.png', 1_000, 'OK')
    assert groups.add_frame('A', 't1', 'top', 'top-1.png', 1_000, 'OK').disposition == 'duplicate'
    conflict = groups.add_frame('A', 't1', 'top', 'top-2.png', 1_005, 'NG')
    assert (conflict.disposition, conflict.state) == ('conflict', 'OPEN')
    done = groups.add_frame('A', 't1', 'side', 'side.png', 1_010, 'OK')
    assert outcome(done) == ('COMPLETE', 'REVIEW'), 'a re-fired camera that saw NG never ends OK'
    assert 'top top-2.png (conflict, NG)' in done.reason and done.frame_refs['top'] == 'top-1.png'
    # The same frame after completion is still a duplicate; another frame of a joined view cannot change the verdict
    # already given and is returned as an alarm.
    assert groups.add_frame('A', 't1', 'side', 'side.png', 1_010, 'OK').disposition == 'duplicate'
    late = groups.add_frame('A', 't1', 'side', 'side-again.png', 1_011, 'NG')
    assert (late.disposition, *outcome(late)) == ('conflict', 'COMPLETE', 'REVIEW')
    assert late.alarms and 'side side-again.png (conflict, NG)' in late.alarms[0]
    assert [row['disposition'] for row in groups.frames('A', 't1')] == ['accepted', 'duplicate', 'conflict', 'accepted', 'duplicate', 'conflict']
    # The reported order of the review: the side view between the two top frames. The verdict was given, so the second
    # top frame is an alarm on the OK part.
    groups.add_frame('G', 't1', 'top', 'g-top-1.png', 1_000, 'OK')
    given = groups.add_frame('G', 't1', 'side', 'g-side.png', 1_000, 'OK')
    assert outcome(given) == ('COMPLETE', 'OK') and given.alarms == []
    after = groups.add_frame('G', 't1', 'top', 'g-top-2.png', 1_002, 'NG')
    assert (after.disposition, *outcome(after)) == ('conflict', 'COMPLETE', 'OK') and 'g-top-2.png' in after.alarms[0]
    assert groups.group('G', 't1').alarms == after.alarms, 'the alarm stays with the group'
    # The same image re-judged differently, or a second agreeing image of a joined view, is another frame too.
    groups.add_frame('H', 't1', 'top', 'h-top.png', 1_000, 'OK')
    assert groups.add_frame('H', 't1', 'top', 'h-top.png', 1_000, 'NG').disposition == 'conflict'
    groups.add_frame('I', 't1', 'top', 'i-top.png', 1_000, 'OK')
    assert groups.add_frame('I', 't1', 'top', 'i-top-b.png', 1_001, 'OK').disposition == 'conflict'
    assert outcome(groups.add_frame('I', 't1', 'side', 'i-side.png', 1_000, 'OK')) == ('COMPLETE', 'REVIEW')
    # NG stays NG with a conflict; a skewed NG frame of a view that joined later, or an unknown view, also blocks OK.
    groups.add_frame('B', 't1', 'top', 'b-top.png', 1_000, 'NG')
    groups.add_frame('B', 't1', 'top', 'b-top-2.png', 1_001, 'OK')
    assert outcome(groups.add_frame('B', 't1', 'side', 'b-side.png', 1_000, 'OK')) == ('COMPLETE', 'NG')
    groups.add_frame('C', 't1', 'top', 'c-top.png', 1_000, 'OK')
    groups.add_frame('C', 't1', 'side', 'c-side-old.png', 1_300, 'NG')
    skewed = groups.add_frame('C', 't1', 'side', 'c-side.png', 1_010, 'OK')
    assert outcome(skewed) == ('COMPLETE', 'REVIEW') and 'out_of_skew, NG' in skewed.reason
    groups.add_frame('D', 't1', 'front', 'd-front.png', 1_000, 'NG')
    groups.add_frame('D', 't1', 'top', 'd-top.png', 1_000, 'OK')
    unknown = groups.add_frame('D', 't1', 'side', 'd-side.png', 1_000, 'OK')
    assert outcome(unknown) == ('COMPLETE', 'REVIEW') and 'front d-front.png (unknown_view, NG)' in unknown.reason


def test_a_missing_view_expires_to_review_and_a_late_frame_cannot_make_it_ok(tmp_path):
    clock = Clock()
    groups = store(tmp_path, clock)
    groups.add_frame('A', 't1', 'top', 'top.png', 1_000, 'OK')
    groups.add_frame('Z', 't0', 'top', 'z-top.png', 1_000, 'OK')
    clock.now += 1_000
    assert groups.expire_due() == [], 'not before its deadline'
    assert outcome(groups.add_frame('Z', 't0', 'side', 'z-side.png', 1_000, 'OK')) == ('COMPLETE', 'OK'), 'a frame at the deadline joins'
    clock.now += 1
    [expired] = groups.expire_due()
    assert (expired.state, expired.verdict, expired.missing_view_ids) == ('EXPIRED', 'REVIEW', ['side'])
    late = groups.add_frame('A', 't1', 'side', 'side.png', 1_050, 'OK')
    assert (late.disposition, *outcome(late)) == ('late', 'EXPIRED', 'REVIEW')
    # Without a sweep, the next frame after the deadline closes the group first.
    groups.add_frame('B', 't2', 'top', 'b-top.png', 1_000, 'OK')
    clock.now += 1_001
    closed = groups.add_frame('B', 't2', 'side', 'b-side.png', 1_000, 'OK')
    assert (closed.disposition, *outcome(closed)) == ('late', 'EXPIRED', 'REVIEW')
    # A complete part keeps its verdict after its deadline and a later frame.
    groups.add_frame('C', 't3', 'top', 'c-top.png', 1_000, 'NG')
    clock.now += 900
    groups.add_frame('C', 't3', 'side', 'c-side.png', 1_000, 'OK')
    clock.now += 200
    assert groups.expire_due() == []
    assert outcome(groups.add_frame('C', 't3', 'side', 'c-side-2.png', 1_000, 'OK')) == ('COMPLETE', 'NG')
    assert outcome(groups.group('C', 't3')) == ('COMPLETE', 'NG')


def test_a_view_that_arrived_only_outside_the_skew_ends_incomplete_and_a_view_that_never_arrived_expired(tmp_path):
    policy = CapturePolicy(required_view_ids=('top', 'side', 'bottom'), timestamp_basis='shared_clock', max_skew_ms=200, deadline_ms=1_000)
    clock = Clock()
    groups = store(tmp_path, clock, policy)
    groups.add_frame('A', 't1', 'top', 'top.png', 1_000, 'OK')
    groups.add_frame('A', 't1', 'side', 'old-side.png', 1_000 + 201, 'OK')
    clock.now += 1_001
    [closed] = groups.expire_due()
    assert outcome(closed) == ('INCOMPLETE', 'REVIEW') and closed.missing_view_ids == ['side', 'bottom']
    assert 'side old-side.png (out_of_skew, OK)' in closed.reason
    # The skewed side view joined later in time: only bottom is missing, and it never arrived.
    groups.add_frame('B', 't1', 'top', 'top.png', 1_000, 'OK')
    groups.add_frame('B', 't1', 'side', 'old-side.png', 700, 'OK')
    groups.add_frame('B', 't1', 'side', 'side.png', 1_100, 'OK')
    clock.now += 1_001
    [closed] = groups.expire_due()
    assert outcome(closed) == ('EXPIRED', 'REVIEW') and closed.missing_view_ids == ['bottom']


def test_the_skew_spans_every_joined_capture_whatever_order_they_arrive_in(tmp_path):
    policy = CapturePolicy(required_view_ids=('a', 'b', 'c'), timestamp_basis='shared_clock', max_skew_ms=200, deadline_ms=1_000)
    captured = {'a': 1_000, 'b': 1_200, 'c': 1_400}
    for number, order in enumerate(itertools.permutations('abc')):
        clock = Clock()
        groups = CaptureGroups(tmp_path / f'groups-{number}.sqlite3', policy, wall_ms=clock, monotonic_ms=clock)
        results = [groups.add_frame('P', 't', view, f'{view}.png', captured[view], 'OK') for view in order]
        assert results[-1].state != 'COMPLETE', f'{order}: a 400 ms spread never joins under a 200 ms skew'
        assert [result.disposition for result in results].count('out_of_skew') == 1
        clock.now += 1_001
        assert outcome(groups.expire_due()[0]) == ('INCOMPLETE', 'REVIEW')
    clock = Clock()
    groups = store(tmp_path, clock)
    groups.add_frame('E', 't', 'top', 'top.png', 1_000, 'OK')
    assert groups.add_frame('E', 't', 'side', 'early.png', 1_000 - 201, 'OK').disposition == 'out_of_skew', 'an earlier capture too'
    assert outcome(groups.add_frame('E', 't', 'side', 'side.png', 1_000 - 200, 'OK')) == ('COMPLETE', 'REVIEW'), 'exactly the skew joins'
    offsets = CapturePolicy(required_view_ids=('top', 'side'), timestamp_basis='trigger_offset', max_skew_ms=200, deadline_ms=1_000)
    triggered = CaptureGroups(tmp_path / 'offsets.sqlite3', offsets, wall_ms=clock, monotonic_ms=clock)
    # Measured from the trigger: a frame 250 ms after it never joins, even when it arrives first.
    assert triggered.add_frame('F', 't', 'side', 'late-side.png', 250, 'OK').disposition == 'out_of_skew'
    triggered.add_frame('F', 't', 'top', 'top.png', 30, 'OK')
    assert outcome(triggered.add_frame('F', 't', 'side', 'side.png', 200, 'OK')) == ('COMPLETE', 'REVIEW')


def test_deadlines_run_on_the_shared_monotonic_clock_and_an_unknown_gap_expires_pending_parts(tmp_path):
    wall, monotonic = Clock(5_000_000), Clock(10_000)  # the system's monotonic clock is shared by every process
    first = store(tmp_path, monotonic, wall=wall)
    first.add_frame('A', 't1', 'top', 'top.png', 5_000_000, 'OK')
    wall.now -= 3_600_000  # the wall clock steps back an hour while the app runs (time sync)
    monotonic.now += 1_001
    late = first.add_frame('A', 't1', 'side', 'side.png', 5_000_100, 'OK')
    assert (late.disposition, *outcome(late)) == ('late', 'EXPIRED', 'REVIEW'), 'a running process ignores wall steps'
    assert 'clock changed' in late.reason, 'the step itself expired the pending part on the next write'
    # Two processes of one boot: the second opens after another step the first has not seen, so the pending part's
    # elapsed time is unknown and it is expired (REVIEW) rather than joined late.
    first.add_frame('B', 't1', 'top', 'top.png', 5_000_000, 'OK')
    wall.now += 3_600_000
    second = store(tmp_path, monotonic, wall=wall)
    assert second.pending() == [{'part_id': 'B', 'trigger_id': 't1', 'deadline_at_ms': -1}], 'marked due at the open'
    [expired] = second.expire_due()
    assert (expired.part_id, *outcome(expired)) == ('B', 'EXPIRED', 'REVIEW') and 'clock changed' in expired.reason, 'and reported by the sweep'
    # Without a step, a second process shares the first one's time exactly: a 5 s late frame never joins.
    first_again = store(tmp_path, monotonic, wall=wall)
    first_again.add_frame('C', 't1', 'top', 'top.png', 5_000_000, 'OK')
    other = store(tmp_path, monotonic, wall=wall)
    monotonic.now += 5_000
    assert outcome(other.add_frame('C', 't1', 'side', 'side.png', 5_000_000, 'OK')) == ('EXPIRED', 'REVIEW')
    # A sleep the monotonic clock did not count: the wall clock moved 2 h while monotonic time stood still.
    first_again.add_frame('S', 't1', 'top', 'top.png', 5_000_000, 'OK')
    wall.now += 7_200_000
    slept = first_again.add_frame('S', 't1', 'side', 'side.png', 5_000_000, 'OK')
    assert (slept.disposition, *outcome(slept)) == ('late', 'EXPIRED', 'REVIEW') and 'clock changed' in slept.reason
    # A restart of the computer (the monotonic clock starts again below what the store recorded) expires pending parts.
    first_again.add_frame('D', 't1', 'top', 'top.png', 5_000_000, 'OK')
    rebooted = store(tmp_path, Clock(500), wall=Clock(wall.now + 60_000_000))
    assert [(result.part_id, *outcome(result)) for result in rebooted.expire_due()] == [('D', 'EXPIRED', 'REVIEW')]
    assert rebooted.pending() == []
    # Even when the wall clock happens to give the same epoch, a monotonic clock below what the store recorded is
    # another boot or computer.
    rebooted.add_frame('E', 't1', 'top', 'top.png', 5_000_000, 'OK')
    epoch = wall.now + 60_000_000 - 500
    other_computer = store(tmp_path, Clock(100), wall=Clock(epoch + 100))
    assert [(result.part_id, *outcome(result)) for result in other_computer.expire_due()] == [('E', 'EXPIRED', 'REVIEW')]


def test_a_restart_keeps_pending_groups_with_their_deadline_and_the_join_policy(tmp_path):
    clock = Clock()
    groups = store(tmp_path, clock)
    groups.add_frame('A', 't1', 'top', 'top.png', 1_000, 'OK')
    [pending] = groups.pending()
    del groups
    clock.now += 500
    reopened = store(tmp_path, clock, CapturePolicy(required_view_ids=['side', 'top'], timestamp_basis='shared_clock', max_skew_ms=200, deadline_ms=1_000))
    assert reopened.pending() == [pending], 'the same group with its original deadline (the views in any order)'
    assert outcome(reopened.add_frame('A', 't1', 'side', 'side.png', 1_010, 'OK')) == ('COMPLETE', 'OK')
    for change in ({'required_view_ids': ('top', 'side', 'bottom')}, {'max_skew_ms': 201}, {'deadline_ms': 999},
                   {'timestamp_basis': 'trigger_offset'}):
        other = CapturePolicy(**{'required_view_ids': ('top', 'side'), 'timestamp_basis': 'shared_clock', 'max_skew_ms': 200,
                                 'deadline_ms': 1_000, **change})
        with pytest.raises(ValueError, match='another join policy'):
            store(tmp_path, clock, other)


def test_a_part_whose_views_are_all_unknown_still_gets_a_verdict_at_its_deadline(tmp_path):
    clock = Clock()
    groups = store(tmp_path, clock)
    first = groups.add_frame('A', 't1', 'Top', 'top.png', 1_000, 'OK')
    assert (first.disposition, first.state) == ('unknown_view', 'OPEN') and groups.pending()[0]['part_id'] == 'A'
    groups.add_frame('A', 't1', 'Side', 'side.png', 1_000, 'OK')
    clock.now += 1_001
    [expired] = groups.expire_due()
    assert outcome(expired) == ('EXPIRED', 'REVIEW') and 'Top top.png (unknown_view, OK)' in expired.reason


def test_a_frame_past_the_late_window_is_refused_every_time_and_the_part_keeps_its_verdict(tmp_path):
    clock = Clock()
    policy = CapturePolicy(required_view_ids=('top', 'side'), timestamp_basis='shared_clock', max_skew_ms=200, deadline_ms=1_000,
                           late_window_ms=5_000)
    groups = store(tmp_path, clock, policy)
    groups.add_frame('P0001', '0001', 'top', 'top.png', 1_000, 'OK')
    groups.add_frame('P0001', '0001', 'side', 'side.png', 1_000, 'OK')
    clock.now += 5_000
    assert groups.add_frame('P0001', '0001', 'bottom', 'extra.png', 1_000, 'OK').disposition == 'late', 'inside the late window'
    clock.now += 86_400_000  # the counter wrapped: a new part with the same ids
    with pytest.raises(CaptureKeyReused, match='late window') as refused:
        groups.add_frame('P0001', '0001', 'top', 'new-top.png', 87_400_000, 'NG')
    assert not isinstance(refused.value, ValueError), 'not mistaken for an input error'
    assert groups.frames('P0001', '0001')[-1]['disposition'] == 'refused', 'recorded before it is refused'
    with pytest.raises(CaptureKeyReused):  # an at-least-once queue delivers it again
        groups.add_frame('P0001', '0001', 'top', 'new-top.png', 87_400_000, 'NG')
    assert outcome(groups.group('P0001', '0001')) == ('COMPLETE', 'OK')


def test_a_view_without_a_verdict_makes_the_part_review(tmp_path):
    assert joined_verdict(['OK', 'OK']) == 'OK'
    assert joined_verdict(['OK', None]) == 'REVIEW'
    assert joined_verdict(['REVIEW', 'NG']) == 'NG'
    groups = store(tmp_path, Clock())
    groups.add_frame('A', 't1', 'top', 'top.png', 1_000, None)
    assert outcome(groups.add_frame('A', 't1', 'side', 'side.png', 1_000, 'OK')) == ('COMPLETE', 'REVIEW')


def test_bad_policies_and_frames_are_refused(tmp_path):
    base = {'required_view_ids': ('top',), 'timestamp_basis': 'shared_clock', 'max_skew_ms': 10, 'deadline_ms': 10}
    for bad in ({'required_view_ids': ()}, {'required_view_ids': ('top', 'top')}, {'required_view_ids': 'top'},
                {'required_view_ids': ('top', ' ')}, {'deadline_ms': 0}, {'max_skew_ms': -1}, {'max_skew_ms': True},
                {'deadline_ms': 10**19}, {'deadline_ms': 10.0}, {'timestamp_basis': 'camera'}, {'completeness_policy': 'any'},
                {'late_window_ms': -1}, {'late_window_ms': True}, {'late_window_ms': 10**12}):
        with pytest.raises(ValueError):
            CapturePolicy(**{**base, **bad})
    groups = store(tmp_path, Clock())
    for args in (('A', 't1', 'top', 'top.png', 1_000, 'maybe'), ('', 't1', 'top', 'top.png', 1_000, 'OK'),
                 ('A', 't1', 'top', 'top.png', 999.9, 'OK'), ('A', 't1', 'top', 'top.png', '1000', 'OK'),
                 ('A', 't1', 'top', 'top.png', True, 'OK')):
        with pytest.raises(ValueError):
            groups.add_frame(*args)
    assert groups.group('A', 't1') is None and groups.frames('A', 't1') == []


def test_the_deadline_sweep_reads_only_open_groups(tmp_path):
    groups = store(tmp_path, Clock())
    with closing(sqlite3.connect(groups.path)) as db:
        plan = db.execute("EXPLAIN QUERY PLAN SELECT part_id, trigger_id FROM groups WHERE state='OPEN' AND deadline_at_ms < 5"
                          ' ORDER BY deadline_at_ms').fetchall()
    assert any('groups_due' in str(row) for row in plan), plan


def test_two_writers_completing_one_group_close_it_once(tmp_path):
    clock = Clock()
    first = store(tmp_path, clock)
    second = store(tmp_path, clock)
    paused = threading.Event()
    calls = []

    def frames_then_pause(db, part_id, trigger_id):
        calls.append(1)
        if len(calls) == 2:  # the first writer recorded its frame and is about to judge completeness
            paused.set()
            time.sleep(0.4)
        return CaptureGroups._frames(db, part_id, trigger_id)

    first._frames = frames_then_pause
    results, errors = [], []

    def add(groups, view):
        try:
            results.append(groups.add_frame('A', 't1', view, f'{view}.png', 1_000, 'OK'))
        except Exception as exc:  # asserted below
            errors.append(repr(exc))

    one = threading.Thread(target=add, args=(first, 'top'))
    one.start()
    assert paused.wait(5)
    two = threading.Thread(target=add, args=(second, 'side'))
    two.start()
    one.join()
    two.join()
    assert errors == []
    assert sorted(result.state for result in results) == ['COMPLETE', 'OPEN'], 'the second writer waited for the first'
    assert outcome(second.group('A', 't1')) == ('COMPLETE', 'OK')
    assert [row['disposition'] for row in second.frames('A', 't1')] == ['accepted', 'accepted']


def test_offsets_before_the_trigger_older_stores_and_long_reasons(tmp_path):
    clock = Clock()
    offsets = CapturePolicy(required_view_ids=('top', 'side'), timestamp_basis='trigger_offset', max_skew_ms=200, deadline_ms=1_000)
    groups = CaptureGroups(tmp_path / 'offsets.sqlite3', offsets, wall_ms=clock, monotonic_ms=clock)
    assert groups.add_frame('A', 't', 'top', 'top.png', -150, 'OK').disposition == 'accepted', 'a capture just before the trigger'
    assert groups.add_frame('A', 't', 'side', 'side.png', -201, 'OK').disposition == 'out_of_skew'
    many = store(tmp_path, clock)
    for index in range(30):
        many.add_frame('B', 't', 'front', f'front-{index}.png', 1_000, 'OK')
    clock.now += 1_001
    [closed] = many.expire_due()
    assert 'and 22 more' in closed.reason and len(closed.reason) < 600, 'a stuck adapter does not grow the reason without bound'
    with closing(sqlite3.connect(tmp_path / 'groups.sqlite3')) as db:
        db.execute("UPDATE meta SET value='1' WHERE name='schema_version'")
        db.commit()
    with pytest.raises(ValueError, match='another version of the capture group store'):
        store(tmp_path, clock)


LONG = CapturePolicy(required_view_ids=('top', 'side'), timestamp_basis='shared_clock', max_skew_ms=200, deadline_ms=3_600_000)


def due(groups):
    return [row['part_id'] for row in groups.pending() if row['deadline_at_ms'] == -1]


def test_parts_closed_by_a_clock_change_are_reported_by_the_next_sweep(tmp_path):
    wall, monotonic = Clock(5_000_000), Clock(10_000)
    groups = store(tmp_path, monotonic, LONG, wall=wall)
    groups.add_frame('A', 't1', 'top', 'a.png', 0, 'OK')
    groups.add_frame('B', 't1', 'top', 'b.png', 0, 'OK')
    wall.now += 7_200_000  # a sleep the monotonic clock did not count
    assert [(result.part_id, *outcome(result)) for result in groups.expire_due()] == [('A', 'EXPIRED', 'REVIEW'), ('B', 'EXPIRED', 'REVIEW')]
    # Another part's frame finds the change first: its own result comes back, and the sweep reports the others.
    groups.add_frame('C', 't1', 'top', 'c.png', 0, 'OK')
    groups.add_frame('D', 't1', 'top', 'd.png', 0, 'OK')
    wall.now -= 3_600_000
    assert outcome(groups.add_frame('E', 't1', 'top', 'e.png', 0, 'OK')) == ('OPEN', None), 'a part opened after the change keeps its deadline'
    reported = groups.expire_due()
    assert [result.part_id for result in reported] == ['C', 'D'] and all('clock changed' in result.reason for result in reported)
    assert [row['part_id'] for row in groups.pending()] == ['E']


def test_the_clock_tolerance_is_two_seconds_plus_the_rate_correction_and_follows_slow_corrections(tmp_path):
    wall, monotonic = Clock(5_000_000), Clock(10_000)
    groups = store(tmp_path, monotonic, LONG, wall=wall)
    groups.add_frame('A', 't1', 'top', 'a.png', 0, 'OK')
    wall.now += 2_000  # an uncounted sleep of exactly the tolerance
    groups.add_frame('Z1', 't1', 'top', 'z.png', 0, 'OK')
    assert due(groups) == []
    wall.now += 2_001
    groups.add_frame('Z2', 't1', 'top', 'z.png', 0, 'OK')
    assert due(groups) == ['A', 'Z1']
    groups.expire_due()
    # 1,000 s since the last write allow 1 s more for the wall clock's rate correction.
    monotonic.now += 1_000_000
    wall.now += 1_000_000 + 3_000
    groups.add_frame('Z3', 't1', 'top', 'z.png', 0, 'OK')
    assert due(groups) == []
    monotonic.now += 1_000_000
    wall.now += 1_000_000 + 3_001
    groups.add_frame('Z4', 't1', 'top', 'z.png', 0, 'OK')
    assert due(groups) == ['Z2', 'Z3']
    groups.expire_due()
    # A slow correction (time sync) re-anchors the epoch on every write and never adds up to a change.
    for index in range(20):
        monotonic.now += 10
        wall.now += 1_500
        groups.add_frame(f'R{index}', 't1', 'top', 'r.png', 0, 'OK')
    assert due(groups) == []


def test_a_restart_reopened_twice_expires_only_the_parts_of_the_earlier_boot(tmp_path):
    wall, monotonic = Clock(5_000_000), Clock(90_000)
    store(tmp_path, monotonic, LONG, wall=wall).add_frame('A', 't1', 'top', 'a.png', 0, 'OK')
    after, after_wall = Clock(500), Clock(wall.now + 600_000)
    store(tmp_path, after, LONG, wall=after_wall).add_frame('B', 't1', 'top', 'b.png', 0, 'OK')
    after.now += 100
    after_wall.now += 100
    again = store(tmp_path, after, LONG, wall=after_wall)
    assert [result.part_id for result in again.expire_due()] == ['A']
    assert [row['part_id'] for row in again.pending()] == ['B'], 'the new boot is the store\'s clock now'


def test_a_write_below_the_last_write_marks_pending_parts_due(tmp_path):
    wall, monotonic = Clock(5_000_000), Clock(90_000)
    groups = store(tmp_path, monotonic, LONG, wall=wall)
    groups.add_frame('A', 't1', 'top', 'a.png', 0, 'OK')
    monotonic.now -= 40_000  # the same epoch, but below the last write: another boot or computer
    wall.now -= 40_000
    groups.add_frame('B', 't1', 'top', 'b.png', 0, 'OK')
    assert due(groups) == ['A']


def test_a_frame_for_a_part_closed_before_a_clock_change_is_refused(tmp_path):
    wall, monotonic = Clock(5_000_000), Clock(90_000)
    groups = store(tmp_path, monotonic, wall=wall)
    groups.add_frame('A', 't1', 'top', 'a.png', 0, 'OK')
    assert outcome(groups.add_frame('A', 't1', 'side', 'a-side.png', 0, 'OK')) == ('COMPLETE', 'OK')
    # Ten minutes later on another boot whose monotonic clock happens to read 500 ms past the close: the time since the
    # verdict is unknown, not 500 ms.
    rebooted = store(tmp_path, Clock(90_500), wall=Clock(wall.now + 600_000))
    with pytest.raises(CaptureKeyReused, match='clock change'):
        rebooted.add_frame('A', 't1', 'side', 'other-side.png', 0, 'NG')
    assert outcome(rebooted.group('A', 't1')) == ('COMPLETE', 'OK') and rebooted.group('A', 't1').alarms == []


def test_an_alarm_is_a_conflict_recorded_after_the_verdict_even_within_one_millisecond(tmp_path):
    groups = store(tmp_path, Clock())
    groups.add_frame('A', 't1', 'top', 'top-1.png', 1_000, 'OK')
    groups.add_frame('A', 't1', 'top', 'top-2.png', 1_000, 'NG')  # the same millisecond, before the verdict
    done = groups.add_frame('A', 't1', 'side', 'side.png', 1_000, 'OK')
    assert outcome(done) == ('COMPLETE', 'REVIEW') and done.alarms == []
    after = groups.add_frame('A', 't1', 'side', 'side-2.png', 1_000, 'OK')  # the same millisecond, after it
    assert len(after.alarms) == 1 and 'side-2.png' in after.alarms[0] and 'top-2.png' not in after.alarms[0]
    assert [row['after_close'] for row in groups.frames('A', 't1')] == [False, False, False, True]


def test_a_read_sees_one_state_of_the_store(tmp_path):
    clock = Clock()
    reader, writer = store(tmp_path, clock), store(tmp_path, clock)
    reader.add_frame('A', 't1', 'top', 'top.png', 1_000, 'OK')
    wrote = []

    def write_between(db, part_id, trigger_id):
        if not wrote:  # the group is read; another process completes it before the frames are read
            wrote.append(writer.add_frame('A', 't1', 'side', 'side.png', 1_000, 'OK'))
        return CaptureGroups._frames(db, part_id, trigger_id)

    reader._frames = write_between
    seen = reader.group('A', 't1')
    assert outcome(wrote[0]) == ('COMPLETE', 'OK')
    assert (seen.state, seen.missing_view_ids) == ('OPEN', ['side']), 'the group and its frames from one state'
    assert outcome(reader.group('A', 't1')) == ('COMPLETE', 'OK')


def test_opening_the_store_while_another_process_writes_keeps_pending_parts(tmp_path):
    ticks = itertools.count(1_000_000)
    opener_read = threading.Event()

    def clock():  # a monotonic clock: every read is later than the one before
        if threading.current_thread() is not threading.main_thread():
            opener_read.set()
        return next(ticks)

    path = tmp_path / 'groups.sqlite3'
    groups = CaptureGroups(path, LONG, wall_ms=clock, monotonic_ms=clock)
    groups.add_frame('A', 't1', 'top', 'a.png', 0, 'OK')
    blocker = sqlite3.connect(path, timeout=10, isolation_level=None)
    try:
        blocker.execute('BEGIN IMMEDIATE')
        opened = []
        opener = threading.Thread(target=lambda: opened.append(CaptureGroups(path, LONG, wall_ms=clock, monotonic_ms=clock)))
        opener.start()
        opener_read.wait(0.5)  # an opener that reads its clock before the write lock has read it by now
        # Another writer of this boot commits a later reading of the same clock while the opener waits.
        blocker.execute("UPDATE meta SET value=? WHERE name='clock_floor_ms'", (str(clock()),))
        blocker.execute('COMMIT')
    finally:
        blocker.close()
    opener.join(10)
    assert opened and due(groups) == [] and groups.expire_due() == []


WRITER = """
import sys, time
from backend.engine.capture_groups import CaptureGroups, CapturePolicy
policy = CapturePolicy(required_view_ids=('top', 'side'), timestamp_basis='shared_clock', max_skew_ms=200, deadline_ms=3_600_000)
groups = CaptureGroups(sys.argv[1], policy)
end, index = time.monotonic() + float(sys.argv[2]), 0
while time.monotonic() < end:
    groups.add_frame(f'P{index}', 't', 'top', f'{index}.png', 0, 'OK')
    index += 1
print(index)
"""


def test_a_second_process_opening_the_store_on_the_real_clocks_keeps_the_writers_pending_parts(tmp_path):
    path = tmp_path / 'groups.sqlite3'
    CaptureGroups(path, LONG)
    writer = subprocess.Popen([sys.executable, '-c', WRITER, str(path), '2.5'], cwd=Path(__file__).resolve().parents[2],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        started = time.monotonic()
        while time.monotonic() - started < 20 and writer.poll() is None:
            with closing(sqlite3.connect(path, timeout=10)) as db:
                if db.execute('SELECT COUNT(*) FROM groups').fetchone()[0] >= 20:
                    break
            time.sleep(0.02)
        for _ in range(30):
            CaptureGroups(path, LONG)
            time.sleep(0.02)
        out, err = writer.communicate(timeout=60)
    finally:
        if writer.poll() is None:
            writer.kill()  # this test's own child, by its handle
            writer.communicate()
    assert writer.returncode == 0, err
    groups = CaptureGroups(path, LONG)
    assert len(groups.pending()) == int(out) > 20
    assert due(groups) == [], 'no open of this boot took the writer\'s later readings for another boot'
