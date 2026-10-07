"""Read original acceptance and requested work scope without changing either gate.

The authorized Windows-only scope relief is never a test pass. Software slices,
curated action scenarios and external acceptance conditions are separate measures.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import check_action_evidence, check_service_plan

USER_INSTRUCTION = '원도우 테스트는 안해도 되니깐 남은 잡들 진행해죠'
WINDOWS_ONLY_PARENTS = frozenset({'S6-02', 'S7-03'})
PHASE_COUNTS = (9, 10, 10, 10, 14, 10, 11, 8)
PARENT_IDS = {f'S{phase}-{number:02}' for phase, count in enumerate(PHASE_COUNTS)
              for number in range(1, count + 1)}
LEGACY_IDS = {f'F{number:03}' for number in range(1, 124)} | {
    f'U{number:03}' for number in range(1, 34)}
PARENT_GATES = {'implementation', 'contract', 'gui', 'persist', 'reopen',
                'failure', 'handoff', 'windows_native', 'target_execution'}
INPUTS = {
    'program': 'docs/service-upgrade-program.json',
    'actions': 'docs/service-action-evidence.json',
    'scope': 'docs/implementation-ledger/user-scope-decisions.json',
}

# These are follow-up boundaries, not evidence that code or acceptance has passed.
FOLLOWUPS = {
    'S1-08': ('Preserve explicit installed-home adoption, bounded history snapshots and launch-ownership migration guards; review unsupported-worker boundaries.',
              ['Installed native target migration and independent acceptance.']),
    'S5-01': ('Collect source-bound independent service lifecycle controls.',
              ['Dedicated target account, reboot/device permissions and GPU/camera operation.']),
    'S6-02': ('', ['Windows native installation/removal is waived for requested work only.']),
    'S6-03': ('Preserve inert pack API/GUI bindings qualified at 5284df6; qualify additional declared providers/targets with new source-bound evidence.',
              ['Other supported providers/devices, representative quality and native license/publisher acceptance.']),
    'S6-04': ('Preserve implemented portable app/database cutover/recovery and fail-closed durable launch ownership; complete native supervisor/caller, authenticated handshake, process-tree reconciliation, known-image execution and OS installer adapter.',
              ['Real publisher signature, OS installer, installed home and installed-app known-image handoff.']),
    'S6-05': ('Preserve the protected manual release-candidate validation workflow and bind complete hosted CI to the exact newly published source.',
              ['Publication and hosted runner completion; older source success cannot qualify newer source.']),
    'S6-06': ('Keep checksum, source and unsigned/channel state explicit.',
              ['Real publisher/signing keys and release channel operating policy.']),
    'S7-01': ('Execute and bind remaining curated scenarios; register omitted menus and shortcuts.',
              ['Full feature target acceptance remains separate from individual UI scenarios.']),
    'S7-02': ('Preserve source-bound model lifecycle and saved multi-model package controls.',
              ['Human-reviewed representative classification, anomaly, OCR, OBB, enhancement and GAN truth/quality.']),
    'S7-03': ('', ['Windows native usage QA is waived for requested work only.']),
    'S7-04': ('Collect current local fault matrix and independent team conflict/restart controls.',
              ['Physical target faults and real publisher-signed native application update acceptance.']),
    'S7-05': ('Preserve source-bound GUI metadata paging/search controls; distinguish 100000 metadata rows with three real images from actual target photo/decoder/resource qualification.',
              ['Terminal receipt for the same 72-hour run and actual target RAM/disk/p95/tact qualification.']),
    'S7-06': ('Preserve simulator and stale-truth/permission release guards.',
              ['Actual camera/PLC/MES, product/Lot reviewed truth and process-owner escape/overkill approval.']),
    'S7-07': ('Review exact source, artifacts, action coverage, recovery and known issues.',
              ['Real signatures, native library terms, supported targets, independent final review and pilot.']),
    'S7-08': ('Keep pilot and support/backup procedures ready to record actual observations.',
              ['First-use human pilot, maintenance responsibility and measured service policy/SLA.']),
}


def _load(root, relative):
    file = root / relative
    if any(path.is_symlink() for path in (file, *file.parents)):
        raise ValueError('Linked report input is refused: ' + relative)
    with file.open('rb') as stream:
        raw = stream.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError('Report input exceeds its size bound: ' + relative)
    value = json.loads(raw, object_pairs_hook=check_service_plan._unique_json_object)
    if not isinstance(value, dict):
        raise ValueError('Report input must be a JSON object: ' + relative)
    return value, raw


def _counts(label, saved, actual):
    if (not isinstance(saved, dict) or set(saved) != set(actual)
            or any(type(value) is not int or value < 0 for value in saved.values())
            or saved != actual):
        raise ValueError(label + ': stale or malformed counts')


def _validate_program(program):
    if type(program.get('schema_version')) is not int or program['schema_version'] != 1:
        raise ValueError('program scope: invalid schema version')
    rows, legacy = program.get('requirements'), program.get('legacy_coverage')
    for label, values, key, expected in (
            ('requirements', rows, 'id', PARENT_IDS),
            ('legacy_coverage', legacy, 'legacy_id', LEGACY_IDS)):
        if (not isinstance(values, list) or any(not isinstance(row, dict) for row in values)
                or len(values) != len(expected)
                or any(not isinstance(row.get(key), str) for row in values)
                or {row[key] for row in values} != expected):
            raise ValueError(label + ' scope: preserve all original 82/156 IDs exactly once')
    for row in rows:
        gates = row.get('acceptance_state')
        if (not isinstance(gates, dict) or set(gates) != PARENT_GATES
                or any(value not in ('verified', 'pending') for value in gates.values())):
            raise ValueError(row['id'] + ': invalid original acceptance gates')
        if row.get('status') not in ('planned', 'in_progress', 'verification_pending', 'accepted'):
            raise ValueError(row['id'] + ': invalid original status')
        if row['status'] == 'accepted' and any(value != 'verified' for value in gates.values()):
            raise ValueError(row['id'] + ': original pending acceptance cannot be relabelled accepted')
    implementation = dict(Counter(row['acceptance_state']['implementation'] for row in rows))
    implementation = {key: implementation.get(key, 0) for key in ('verified', 'pending')}
    status = dict(Counter(row['status'] for row in rows))
    accepted = status.get('accepted', 0)
    summary = program.get('coverage_summary')
    expected = {'feature_requirements': 123, 'upgrade_requirements': 33, 'mapped_ids': 156,
                'unmapped_ids': 0, 'new_work_packages': 82,
                'new_plan_accepted_work_packages': accepted}
    if not isinstance(summary, dict):
        raise ValueError('program scope: missing coverage summary')
    _counts('coverage_summary', {key: summary.get(key) for key in expected}, expected)
    progress = program.get('execution', {}).get('progress', {})
    if not isinstance(progress, dict):
        raise ValueError('program scope: missing execution progress')
    _counts('parent_implementation', progress.get('parent_implementation'), implementation)
    _counts('parent_status', progress.get('parent_status'), status)
    slices = progress.get('slice_records')
    if (not isinstance(slices, list) or any(not isinstance(row, dict) for row in slices)
            or any(not isinstance(row.get('slice_id'), str) or not row['slice_id']
                   or row.get('task') not in PARENT_IDS for row in slices)
            or len({row['slice_id'] for row in slices}) != len(slices)):
        raise ValueError('program scope: invalid software slice records')
    for field in ('merged_extension_slices', 'additional_improved_parent_areas'):
        if type(progress.get(field)) is not int or progress[field] < 0:
            raise ValueError(field + ': malformed registry claim count')
    return rows, implementation, accepted, slices


def _validate_scope(scope):
    if (set(scope) != {'schema_version', 'kind', 'authority', 'decisions'}
            or type(scope.get('schema_version')) is not int or scope['schema_version'] != 1
            or scope.get('kind') != 'requested_work_scope_only'):
        raise ValueError('scope: invalid requested-work record')
    if scope['authority'] != {
            'kind': 'direct_user_instruction', 'instruction': USER_INSTRUCTION,
            'recorded_on': '2026-10-07'}:
        raise ValueError('scope: authority must match the retained direct user instruction')
    decisions = scope.get('decisions')
    if not isinstance(decisions, list) or len(decisions) != 2:
        raise ValueError('scope: only the two Windows-only parent waivers are authorized')
    for decision in decisions:
        if (not isinstance(decision, dict)
                or set(decision) != {'id', 'decision', 'scope', 'original_acceptance_preserved'}
                or not isinstance(decision.get('id'), str)
                or decision.get('id') not in WINDOWS_ONLY_PARENTS
                or decision.get('decision') != 'waived_for_requested_work'
                or decision.get('scope') != 'windows_native_only'
                or decision.get('original_acceptance_preserved') is not True):
            raise ValueError('scope: waiver cannot broaden the user instruction or become a pass')
    if {row['id'] for row in decisions} != WINDOWS_ONLY_PARENTS:
        raise ValueError('scope: duplicated or missing Windows-only parent waiver')
    return decisions


def build_report(root=ROOT):
    root = Path(root).resolve(strict=True)
    loaded = {name: _load(root, relative) for name, relative in INPUTS.items()}
    program, actions, scope = (loaded[name][0] for name in ('program', 'actions', 'scope'))
    rows, implementation, accepted, slices = _validate_program(program)
    decisions = _validate_scope(scope)
    pending = sorted(row['id'] for row in rows if row['acceptance_state']['implementation'] == 'pending')
    waived = sorted(set(pending) & WINDOWS_ONLY_PARENTS)
    required = sorted(set(pending) - WINDOWS_ONLY_PARENTS)
    action_report = check_action_evidence.check(program, actions, root)
    by_id = {row['id']: row for row in rows}
    remaining = []
    for identifier in pending:
        followup, conditions = FOLLOWUPS.get(identifier, (
            'Review this newly pending parent against its exact code and execution evidence.',
            ['Consult the original parent acceptance assertions; no external condition is waived.']))
        remaining.append({'id': identifier, 'title': by_id[identifier]['title'],
                          'requested_work': 'waived' if identifier in waived else 'required',
                          'software_followup': followup, 'external_conditions': conditions,
                          'recorded_remaining': by_id[identifier].get('remaining', [])})
    for name, relative in INPUTS.items():
        if _load(root, relative)[1] != loaded[name][1]:
            raise ValueError('Report input changed during read: ' + relative)
    return {
        'schema_version': 1, 'kind': 'remaining_requested_work_not_acceptance',
        'ok': action_report['ok'],
        'inputs': {name: {'path': relative, 'sha256': hashlib.sha256(loaded[name][1]).hexdigest()}
                   for name, relative in INPUTS.items()},
        'original_program': {'work_packages': len(rows), 'legacy_features': len(program['legacy_coverage']),
                             'implementation_verified': implementation['verified'],
                             'implementation_pending': implementation['pending'],
                             'accepted_work_packages': accepted},
        'requested_work': {'waived_pending_parents': waived, 'waived_pending_count': len(waived),
                           'still_required_pending_parents': required,
                           'still_required_pending_count': len(required)},
        'scope_decisions': [{**row, 'original_windows_native':
                             by_id[row['id']]['acceptance_state']['windows_native']} for row in decisions],
        'software_slices': {'registry_records': len(slices),
                            'claimed_merged_count': program['execution']['progress']['merged_extension_slices'],
                            'claimed_additional_parent_areas': program['execution']['progress']['additional_improved_parent_areas'],
                            'parent_ids': sorted({row['task'] for row in slices}),
                            'overlap_parent_counts': True,
                            'records_are_registry_claims_not_independent_acceptance': True},
        'action_evidence': action_report, 'remaining_parents': remaining,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        report = build_report(args.root)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
