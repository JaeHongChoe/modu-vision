"""Scoped labeling guidance, owned edit leases and two-person review.

The existing metadata transaction is the single serialization boundary for
policy, reviews and annotation writes. Source files remain read-only. Two-person
review examines one saved annotation; it is not independent double annotation.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import secrets
import time
import uuid
from pathlib import Path

from backend.engine import dataset_metadata as dm


DEFAULT_SETTINGS = {
    'revision': 1, 'editing_enabled': False, 'review_enabled': False,
    'required_reviews': 1, 'prevent_self_review': True, 'approved_only_training': False,
}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def _actor(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 100:
        raise ValueError('작업자 이름을 1~100자로 입력하세요.')
    return value.strip()


def _state(ledger):
    state = ledger.setdefault('team_data', {'schema_version': 1, 'settings': dict(DEFAULT_SETTINGS), 'books': []})
    if state.get('schema_version') != 1 or not isinstance(state.get('books'), list):
        raise ValueError('Unsupported team-data schema')
    settings = state.setdefault('settings', {})
    for key, value in DEFAULT_SETTINGS.items(): settings.setdefault(key, value)
    return state


def ensure_image_team(row):
    value = row.setdefault('team', {})
    for key, default in {'assignment': None, 'edit_lease': None, 'reviews': [],
                         'review_status': 'pending', 'annotation_actor': None, 'history': []}.items():
        value.setdefault(key, copy.deepcopy(default))
    return value


def public_image(row):
    result = copy.deepcopy(row)
    state = ensure_image_team(result)
    if state.get('edit_lease'):
        state['edit_lease'] = {key: state['edit_lease'][key] for key in ('owner', 'expires_at')}
    return result


def invalidate_reviews(row, reason, *, clear_lease=False):
    state = ensure_image_team(row)
    if state['reviews'] or state['review_status'] != 'pending':
        state['history'].append({'action': 'invalidated', 'at': dm._now(), 'reason': reason,
                                 'reviews': copy.deepcopy(state['reviews'])})
    state['reviews'] = []
    state['review_status'] = 'pending'
    state.pop('adjudication', None)
    if clear_lease: state['edit_lease'] = None


def policy_sha256(ledger):
    settings = _state(ledger)['settings']
    return _digest({key: settings[key] for key in DEFAULT_SETTINGS if key != 'revision'})


def _binding(ledger, row):
    books = _state(ledger)['books']; book = books[-1] if books else None
    return _digest({'image_uuid': row['image_uuid'], 'content_hash': row['content_hash'],
                    'annotation_hash': row.get('annotation_hash'), 'mask_hash': row.get('mask_hash'),
                    'book_sha256': book['sha256'] if book else None,
                    'policy_sha256': policy_sha256(ledger)})


def _check_revision(row, expected):
    if expected != row['revision']: raise dm.RevisionConflict(public_image(row))


def _context(project, source):
    root = Path(project['project_dir']).resolve(); source = Path(source).resolve()
    configured = project.get('source_dataset_dir')
    if not configured or Path(configured).resolve() != source:
        raise ValueError('Team work must use the active project source')
    return root, source, Path(project['annotations_dir'])


def _current_row(ledger, project, source, identifier):
    old = dm._find(ledger, identifier)
    row = dm._ensure(ledger, Path(project['project_dir']), source, old['file_path'], Path(project['annotations_dir']))
    ensure_image_team(row)
    return row


def _inventory(project, source):
    from backend.engine.grouped_dataset_views import source_image_paths
    return source_image_paths(source, project['task'], include_unused=True)


def workspace(project, source):
    root, source, annotations = _context(project, source)
    with dm.metadata_transaction(root, source, annotations) as ledger:
        state = _state(ledger); books = state['books']
        return {'scope': {'project_id': project['id'], 'source': str(source),
                           'labelset_id': project.get('active_labelset_id', 'default')},
                'book': copy.deepcopy(books[-1]) if books else None,
                'book_history': copy.deepcopy(books), 'settings': copy.deepcopy(state['settings']),
                'members': [], 'actor': None}


def _clean_categories(source, categories):
    if not isinstance(categories, list) or not 1 <= len(categories) <= 255:
        raise ValueError('라벨북에 1~255개 클래스를 등록하세요.')
    ids = set(); names = set(); result = []
    for item in categories:
        if not isinstance(item, dict): raise ValueError('Invalid labelbook category')
        identifier = item.get('id'); name = item.get('name', '').strip()
        if isinstance(identifier, bool) or not isinstance(identifier, int) or not 0 <= identifier <= 254:
            raise ValueError('Class ID must be an integer from 0 to 254')
        if not name or len(name) > 120 or identifier in ids or name.casefold() in names:
            raise ValueError('Class IDs and names must be unique and nonempty')
        ids.add(identifier); names.add(name.casefold())
        color = item.get('color', '#22d3ee')
        if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
            raise ValueError('Class color must be a hexadecimal RGB color')
        row = {'id': identifier, 'name': name, 'color': color}
        for field in ('definition', 'inclusion', 'exclusion', 'annotation_guidance'):
            value = item.get(field, '')
            if not isinstance(value, str) or len(value) > 10000: raise ValueError('Label guidance must be under 10000 characters')
            row[field] = value.strip()
        examples = item.get('examples', [])
        if not isinstance(examples, list) or len(examples) > 100: raise ValueError('Limit each class to 100 examples')
        row['examples'] = []
        for example in examples:
            if not isinstance(example, dict): raise ValueError('Invalid example reference')
            relative = Path(example.get('relative_path', ''))
            if (relative.is_absolute() or not relative.parts or '..' in relative.parts
                    or '\\' in str(relative) or str(relative) in {'.', ''}):
                raise ValueError('Example images require a source-relative path')
            image = source / relative
            if not image.resolve().is_relative_to(source) or not image.is_file():
                raise ValueError('Example image is outside the active source')
            dm._visible_path(source, image)
            digest = dm._hash(image)
            if example.get('content_hash') and example['content_hash'] != digest:
                raise ValueError('Example source changed; select the image again')
            caption = example.get('caption', '')
            if not isinstance(caption, str) or len(caption) > 2000: raise ValueError('Example caption is too long')
            row['examples'].append({'relative_path': relative.as_posix(), 'content_hash': digest, 'caption': caption.strip()})
        result.append(row)
    return result


def publish_book(project, source, expected_version, actor, title, categories):
    actor = _actor(actor); root, source, annotations = _context(project, source)
    if not isinstance(title, str) or not title.strip() or len(title) > 200: raise ValueError('라벨북 이름을 입력하세요.')
    clean = _clean_categories(source, categories)
    with dm.metadata_transaction(root, source, annotations) as ledger:
        state = _state(ledger); books = state['books']; version = books[-1]['version'] if books else 0
        if expected_version != version: raise dm.RevisionConflict({'book_version': version})
        prior_ids = {entry['id']: entry['name'] for book in books for entry in book['categories']}
        prior_names = {name.casefold(): identifier for identifier, name in prior_ids.items()}
        for entry in clean:
            if ((entry['id'] in prior_ids and prior_ids[entry['id']] != entry['name'])
                    or (entry['name'].casefold() in prior_names and prior_names[entry['name'].casefold()] != entry['id'])):
                raise ValueError('기존 클래스 ID와 이름은 유지하세요. 이름 변경은 별도 라벨 마이그레이션이 필요합니다.')
        book = {'id': f'book_{uuid.uuid4().hex}', 'version': version + 1,
                'title': title.strip(), 'categories': clean, 'actor': actor, 'published_at': dm._now()}
        book['sha256'] = _digest(book); books.append(book)
        for row in ledger['images'].values():
            invalidate_reviews(row, 'labelbook_changed'); row['workflow_state'] = 'needs_review'; row['reviewer'] = None
            dm._event(row, actor, 'labelbook_changed', {'book_version': book['version']})
        return copy.deepcopy(book)


def update_settings(project, source, expected_revision, actor, changes):
    actor = _actor(actor); root, source, annotations = _context(project, source)
    if not isinstance(changes, dict) or not changes or set(changes) - (set(DEFAULT_SETTINGS) - {'revision'}):
        raise ValueError('Unknown or empty team settings')
    for key, value in changes.items():
        if key == 'required_reviews':
            if isinstance(value, bool) or value not in {1, 2}: raise ValueError('Choose one-person or two-person review')
        elif not isinstance(value, bool): raise ValueError('Policy switches must be booleans')
    with dm.metadata_transaction(root, source, annotations) as ledger:
        state = _state(ledger); settings = state['settings']
        if expected_revision != settings['revision']: raise dm.RevisionConflict(copy.deepcopy(settings))
        review_changed = any(key in changes and changes[key] != settings[key] for key in ('review_enabled', 'required_reviews', 'prevent_self_review'))
        policy_changed = any(settings[key] != value for key,value in changes.items())
        settings.update(changes); settings['revision'] += 1
        for row in ledger['images'].values():
            if review_changed or policy_changed and (settings['review_enabled'] or row.get('team',{}).get('reviews')):
                invalidate_reviews(row, 'review_policy_changed'); row['workflow_state'] = 'needs_review'; row['reviewer'] = None
                dm._event(row, actor, 'review_policy_changed', {'settings_revision': settings['revision']})
        return copy.deepcopy(settings)


def image_state(project, source, image_path):
    root, source, annotations = _context(project, source)
    with dm.metadata_transaction(root, source, annotations) as ledger:
        _state(ledger)
        row = dm._ensure(ledger, root, source, image_path, annotations)
        return public_image(row)


def assign_image(project, source, identifier, expected_revision, actor, assignee, priority=50, members=None):
    actor = _actor(actor); root, source, annotations = _context(project, source)
    if isinstance(priority, bool) or not isinstance(priority, int) or not 0 <= priority <= 100: raise ValueError('Priority must be from 0 to 100')
    if assignee is not None:
        assignee = _actor(assignee)
        if members is not None and assignee not in members: raise ValueError('담당자는 현재 프로젝트 멤버여야 합니다.')
    with dm.metadata_transaction(root, source, annotations) as ledger:
        row = _current_row(ledger, project, source, identifier); _check_revision(row, expected_revision)
        state = ensure_image_team(row); lease = state['edit_lease']
        if lease and lease['expires_at'] > time.time() and lease['owner'] != assignee:
            raise ValueError('편집 잠금이 해제된 후 담당자를 변경하세요.')
        state['assignment'] = {'assignee': assignee, 'priority': priority, 'assigned_by': actor, 'assigned_at': dm._now()} if assignee else None
        dm._event(row, actor, 'work_assigned', {'assignee': assignee, 'priority': priority})
        return {'image': public_image(row)}


def _ttl(seconds):
    if isinstance(seconds, bool) or not isinstance(seconds, int) or not 30 <= seconds <= 900:
        raise ValueError('Edit lease duration must be from 30 to 900 seconds')
    return seconds


def acquire_lease(project, source, identifier, expected_revision, actor, ttl_seconds=120):
    actor = _actor(actor); ttl_seconds = _ttl(ttl_seconds); root, source, annotations = _context(project, source)
    with dm.metadata_transaction(root, source, annotations) as ledger:
        row = _current_row(ledger, project, source, identifier); _check_revision(row, expected_revision)
        state = ensure_image_team(row); lease = state['edit_lease']; assignment = state['assignment']
        if assignment and assignment['assignee'] != actor: raise ValueError('다른 담당자에게 배정된 이미지입니다.')
        if lease and lease['expires_at'] > time.time(): raise dm.RevisionConflict(public_image(row))
        token = secrets.token_urlsafe(32)
        state['edit_lease'] = {'owner': actor, 'token_hash': hashlib.sha256(token.encode()).hexdigest(), 'expires_at': time.time() + ttl_seconds}
        dm._event(row, actor, 'edit_lease_acquired', {'owner': actor})
        return {'image': public_image(row), 'lease_token': token}


def require_lease(ledger, row, actor, token):
    state = ensure_image_team(row); lease = state['edit_lease']; actor = _actor(actor)
    if (not lease or lease['expires_at'] <= time.time() or lease['owner'] != actor
            or not isinstance(token, str) or not secrets.compare_digest(lease['token_hash'], hashlib.sha256(token.encode()).hexdigest())):
        raise ValueError('편집 잠금이 없거나 만료·변경되었습니다. 담당자와 최신 라벨을 확인하고 잠금을 다시 받으세요.')


def renew_lease(project, source, identifier, expected_revision, actor, lease_token, ttl_seconds=120):
    actor = _actor(actor); ttl_seconds = _ttl(ttl_seconds); root, source, annotations = _context(project, source)
    with dm.metadata_transaction(root, source, annotations) as ledger:
        row = _current_row(ledger, project, source, identifier); _check_revision(row, expected_revision); require_lease(ledger, row, actor, lease_token)
        row['team']['edit_lease']['expires_at'] = time.time() + ttl_seconds
        # Content revision is stable while renewing an unchanged editor's lease.
        # Keep its token stable too: an in-flight owned save must not fail just
        # because the same editor extended the lease. Reacquisition rotates it.
        return {'image': public_image(row), 'lease_token': lease_token}


def release_lease(project, source, identifier, expected_revision, actor, lease_token):
    actor = _actor(actor); root, source, annotations = _context(project, source)
    with dm.metadata_transaction(root, source, annotations) as ledger:
        row = _current_row(ledger, project, source, identifier); _check_revision(row, expected_revision); require_lease(ledger, row, actor, lease_token)
        row['team']['edit_lease'] = None; dm._event(row, actor, 'edit_lease_released', {})
        return {'image': public_image(row)}


def guard_annotation_write(ledger, row, actor, lease_token, annotations=None):
    settings = _state(ledger)['settings']
    if settings['editing_enabled']: require_lease(ledger, row, actor, lease_token)
    books = _state(ledger)['books']
    if books and (settings['editing_enabled'] or settings['review_enabled']) and annotations is not None:
        classes = {entry['id']: entry['name'] for entry in books[-1]['categories']}
        for entry in annotations:
            data = entry.model_dump() if hasattr(entry, 'model_dump') else entry
            if classes.get(data.get('category_id', 1)) != data.get('label'):
                raise ValueError('라벨의 클래스 ID·이름이 활성 라벨북과 다릅니다. 라벨북을 먼저 갱신하세요.')


def annotation_written(row, actor):
    invalidate_reviews(row, 'annotation_changed')
    ensure_image_team(row)['annotation_actor'] = _actor(actor)


def guard_metadata_approval(ledger, row, changes):
    if changes.get('workflow_state') != 'approved' or not _state(ledger)['settings']['review_enabled']: return
    state = ensure_image_team(row); binding = _binding(ledger, row)
    adjudication = state.get('adjudication')
    valid = (state['review_status'] == 'approved' and
             ((adjudication and adjudication.get('binding_sha256') == binding and adjudication.get('decision') == 'approve')
              or (len(state['reviews']) >= _state(ledger)['settings']['required_reviews']
                  and all(v['decision'] == 'approve' and v['binding_sha256'] == binding for v in state['reviews']))))
    if not valid: raise ValueError('현재 라벨에 필요한 검토를 완료하세요. 검토 정책을 우회해 승인할 수 없습니다.')


def _decision(value, reason):
    if value not in {'approve', 'reject'}: raise ValueError('Choose approve or reject')
    if not isinstance(reason, str) or len(reason) > 4000: raise ValueError('Review reason must be under 4000 characters')
    if value == 'reject' and not reason.strip(): raise ValueError('반려 이유를 입력하세요.')
    return reason.strip()


def review_image(project, source, identifier, expected_revision, actor, decision, reason=''):
    actor = _actor(actor); reason = _decision(decision, reason); root, source, annotations = _context(project, source)
    with dm.metadata_transaction(root, source, annotations) as ledger:
        row = _current_row(ledger, project, source, identifier); _check_revision(row, expected_revision)
        settings = _state(ledger)['settings']; state = ensure_image_team(row)
        if not row.get('annotation_hash') and not row.get('mask_hash'): raise ValueError('라벨을 저장한 후 검토하세요.')
        if settings['prevent_self_review'] and state['annotation_actor'] and actor.casefold() == state['annotation_actor'].casefold():
            raise ValueError('자신이 수정한 라벨은 다른 사람이 검토해야 합니다.')
        binding = _binding(ledger, row)
        if any(v['binding_sha256'] != binding for v in state['reviews']): invalidate_reviews(row, 'review_binding_changed')
        if any(v['actor'].casefold() == actor.casefold() for v in state['reviews']): raise ValueError('같은 검토자는 현재 라벨을 두 번 검토할 수 없습니다.')
        if state['review_status'] in {'approved', 'disputed'} or state['review_status']=='rejected' and len(state['reviews'])>=settings['required_reviews']:
            raise ValueError('완료·불일치·반려된 검토는 라벨 수정 또는 판정 조정 후 진행하세요.')
        vote = {'actor': actor, 'decision': decision, 'reason': reason, 'at': dm._now(), 'binding_sha256': binding}
        state['reviews'].append(vote)
        decisions = {v['decision'] for v in state['reviews']}
        status = 'disputed' if len(decisions) > 1 else 'rejected' if decisions == {'reject'} else 'approved' if len(state['reviews']) >= settings['required_reviews'] else 'pending'
        state['review_status'] = status
        row['workflow_state'] = 'approved' if status == 'approved' else 'needs_review'; row['reviewer'] = actor if status == 'approved' else None
        state['history'].append({'action': 'reviewed', **vote})
        dm._event(row, actor, 'team_review', {'decision': decision, 'review_status': status})
        return {'image': public_image(row)}


def adjudicate_image(project, source, identifier, expected_revision, actor, decision, reason):
    actor = _actor(actor); reason = _decision(decision, reason)
    if not reason: raise ValueError('판정 조정 이유를 입력하세요.')
    root, source, annotations = _context(project, source)
    with dm.metadata_transaction(root, source, annotations) as ledger:
        row = _current_row(ledger, project, source, identifier); _check_revision(row, expected_revision); state = ensure_image_team(row)
        if state['review_status'] not in {'disputed', 'rejected'}: raise ValueError('불일치·반려된 검토만 판정 조정할 수 있습니다.')
        if len(state['reviews'])<_state(ledger)['settings']['required_reviews']:
            raise ValueError('필요한 인원의 검토를 완료한 후 판정을 조정하세요.')
        if _state(ledger)['settings']['prevent_self_review'] and (state['annotation_actor'] or '').casefold() == actor.casefold():
            raise ValueError('자신이 수정한 라벨은 다른 사람이 판정 조정해야 합니다.')
        record = {'action': 'adjudicated', 'actor': actor, 'decision': decision, 'reason': reason,
                  'at': dm._now(), 'binding_sha256': _binding(ledger, row)}
        state['history'].append(record); state['adjudication'] = record
        state['review_status'] = 'approved' if decision == 'approve' else 'rejected'
        row['workflow_state'] = 'approved' if decision == 'approve' else 'needs_review'; row['reviewer'] = actor if decision == 'approve' else None
        dm._event(row, actor, 'team_adjudication', {'decision': decision, 'reason': reason})
        return {'image': public_image(row)}


def work_queue(project, source, assignee=None, state=None, offset=0, limit=100):
    root, source, annotations = _context(project, source)
    if state not in {None, 'pending', 'approved', 'rejected', 'disputed', 'unworked', 'needs_review'}: raise ValueError('Unknown work queue state')
    if offset < 0 or not 1 <= limit <= 500: raise ValueError('Invalid work queue page')
    with dm.metadata_transaction(root, source, annotations) as ledger:
        _state(ledger); rows = []
        for path in _inventory(project, source):
            row = dm._ensure(ledger, root, source, path, annotations); team = ensure_image_team(row)
            if assignee and (team.get('assignment') or {}).get('assignee') != assignee: continue
            status = 'approved' if row['workflow_state'] == 'approved' else team['review_status']
            if state and status != state and row['workflow_state'] != state: continue
            rows.append(public_image(row))
        rows.sort(key=lambda row: (-((row['team'].get('assignment') or {}).get('priority', 0)), row['relative_path']))
        return {'items': rows[offset:offset + limit], 'total': len(rows), 'offset': offset, 'limit': limit}


def _training_state(ledger, project, source, root, annotations):
    settings = _state(ledger)['settings']; counts = dict.fromkeys(('approved', 'pending', 'rejected', 'disputed', 'unused', 'gold', 'total', 'eligible'), 0)
    eligibility = []; blockers = []; books = _state(ledger)['books']; book = books[-1] if books else None
    unmapped=set()
    # Gold samples of the label review (E05) leave training unless the dataset policy keeps them, as in the loaders.
    from backend.engine.annotation_quality import gold_image_paths, gold_receipt
    quality_scope = {'project_dir': str(root), 'source_dataset_dir': str(source)}
    gold = gold_image_paths(quality_scope)
    for path in _inventory(project, source):
        row = dm._ensure(ledger, root, source, path, annotations); team = ensure_image_team(row); counts['total'] += 1
        if row.get('usage_state') == 'not_used': counts['unused'] += 1; continue
        if str(Path(path).resolve()) in gold: counts['gold'] += 1; continue
        status = 'approved' if row['workflow_state'] == 'approved' else team['review_status']
        counts[status if status in {'approved', 'rejected', 'disputed'} else 'pending'] += 1
        if not settings['approved_only_training'] or row['workflow_state'] == 'approved':
            if book:
                from backend.engine.annotation_storage import dataset_annotation_dir
                overlay=dataset_annotation_dir(path.parent,annotations,use_scope=False)/f'{path.stem}.json'
                if overlay.is_file():labels=json.loads(overlay.read_text(encoding='utf-8')).get('annotations',[])
                else:
                    from backend.engine.grouped_dataset_views import _annotations
                    labels=_annotations(source,path)[0] or []
                classes={entry['id']:entry['name'] for entry in book['categories']}
                for label in labels:
                    if classes.get(label.get('category_id',1))!=label.get('label'):
                        unmapped.add(label.get('label') or '이름 없는 클래스')
            eligibility.append({'image_uuid': row['image_uuid'], 'relative_path': row['relative_path'],
                                'content_hash': row['content_hash'], 'annotation_hash': row.get('annotation_hash'), 'mask_hash': row.get('mask_hash')})
    counts['eligible'] = len(eligibility)
    if not eligibility: blockers.append('학습에 사용할 이미지가 없습니다. 라벨 승인·미사용 상태를 확인하세요.')
    if unmapped:blockers.append('활성 라벨북과 맞지 않는 저장 클래스: '+', '.join(sorted(unmapped)))
    if settings['review_enabled'] and settings['required_reviews'] == 2 and counts['pending']:
        blockers.append(f"두 사람 검토 대기 이미지 {counts['pending']}장")
    # Unreviewed images are reported, while eligible images can still be trained.
    return {'ready': bool(eligibility) and not unmapped, 'counts': counts, 'blockers': blockers,
            'book_version': book['version'] if book else 0, 'book_sha256': book['sha256'] if book else None,
            'policy_sha256': policy_sha256(ledger), 'eligibility_sha256': _digest(eligibility),
            'eligible_image_uuids': [row['image_uuid'] for row in eligibility],
            'eligibility': eligibility, 'settings': copy.deepcopy(settings), 'gold': gold_receipt(quality_scope)}


def training_readiness(project, source):
    root, source, annotations = _context(project, source)
    with dm.metadata_transaction(root, source, annotations) as ledger:
        result = _training_state(ledger, project, source, root, annotations)
        result.pop('eligibility'); result.pop('settings')
        return result


def training_binding(project, source):
    root, source, annotations = _context(project, source)
    with dm.metadata_transaction(root, source, annotations) as ledger:
        result = _training_state(ledger, project, source, root, annotations)
        mismatches = [reason for reason in result['blockers'] if reason.startswith('활성 라벨북')]
        if mismatches:
            raise ValueError(' · '.join(mismatches))
        return {key: result[key] for key in ('book_version', 'book_sha256', 'policy_sha256', 'eligibility_sha256', 'eligibility', 'settings', 'gold')} | {
            'scope': {'project_id': project['id'], 'source': str(source), 'labelset_id': project.get('active_labelset_id', 'default')}}


def training_excluded_paths(project, source, annotations=None):
    """Read the current policy through the same source/labelset metadata lock."""
    project = dict(project)
    if annotations is not None: project['annotations_dir'] = str(annotations)
    root, source, annotations = _context(project, source)
    with dm.metadata_transaction(root, source, annotations) as ledger:
        settings = _state(ledger)['settings']
        if not settings['approved_only_training']: return set()
        # Missing metadata is unapproved, including newly arrived source images.
        excluded = set()
        from backend.engine.dataset_loaders import SUPPORTED_IMAGE_EXTENSIONS
        for path in source.rglob('*'):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS or root.is_relative_to(source) and path.is_relative_to(root): continue
            row = dm._ensure(ledger, root, source, path, annotations)
            if row['workflow_state'] != 'approved': excluded.add(str(path.resolve()))
        return excluded
