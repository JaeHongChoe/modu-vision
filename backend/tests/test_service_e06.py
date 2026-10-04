"""E06: field intake sampling keeps images for review within a budget, repeatably, with an ordinary baseline spread over
the window, typed scores, visible quota exhaustion and retention states, and every receipt naming its origin; nothing
kept becomes truth by being kept.
"""
import json
import sqlite3

import pytest

from backend.engine.intake_sampling import EventIdReused, IntakeEvent, IntakeSampler, IntakeSamplingPolicy

PROBABILITY = {'domain': 'probability', 'unit': 'probability', 'direction': 'higher_is_defect', 'calibration_id': 'cal-a', 'threshold': 0.5}
DISTANCE = {'domain': 'distance', 'unit': 'mahalanobis_distance', 'direction': 'higher_is_defect', 'calibration_id': 'cal-b', 'threshold': 3.0}
ORIGIN = {'run_id': 'run-7', 'node_id': 'node_decision', 'recipe': 'v12'}


def policy(**overrides):
    values = dict(revision=1, seed=20261004, eligibility_reasons=('ng_verdict', 'review_verdict', 'operator_flag', 'low_confidence'),
                  window_seconds=3600, max_items_per_window=50, max_bytes_per_window=10_000_000, per_product_lot_camera_quota=30,
                  normal_baseline_fraction=0.2, score_spec=PROBABILITY, low_confidence_band=(0.4, 0.6))
    return IntakeSamplingPolicy(**{**values, **overrides})


def event(index, verdict='OK', **overrides):
    values = dict(event_id=f'e{index:05d}', captured_at=1_000.0 + index, size_bytes=100_000, product='bracket', lot=f'L{index % 3}',
                  camera=f'cam{index % 2}', run_ref=dict(ORIGIN), verdict=verdict)
    return IntakeEvent(**{**values, **overrides})


def stream(count=1000, start=1_000.0, every=1.0):
    return [event(i, 'NG' if i % 50 == 0 else 'REVIEW' if i % 50 == 1 else 'OK', captured_at=start + i * every) for i in range(count)]


def test_the_same_policy_seed_and_events_give_the_same_receipts_and_a_repeat_returns_its_first_receipt(tmp_path):
    first = IntakeSampler(tmp_path / 'a.sqlite3', policy())
    second = IntakeSampler(tmp_path / 'b.sqlite3', policy())
    events = stream(3600, start=0.0)
    receipts = [first.decide(e) for e in events]
    assert receipts == [second.decide(e) for e in events]
    before = first.status(0)
    skipped = next(i for i, r in enumerate(receipts) if r.decision == 'skipped')
    for index in (0, skipped, 3500):  # a selected NG, a skipped ordinary image, a late image after the budget filled
        again = first.decide(events[index])
        assert again == receipts[index], 'the stored first receipt, with its decision, status and origin'
    assert first.status(0) == before, 'a repeat spends nothing'
    assert receipts[0].decision == 'selected' and receipts[3500].decision == 'skipped'
    other_seed = [r.reason for r in map(IntakeSampler(tmp_path / 'c.sqlite3', policy(seed=7)).decide, events[:720])]
    assert other_seed != [r.reason for r in receipts[:720]], 'the baseline follows the seed'


def test_an_event_id_is_one_image_for_the_life_of_the_store(tmp_path):
    sampler = IntakeSampler(tmp_path / 's.sqlite3', policy())
    kept = sampler.decide(event(0, 'NG'))
    before = sampler.status(0)
    other = event(0, 'OK', camera='camX', lot='LX', captured_at=99_000.0, size_bytes=9_990_000,
                  run_ref={'run_id': 'run-OTHER', 'node_id': 'n-other', 'recipe': 'v99'})
    with pytest.raises(EventIdReused, match='already decided for another image'):
        sampler.decide(other)
    assert sampler.status(0) == before and sampler.status(other.captured_at // 3600)['selected'] == 0
    assert sampler.decide(event(0, 'NG')) == kept


def test_an_hour_keeps_its_budgets_and_a_baseline_spread_over_the_whole_window(tmp_path):
    sampler = IntakeSampler(tmp_path / 's.sqlite3', policy())
    events = stream(3600, start=0.0)  # one camera pair, one image a second for one hour
    receipts = [sampler.decide(e) for e in events]
    status = sampler.status(0)
    assert status['selected'] == 50 and status['baseline'] == 10 and status['bytes'] <= 10_000_000, status
    # Golden: the seed's sha256 choice, slot by slot (a per-process hash() or another rule changes these).
    assert [r.event_id for r in receipts if r.reason == 'baseline'] == ['e00003', 'e00369', 'e00721', 'e01082', 'e01443', 'e01820',
                                                                         'e02160', 'e02528', 'e02883', 'e03246']
    assert [(r.event_id, r.reason) for r in receipts[:4]] == [('e00000', 'ng_verdict'), ('e00001', 'review_verdict'),
                                                               ('e00002', 'not_eligible'), ('e00003', 'baseline')]
    baseline = [e.captured_at for e, r in zip(events, receipts) if r.reason == 'baseline']
    assert len(baseline) == 10 and sorted(int(t // 360) for t in baseline) == list(range(10)), 'one baseline image per 6-minute slot'
    assert any(t < 900 for t in baseline) and any(t >= 2700 for t in baseline), 'kept in the first and the last quarter'
    eligible = [(e, r) for e, r in zip(events, receipts) if r.decision == 'selected' and r.reason != 'baseline']
    assert len(eligible) == 40 and all(r.reason == {'NG': 'ng_verdict', 'REVIEW': 'review_verdict'}[e.verdict] for e, r in eligible)
    assert all(r.run_ref == ORIGIN and r.status == 'proposed_for_review' and r.retention == 'pending' for r in receipts if r.decision == 'selected')
    assert {'items', 'baseline_slot'} <= set(status['quota_exhausted'])
    keyed = IntakeSampler(tmp_path / 'key.sqlite3', policy(per_product_lot_camera_quota=1, eligibility_reasons=('operator_flag',)))
    keyed_receipts = [keyed.decide(event(i, captured_at=i * 1.0, lot='L', camera='cam')) for i in range(3600)]
    assert sum(r.decision == 'selected' for r in keyed_receipts) == 1, 'the key quota applies to baseline images too'
    assert any(r.reason == 'quota_exhausted:product_lot_camera' for r in keyed_receipts)


def test_a_window_budget_is_shared_by_every_revision_and_one_spelling_is_one_policy(tmp_path):
    path = tmp_path / 's.sqlite3'
    first = IntakeSampler(path, policy(normal_baseline_fraction=0, max_items_per_window=10))
    [first.decide(event(i, 'NG')) for i in range(20)]
    second = IntakeSampler(path, policy(normal_baseline_fraction=0, max_items_per_window=10, revision=2))
    later = [second.decide(event(100 + i, 'NG')) for i in range(10)]
    assert all(r.reason == 'quota_exhausted:items' for r in later), 'a new revision continues the window, not a second budget'
    status = second.status(0)
    assert status['selected'] == 10 and set(status['by_policy']) == {first.policy.ref, second.policy.ref}
    assert status['by_policy'][first.policy.ref]['selected'] == 10 and status['by_policy'][second.policy.ref]['selected'] == 0
    assert policy(normal_baseline_fraction=0).ref == policy(normal_baseline_fraction=0.0).ref
    assert policy(score_spec={**PROBABILITY, 'threshold': 1}).ref == policy(score_spec={**PROBABILITY, 'threshold': 1.0}).ref
    spec = dict(PROBABILITY)
    held = policy(score_spec=spec)
    spec['calibration_id'] = 'changed later'
    assert held.ref == policy().ref and held.score_spec['calibration_id'] == 'cal-a', 'the policy is read once'


def test_quotas_bind_exactly_and_every_binding_quota_is_named(tmp_path):
    exact = IntakeSampler(tmp_path / 'e.sqlite3', policy(normal_baseline_fraction=0, max_bytes_per_window=300_000))
    assert [exact.decide(event(i, 'NG')).decision for i in range(3)] == ['selected'] * 3, 'an exact fit is kept'
    assert exact.decide(event(3, 'NG')).reason == 'quota_exhausted:bytes'
    huge = IntakeSampler(tmp_path / 'h.sqlite3', policy(normal_baseline_fraction=0, max_bytes_per_window=300_000))
    assert huge.decide(event(0, 'NG', size_bytes=300_001)).reason == 'too_large'
    assert huge.status(0)['too_large'] == 1 and huge.status(0)['quota_exhausted'] == []
    both = IntakeSampler(tmp_path / 'b.sqlite3', policy(normal_baseline_fraction=0, max_items_per_window=2, per_product_lot_camera_quota=2,
                                                        max_bytes_per_window=200_000))
    [both.decide(event(i, 'NG', lot='L', camera='c')) for i in range(2)]
    assert both.decide(event(2, 'NG', lot='L', camera='c')).reason == 'quota_exhausted:bytes+product_lot_camera+items'


def test_band_edges_window_edges_and_the_window_offset(tmp_path):
    sampler = IntakeSampler(tmp_path / 's.sqlite3', policy(normal_baseline_fraction=0))
    for index, value, reason in ((1, 0.4, 'low_confidence'), (2, 0.6, 'low_confidence'), (3, 0.39999, 'not_eligible'), (4, 0.60001, 'not_eligible')):
        assert sampler.decide(event(index, score={'value': value, 'spec': PROBABILITY})).reason == reason, value
    assert sampler.decide(event(10, 'NG', captured_at=3599.999)).window == 0
    assert sampler.decide(event(11, 'NG', captured_at=3600.0)).window == 1
    local = IntakeSampler(tmp_path / 'l.sqlite3', policy(window_seconds=86_400, window_offset_seconds=54_000, normal_baseline_fraction=0))
    midnight_kst = 86_400 * 10 + 54_000  # 00:00 in UTC+9
    assert local.decide(event(20, 'NG', captured_at=midnight_kst - 0.5)).window == 9
    assert local.decide(event(21, 'NG', captured_at=midnight_kst)).window == 10
    assert local.status(10)['window_starts_at'] == midnight_kst


def test_only_declared_reasons_are_eligible_and_only_ordinary_images_are_baseline(tmp_path):
    sampler = IntakeSampler(tmp_path / 's.sqlite3', policy(eligibility_reasons=('low_confidence',), normal_baseline_fraction=0.9,
                                                           max_items_per_window=1000))
    undeclared = [sampler.decide(event(i, 'NG', flags=('operator_flag',) if i % 2 else ())) for i in range(40)]
    assert all(r.decision == 'skipped' and r.reason == 'not_eligible' and 'not ordinary' in r.note for r in undeclared), \
        'an undeclared NG or flag is neither kept nor taken as the ordinary baseline'
    other_type = [sampler.decide(event(100 + i, score={'value': 0.5, 'spec': DISTANCE})) for i in range(40)]
    assert all(r.decision == 'skipped' and 'another type' in r.note for r in other_type), 'an uncomparable score is not ordinary'
    broken = sampler.decide(event(200, score={'value': 0.5, 'spec': {'domain': 'probability'}}))
    assert broken.decision == 'skipped' and 'another type' in broken.note
    ordinary = [sampler.decide(event(300 + i, captured_at=1_000.0 + i * 4)) for i in range(400)]
    assert any(r.reason == 'baseline' for r in ordinary)


def test_retention_is_pending_until_marked_and_an_unreported_one_becomes_visible(tmp_path):
    now = {'t': 10_000.0}
    sampler = IntakeSampler(tmp_path / 'intake' / 's.sqlite3', policy(normal_baseline_fraction=0), clock=lambda: now['t'])
    stored = sampler.decide(event(1, 'NG'))
    failed = sampler.decide(event(2, 'NG', score={'value': 0.5, 'spec': DISTANCE}))
    lost = sampler.decide(event(3, 'NG', flags=('operator_flag',)))
    assert lost.reason == 'ng_verdict+operator_flag' and lost.retention == 'pending'
    sampler.mark_retained(stored.event_id)
    sampler.mark_retention_failed(failed.event_id, 'disk full')
    status = sampler.status(0)
    assert (status['retained'], status['retention_failed'], status['retention_pending'], status['retention_overdue']) == (1, 1, 1, 0)
    now['t'] += 301
    assert sampler.status(0)['retention_overdue'] == 1, 'a writer that died before storing it shows after the grace period'
    again = sampler.decide(event(2, 'NG', score={'value': 0.5, 'spec': DISTANCE}))
    assert (again.status, again.retention, again.failure) == ('retention_failed', 'failed', 'disk full')
    assert 'another type' in again.note, 'the failure does not replace the note'
    assert status['selected'] == 3 and status['bytes'] == 300_000, 'a failed or pending image keeps the budget it was selected under'
    with pytest.raises(ValueError):
        sampler.mark_retained(sampler.decide(event(4, 'OK')).event_id)
    assert sorted(path.name for path in (tmp_path / 'intake').iterdir() if not path.name.endswith(('-wal', '-shm'))) == ['s.sqlite3'], \
        'the sampler writes only its receipts: no label or truth'


def test_old_windows_are_pruned_and_the_store_has_a_schema_version(tmp_path):
    path = tmp_path / 's.sqlite3'
    sampler = IntakeSampler(path, policy(normal_baseline_fraction=0))
    kept = [sampler.decide(event(i, 'NG', captured_at=i * 1800.0)) for i in range(6)]  # windows 0, 0, 1, 1, 2, 2
    with pytest.raises(ValueError, match='pending retention'):
        sampler.prune(before_window=2)  # freeze 3: an image not yet stored is never pruned out of sight
    for receipt in kept[:4]:
        sampler.mark_retained(receipt.event_id)
    assert sampler.prune(before_window=2) == 4
    assert sampler.status(0)['selected'] == 0 and sampler.status(2)['selected'] == 2
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 3
        assert db.execute('SELECT COUNT(*) FROM window_usage').fetchone()[0] == 1
    old = tmp_path / 'old.sqlite3'
    with sqlite3.connect(old) as db:
        db.execute('CREATE TABLE receipts(event_id TEXT PRIMARY KEY, decision TEXT)')
    with pytest.raises(ValueError, match='another schema'):
        IntakeSampler(old, policy()).decide(event(1, 'NG'))


def test_bad_policies_and_events_are_refused():
    for bad in ({'eligibility_reasons': ()}, {'eligibility_reasons': ('everything',)}, {'eligibility_reasons': ('ng_verdict', 'ng_verdict')},
                {'normal_baseline_fraction': 1.0}, {'normal_baseline_fraction': 0.99}, {'normal_baseline_fraction': True},
                {'max_items_per_window': 0}, {'revision': True}, {'score_spec': None}, {'low_confidence_band': (0.6, 0.4)},
                {'low_confidence_band': (-5, 7)}, {'low_confidence_band': ('a', 'b')}, {'low_confidence_band': (0.1, 0.2, 0.3)},
                {'window_offset_seconds': 3600}, {'eligibility_reasons': ('ng_verdict',), 'score_spec': {'domain': 'nope'}}):
        with pytest.raises(ValueError):
            policy(**bad)
    for bad in ({'event_id': ''}, {'event_id': ' '}, {'size_bytes': -1}, {'lot': ''}, {'product': ' '},
                {'run_ref': {'run_id': 'r'}}, {'run_ref': {'run_id': '', 'node_id': 'n', 'recipe': 'v'}},
                {'run_ref': {'run_id': 'r', 'node_id': 'n', 'recipe': 'v', 'extra': object()}},
                {'captured_at': float('nan')}, {'captured_at': 1e300}, {'verdict': 'ng'}, {'verdict': 'FAIL'},
                {'flags': 'operator_flag'}, {'flags': ('mystery',)}, {'flags': None},
                {'score': {'value': float('nan'), 'spec': PROBABILITY}}, {'score': {'value': '0.5', 'spec': PROBABILITY}},
                {'score': {'spec': PROBABILITY}}, {'score': {'value': 0.5}}):
        with pytest.raises(ValueError):
            event(1, **bad)
    assert policy().ref == policy().ref != policy(revision=2).ref


def test_two_writers_on_one_store_never_spend_a_budget_twice(tmp_path):
    import threading
    path = tmp_path / 's.sqlite3'
    samplers = [IntakeSampler(path, policy(normal_baseline_fraction=0)) for _ in range(2)]
    events = [event(i, 'NG') for i in range(400)]
    errors = []

    def decide(sampler, chunk):
        try:
            for item in chunk:
                sampler.decide(item)
        except Exception as exc:  # asserted below
            errors.append(repr(exc))

    threads = [threading.Thread(target=decide, args=(samplers[n], events[n::2] + events[(n + 1) % 2::2][:50])) for n in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    status = samplers[0].status(0)
    assert status['selected'] == 50 and 'items' in status['quota_exhausted'], status  # six lot/camera keys: the item budget binds
    assert status['selected'] + status['skipped'] == 400, 'each event decided once, its repeats returned their receipt'
    with sqlite3.connect(path) as db:
        usage = db.execute('SELECT items, bytes FROM window_usage WHERE window=0').fetchone()
        stored = db.execute("SELECT COUNT(*), SUM(size_bytes) FROM receipts WHERE window=0 AND decision='selected'").fetchone()
    assert usage == stored, 'the usage counters agree with the receipts'
    assert json.loads(samplers[0].decide(events[0]).key) == ['bracket', 'L0', 'cam0'], 'the key cannot be confused by a / in a name'


# ------------------------------------------------------------------------------------------------ freeze 3

def one_key_hour(ng_every=50, **overrides):
    """One product, lot and camera, one image a second for an hour, 2 % NG (review e06s2 P1-1)."""
    return [event(i, 'NG' if i % ng_every == 7 else 'OK', captured_at=float(i), lot='L', camera='cam', **overrides) for i in range(3600)]


@pytest.mark.parametrize('limits', [{}, {'per_product_lot_camera_quota': 50, 'max_bytes_per_window': 3_000_000, 'max_items_per_window': 50}],
                         ids=['key_quota_binds', 'bytes_bind'])
def test_eligible_images_never_spend_the_baseline_share_of_a_key_or_the_bytes(tmp_path, limits):
    sampler = IntakeSampler(tmp_path / 's.sqlite3', policy(**limits))
    events = one_key_hour(size_bytes=60_000) if limits else one_key_hour()
    receipts = [sampler.decide(e) for e in events]
    baseline = [e.captured_at for e, r in zip(events, receipts) if r.reason == 'baseline']
    assert len(baseline) == 10 and sorted(int(t // 360) for t in baseline) == list(range(10)), 'one baseline image per 6-minute slot'
    assert all(any(q * 900 <= t < (q + 1) * 900 for t in baseline) for q in range(4)), 'a baseline image in every quarter'
    bound = lambda r: set(r.reason.split(':', 1)[1].split('+'))
    candidates = [(e, r) for e, r in zip(events, receipts) if r.decision == 'skipped' and r.reason.startswith('quota_exhausted')
                  and e.verdict == 'OK' and not {'baseline_slot', 'baseline'} & bound(r)]  # baseline candidates whose slot was free
    assert not [r for e, r in candidates if {'product_lot_camera', 'bytes'} & bound(r)], \
        'no baseline candidate is skipped by the key quota or the bytes while its slot is free'
    eligible = [r for r in receipts if r.decision == 'selected' and r.reason != 'baseline']
    if not limits:
        assert len(eligible) == 20, 'the key quota (30) less one place per baseline slot (10)'
    assert sampler.status(0)['selected'] == len(eligible) + 10


def test_a_pruned_window_is_never_decided_again_with_a_fresh_budget(tmp_path):
    """Review e06s2 P2-1: a replay after an outage, or a late image, of a pruned window spent a second budget."""
    sampler = IntakeSampler(tmp_path / 's.sqlite3', policy(normal_baseline_fraction=0, max_items_per_window=3))
    first = [sampler.decide(event(i, 'NG')) for i in range(5)]
    assert [r.decision for r in first] == ['selected'] * 3 + ['skipped'] * 2
    for receipt in first[:3]:
        sampler.mark_retained(receipt.event_id)
    assert sampler.prune(before_window=1) == 5
    replay = [sampler.decide(event(i, 'NG')) for i in range(5)]
    assert {(r.decision, r.reason) for r in replay} == {('skipped', 'window_pruned')}
    assert sampler.decide(event(9, 'NG', captured_at=10.0)).reason == 'window_pruned', 'a never-seen late image of the window'
    assert sampler.status(0)['selected'] == 0, 'nothing was spent'
    assert sampler.decide(event(20, 'NG', captured_at=3600.0)).decision == 'selected', 'a kept window decides as before'
    assert sampler.prune(before_window=0) == 0
    assert sampler.decide(event(21, 'NG', captured_at=5.0)).reason == 'window_pruned', 'the horizon never moves back'


def test_a_resend_in_another_spelling_is_the_same_event(tmp_path):
    """Review e06s2 P3-2: 1 vs 1.0, a repeated flag or a score spec's extra key raised EventIdReused."""
    sampler = IntakeSampler(tmp_path / 's.sqlite3', policy(normal_baseline_fraction=0))
    first = sampler.decide(event(1, 'NG', flags=('operator_flag',), score={'value': 1.0, 'spec': {**PROBABILITY, 'threshold': 0.5}}))
    for score, flags in (({'value': 1, 'spec': {**PROBABILITY, 'threshold': 0.5}}, ('operator_flag',)),
                         ({'value': 1.0, 'spec': {**PROBABILITY, 'threshold': 0.5, 'note': 'ignored by the spec'}}, ('operator_flag',)),
                         ({'value': 1.0, 'spec': {**PROBABILITY, 'threshold': 0.5}}, ('operator_flag', 'operator_flag'))):
        assert sampler.decide(event(1, 'NG', flags=flags, score=score)) == first
    with pytest.raises(EventIdReused):
        sampler.decide(event(1, 'NG', flags=('operator_flag',), score={'value': 0.9, 'spec': PROBABILITY}))


def test_a_revision_cannot_change_the_store_window_geometry(tmp_path):
    """Review e06s2 P3-3: another window length, offset or slot count would get a second budget or misread the slots."""
    path = tmp_path / 's.sqlite3'
    IntakeSampler(path, policy()).decide(event(1, 'NG'))
    for changed in (dict(window_seconds=1800), dict(window_offset_seconds=60), dict(normal_baseline_fraction=0.1)):
        with pytest.raises(ValueError, match='cannot change them'):
            IntakeSampler(path, policy(revision=2, **changed)).decide(event(2, 'NG'))
    assert IntakeSampler(path, policy(revision=2, max_bytes_per_window=9_000_000)).decide(event(3, 'NG')).decision == 'selected'


def test_a_foreign_file_is_refused_and_left_as_it_was(tmp_path):
    """Review e06s2 P3-4: a version-0 file with other tables was adopted, and a refused file was switched to WAL."""
    for version, name in ((0, 'other.sqlite3'), (7, 'newer.sqlite3')):
        path = tmp_path / name
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE something_else(x)')
            db.execute(f'PRAGMA user_version={version}')
        with pytest.raises(ValueError, match='another schema'):
            IntakeSampler(path, policy()).decide(event(1, 'NG'))
        with sqlite3.connect(path) as db:
            assert db.execute('PRAGMA journal_mode').fetchone()[0] != 'wal'
            assert {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {'something_else'}
            assert db.execute('PRAGMA user_version').fetchone()[0] == version


def test_a_concurrent_schema_commit_cannot_mix_an_old_version_with_new_tables(tmp_path, monkeypatch):
    """A real second connection finishes the schema just after the first metadata observation.

    Separate autocommit reads used to combine version 0 with the other writer's new tables and falsely call an
    owned intake store foreign. One read statement must classify a coherent snapshot, followed by the unchanged
    write-transaction recheck. There are no sleeps or retries in this regression.
    """
    path = tmp_path / 's.sqlite3'
    real_connect = sqlite3.connect
    writer = IntakeSampler(path, policy(normal_baseline_fraction=0))
    committed = []
    reader_connection = {'assigned': False}

    class AfterObservation:
        def __init__(self, cursor):
            self.cursor = cursor

        def fetchone(self):
            row = self.cursor.fetchone()
            self.cursor.close()
            # Finish a genuine production decision on a separate connection after this observation, before the
            # caller consumes it. This makes old split reads deterministically see two database snapshots.
            committed.append(writer.decide(event(1, 'NG')))
            return row

    class FirstReader(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            cursor = super().execute(sql, *args, **kwargs)
            if not committed and ('user_version' in sql.lower()):
                return AfterObservation(cursor)
            return cursor

    def connect(database, *args, **kwargs):
        if Path(database) == path and not reader_connection['assigned']:
            reader_connection['assigned'] = True
            kwargs['factory'] = FirstReader
        return real_connect(database, *args, **kwargs)

    from pathlib import Path
    monkeypatch.setattr(sqlite3, 'connect', connect)
    reader = IntakeSampler(path, policy(normal_baseline_fraction=0))
    observed = reader.decide(event(2, 'NG'))
    assert len(committed) == 1 and committed[0].decision == observed.decision == 'selected'
    assert reader.status(0)['selected'] == 2, 'each genuine decision is accounted once, with the same budget'
    with real_connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 3
        assert db.execute('SELECT items FROM window_usage WHERE window=0').fetchone()[0] == 2


@pytest.mark.parametrize('already_selected', [19, 20, 30], ids=['one-place-left', 'at-reduced-cap', 'over-reduced-cap'])
def test_a_reduced_current_item_cap_binds_baseline_without_rewriting_old_receipts(tmp_path, already_selected):
    # Both revisions retain ten slots: the same store geometry legitimately accepts this policy change.
    first = IntakeSampler(tmp_path / 's.sqlite3', policy(seed=1, max_items_per_window=100, normal_baseline_fraction=0.1))
    prior = [first.decide(event(i, 'NG')) for i in range(already_selected)]
    assert all(receipt.decision == 'selected' for receipt in prior)
    reduced = IntakeSampler(first.path, policy(seed=1, revision=2, max_items_per_window=20, normal_baseline_fraction=0.5))
    assert first.policy.geometry() == reduced.policy.geometry()
    # Seed 1 admits both IDs to baseline. Different free slots and a separate key avoid a slot/key/byte refusal
    # masking the total item cap; the first one is allowed exactly when there is one place left.
    new = [reduced.decide(event(100 + n, event_id=name, captured_at=at, size_bytes=1, lot='baseline', camera='base'))
           for n, (name, at) in enumerate((('ordinary2', 3500.0), ('ordinary3', 3100.0)))]
    expected_added = 1 if already_selected < 20 else 0
    assert sum(receipt.decision == 'selected' for receipt in new) == expected_added
    if expected_added:
        assert new[0].reason == 'baseline', 'an exact fit remains available to the ordinary baseline'
    assert all(receipt.reason == 'quota_exhausted:items' for receipt in new[expected_added:])
    assert reduced.status(0)['selected'] == already_selected + expected_added
    assert reduced.decide(event(0, 'NG')) == prior[0], 'a reduced cap never rewrites an existing first receipt'
    before = reduced.status(0)
    assert reduced.decide(event(100, event_id='ordinary2', captured_at=3500.0, size_bytes=1, lot='baseline', camera='base')) == new[0]
    assert reduced.status(0) == before, 'repeating a selected or refused event spends nothing'
    with sqlite3.connect(first.path) as db:
        assert db.execute('SELECT items FROM window_usage WHERE window=0').fetchone()[0] == already_selected + expected_added
        assert db.execute("SELECT COUNT(*) FROM receipts WHERE decision='selected'").fetchone()[0] == already_selected + expected_added


@pytest.mark.parametrize('baseline_bytes', [99, 100, 101], ids=['one-byte-left', 'at-reduced-cap', 'over-reduced-cap'])
def test_a_reduced_byte_cap_binds_eligible_even_when_old_baseline_exceeds_its_new_share(tmp_path, baseline_bytes):
    first = IntakeSampler(tmp_path / 's.sqlite3', policy(seed=1, max_items_per_window=20, normal_baseline_fraction=0.5,
                                                        max_bytes_per_window=10_000, per_product_lot_camera_quota=100))
    ordinary = event(100, event_id='ordinary2', captured_at=3500.0, size_bytes=baseline_bytes, lot='base', camera='base')
    prior = first.decide(ordinary)
    assert prior.decision == 'selected' and prior.reason == 'baseline'
    reduced = IntakeSampler(first.path, policy(seed=1, revision=2, max_items_per_window=20, normal_baseline_fraction=0.5,
                                               max_bytes_per_window=100, per_product_lot_camera_quota=100))
    assert first.policy.geometry() == reduced.policy.geometry()
    ng = event(200, 'NG', size_bytes=1, lot='base', camera='base')
    receipt = reduced.decide(ng)
    fits = baseline_bytes == 99
    assert receipt.decision == ('selected' if fits else 'skipped')
    assert receipt.reason == ('ng_verdict' if fits else 'quota_exhausted:bytes')
    assert reduced.decide(event(201, 'NG', size_bytes=1, lot='base', camera='base')).reason == 'quota_exhausted:bytes'
    assert reduced.decide(ordinary) == prior, 'old baseline usage and its first receipt are never rewritten'
    before = reduced.status(0)
    assert reduced.decide(ng) == receipt and reduced.status(0) == before, 'replay spends nothing'
    assert before['bytes'] == baseline_bytes + int(fits) and before['baseline'] == 1
    with sqlite3.connect(first.path) as db:
        assert db.execute('SELECT bytes FROM window_usage WHERE window=0').fetchone()[0] == baseline_bytes + int(fits)
        assert db.execute("SELECT SUM(size_bytes) FROM receipts WHERE decision='selected'").fetchone()[0] == baseline_bytes + int(fits)


@pytest.mark.parametrize('baseline_count, current_quota', [(1, 1), (1, 2), (2, 2), (3, 2)],
                         ids=['root-exact-cap', 'one-place-left', 'at-reduced-cap', 'over-reduced-cap'])
def test_a_reduced_key_cap_binds_eligible_even_when_old_baseline_exceeds_its_new_share(tmp_path, baseline_count, current_quota):
    first = IntakeSampler(tmp_path / 's.sqlite3', policy(seed=1, max_items_per_window=20, normal_baseline_fraction=0.5,
                                                        per_product_lot_camera_quota=100))
    ordinary = [event(100 + n, event_id=name, captured_at=at, size_bytes=1, lot='base', camera='base')
                for n, (name, at) in enumerate((('ordinary2', 3500.0), ('ordinary3', 3100.0), ('ordinary4', 2700.0)))]
    prior = [first.decide(e) for e in ordinary[:baseline_count]]
    assert all(receipt.decision == 'selected' and receipt.reason == 'baseline' for receipt in prior)
    reduced = IntakeSampler(first.path, policy(seed=1, revision=2, max_items_per_window=20, normal_baseline_fraction=0.5,
                                               per_product_lot_camera_quota=current_quota))
    assert first.policy.geometry() == reduced.policy.geometry()
    ng = event(200, 'NG', size_bytes=1, lot='base', camera='base')
    receipt = reduced.decide(ng)
    fits = baseline_count < current_quota
    assert receipt.decision == ('selected' if fits else 'skipped')
    assert receipt.reason == ('ng_verdict' if fits else 'quota_exhausted:product_lot_camera')
    assert reduced.decide(event(201, 'NG', size_bytes=1, lot='base', camera='base')).reason == 'quota_exhausted:product_lot_camera'
    assert [reduced.decide(e) for e in ordinary[:baseline_count]] == prior, 'the old selected baseline stays intact'
    before = reduced.status(0)
    assert reduced.decide(ng) == receipt and reduced.status(0) == before
    assert before['selected'] == baseline_count + int(fits) and before['baseline'] == baseline_count
    with sqlite3.connect(first.path) as db:
        assert db.execute('SELECT items FROM key_usage WHERE window=0').fetchone()[0] == baseline_count + int(fits)
        assert db.execute("SELECT COUNT(*) FROM receipts WHERE decision='selected'").fetchone()[0] == baseline_count + int(fits)
