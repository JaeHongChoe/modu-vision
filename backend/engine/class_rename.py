"""Rename or merge class names in the active label set's Studio overlays, after a preview and behind a backup.

Scope (v1, decided 2026-10-22): names only. ``category_id`` values and the mask PNG pixels that encode them never
change, so a merge whose classes carry different ids is refused instead of re-rasterizing masks. Only the active
label set's overlay files (<annotations_dir>/by_dataset/<key>/<stem>.json: annotations[].label and
mask_classes[].name) are rewritten, atomically and inside the metadata transaction that rolls every file back
together with the ledger. LabelMe/COCO/YOLO source files are never written, so an image whose labels for the class live
only in a source file refuses the rename (it would keep the old name and split the class in two) until it is saved as
an edited label; a class that exists only in source files still counts as a merge target. A rename that changes a
class's inspection role (normal/defect, class_semantics) is refused too. Flow versions, model receipts, truth
declarations and pending drafts that name the class are counted as advisories and left alone; the dataset fingerprint
changes by itself, which is what marks models, evaluations and flows built on the old names as stale. A published label
book that names the class refuses the rename: publish_book keeps refusing renamed categories, so the book is never
migrated here. Names are compared by ``name_key`` (NFKC, casefold).
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Callable, Optional

from backend.engine import dataset_metadata as dm
from backend.engine import team_data as td
from backend.engine.annotation_storage import (
    dataset_annotation_dir, reset_request_annotation_root, reset_request_project_root,
    set_request_annotation_root, set_request_project_root,
)
from backend.engine.class_semantics import class_role
from backend.engine.original_owned_UI_ancillary_request import original_dataset_UI_read_text
from backend.engine.source_text import read_source_text

MAX_NAME = 120
MAX_MAPPINGS = 50
# publish_book refuses a renamed category, so a class the label book names cannot be renamed at all (team_data has no
# book migration yet): the only working next step is another class.
LABELBOOK_REFUSAL = '라벨 기준서에 없는 클래스만 바꾸세요'
STALE_PREVIEW = '미리보기 이후 라벨이 바뀌었습니다. 다시 미리보기하세요.'
PUBLISH_FAILED = '편집 라벨 파일을 쓰지 못해 바꾼 내용을 모두 되돌렸습니다. 라벨 파일을 연 프로그램을 닫고 다시 시도하세요.'
TRAINING_BUSY = '학습이 진행 중입니다. 학습이 끝나거나 멈춘 뒤 다시 시도하세요.'
SPECIALIZED_BUSY = '특수 모델 학습(회전 검출·OCR·결함 생성·화질 개선)이 진행 중입니다. 끝나거나 멈춘 뒤 다시 시도하세요.'
AUTOMATED_BUSY = '자동 학습(후보 탐색)이 진행 중입니다. 끝나거나 멈춘 뒤 다시 시도하세요.'
ROLE_NAMES = {'normal': '정상', 'defect': '불량', 'unknown': '미정'}
# Class names also become folder names (exports, defect generation): Windows device names are refused as targets.
_RESERVED = re.compile(r'(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?', re.IGNORECASE)
_SAMPLE = 3
# Flow node params and edge predicates that name classes (flowchart_engine: target_classes, class_names, class_rules,
# class_ids; FlowchartStudio edge predicate class_name). Counted, never rewritten: a rewrite would change the flow's
# semantic identity.
FLOW_CLASS_KEYS = {'class_name', 'class_names', 'target_classes', 'class_rules', 'classes', 'defect_classes'}


class Refused(ValueError):
    """A rename the project's current state does not allow (HTTP 409): the message says why and what to do next."""


class Stale(Refused):
    """The preview no longer matches the files on disk."""


class RollbackFailed(RuntimeError):
    """The apply failed after its backup and some overlay files could not be put back: the backup is the recovery."""

    def __init__(self, backup_version_id: str):
        super().__init__(f'이름 바꾸기에 실패했고 일부 라벨 파일을 되돌리지 못했습니다. 데이터 버전 {backup_version_id}을(를) 복원하세요.')
        self.backup_version_id = backup_version_id


def name_key(name: str) -> str:
    """Comparison key: case, width and composition (NFD names from macOS folders) never make two classes."""
    return unicodedata.normalize('NFKC', name).casefold()


def _clean_name(value: Any, role: str, target: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{role} 클래스 이름을 입력하세요.')
    name = value.strip()
    if len(name) > MAX_NAME or any(ord(character) < 32 for character in name):
        raise ValueError('클래스 이름은 제어 문자 없이 120자 이하여야 합니다. 이름을 줄여서 다시 입력하세요.')
    if target:
        # Zero-width, bidi and line-separator characters make a name that looks exactly like another one.
        if any(unicodedata.category(character) in {'Cc', 'Cf', 'Zl', 'Zp'} for character in name):
            raise ValueError('새 이름에 보이지 않는 문자(제어·폭 없는 공백·방향 표시)가 있습니다. 이름을 직접 다시 입력하세요.')
        if '/' in name or '\\' in name or name.startswith('.') or name.endswith('.') or _RESERVED.fullmatch(name):
            raise ValueError('새 이름에는 / 와 \\ 를 쓸 수 없고 점(.)으로 시작하거나 끝날 수 없으며 CON·NUL 같은 예약 이름도 안 됩니다. '
                             '클래스 이름은 폴더 이름으로도 쓰이니 다른 이름을 입력하세요.')
    return name


def normalize_mapping(mapping: Any) -> list[dict[str, str]]:
    """[{from, to}] with trimmed names: no empty names, no from==to, no name used twice, no chains (A→B and B→C), and
    one spelling per shared target (A→X with B→x would write two classes)."""
    if not isinstance(mapping, list) or not 1 <= len(mapping) <= MAX_MAPPINGS:
        raise ValueError('바꿀 클래스를 1~50개 고르세요.')
    rows: list[dict[str, str]] = []
    sources: set[str] = set()
    targets: dict[str, str] = {}
    for item in mapping:
        if not isinstance(item, dict):
            raise ValueError('바꿀 클래스는 {from, to} 쌍으로 적으세요.')
        source = _clean_name(item.get('from'), '바꿀')
        target = _clean_name(item.get('to'), '새', target=True)
        if source == target:
            raise ValueError(f'"{source}"은(는) 지금 이름과 같습니다. 다른 이름을 입력하세요.')
        if name_key(source) in sources:
            raise ValueError(f'"{source}" 클래스가 두 번 적혔습니다. 한 번만 적으세요.')
        sources.add(name_key(source))
        spelled = targets.setdefault(name_key(target), target)
        if spelled != target:
            raise ValueError(f'"{spelled}"과(와) "{target}"은(는) 대소문자나 글자 폭만 다른 같은 이름입니다. 합칠 이름은 똑같이 적으세요.')
        rows.append({'from': source, 'to': target})
    for row in rows:
        if name_key(row['to']) in sources and name_key(row['to']) != name_key(row['from']):
            raise ValueError(f'"{row["to"]}"은(는) 이 작업에서 함께 바뀌는 이름입니다. 한 번에 한 단계만 바꾸세요.')
    return rows


def _enter(project: dict) -> tuple:
    return (set_request_annotation_root(Path(project['annotations_dir'])),
            set_request_project_root(Path(project['project_dir'])))


def _leave(tokens: tuple) -> None:
    reset_request_project_root(tokens[1])
    reset_request_annotation_root(tokens[0])


def _images(project: dict, source: Path) -> list[Path]:
    from backend.engine.grouped_dataset_views import source_image_paths
    return source_image_paths(source, project['task'], include_unused=True)


def _overlay_path(image: Path, annotations: Path) -> Path:
    return dataset_annotation_dir(image.parent, annotations, use_scope=False) / f'{image.stem}.json'


def _read_overlay(path: Path) -> dict:
    try:
        data = json.loads(original_dataset_UI_read_text(path, encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError(f'편집 라벨 파일을 읽지 못했습니다: {path.name}. 파일을 확인한 뒤 다시 시도하세요.') from exc
    if not isinstance(data, dict) or not isinstance(data.get('annotations'), list):
        raise ValueError(f'편집 라벨 파일 형식이 올바르지 않습니다: {path.name}. 파일을 확인한 뒤 다시 시도하세요.')
    return data


def _category_id(item: dict) -> int:
    value = item.get('category_id', 1)
    return value if isinstance(value, int) and not isinstance(value, bool) else 1


def _bindings(data: dict, bound: dict[str, set[int]], spellings: dict[str, set[str]]) -> None:
    """Collect name_key → category ids and exact spellings from one overlay's shapes and palette."""
    for item in data['annotations']:
        if isinstance(item, dict) and isinstance(item.get('label'), str) and item['label']:
            key = name_key(item['label'])
            bound.setdefault(key, set()).add(_category_id(item))
            spellings.setdefault(key, set()).add(item['label'])
    classes = data.get('mask_classes')
    if isinstance(classes, list):
        for entry in classes:
            if isinstance(entry, dict) and isinstance(entry.get('name'), str) and entry['name']:
                key = name_key(entry['name'])
                if isinstance(entry.get('id'), int) and not isinstance(entry['id'], bool):
                    bound.setdefault(key, set()).add(entry['id'])
                spellings.setdefault(key, set()).add(entry['name'])


def _rewrite(data: dict, targets: dict[str, str]) -> tuple[dict, int, int, dict[str, int]]:
    """A renamed copy of one overlay: (data, shapes renamed, palette entries renamed, shapes by type)."""
    result = copy.deepcopy(data)
    shapes = 0
    palette = 0
    by_type: dict[str, int] = {}
    for item in result['annotations']:
        if not isinstance(item, dict) or not isinstance(item.get('label'), str):
            continue
        new = targets.get(name_key(item['label']))
        if new is None or new == item['label']:
            continue
        item['label'] = new
        shapes += 1
        kind = item.get('type') if isinstance(item.get('type'), str) else 'bbox'
        by_type[kind] = by_type.get(kind, 0) + 1
    classes = result.get('mask_classes')
    if isinstance(classes, list):
        for entry in classes:
            if isinstance(entry, dict) and isinstance(entry.get('name'), str):
                new = targets.get(name_key(entry['name']))
                if new is not None and new != entry['name']:
                    entry['name'] = new
                    palette += 1
    return result, shapes, palette, by_type


def _source_labels(source: Path, image: Path) -> list[str]:
    """Class names a source label file gives this image (adjacent LabelMe first, then COCO/YOLO). Read only.

    Names only: the LabelMe shapes and the rows the COCO/YOLO importer returns carry no category id, and a source-only
    image has no Studio mask whose pixels hold one, so source classes join the merge and spelling checks, not the id
    rule."""
    adjacent = image.with_suffix('.json')
    if adjacent.is_file():
        data = json.loads(read_source_text(adjacent))
        shapes = data.get('shapes') if isinstance(data, dict) else None
        if not isinstance(shapes, list):
            return []
        return [shape['label'] for shape in shapes if isinstance(shape, dict) and isinstance(shape.get('label'), str)]
    from backend.engine.annotation_formats import source_annotations_for_image
    rows = source_annotations_for_image(source, image)
    if not rows:
        return []
    return [row['label'] for row in rows if isinstance(row, dict) and isinstance(row.get('label'), str)]


def _flow_references(value: Any, names: set[str], under: bool = False) -> int:
    count = 0
    if isinstance(value, dict):
        for key, child in value.items():
            if under and isinstance(key, str) and name_key(key) in names:
                count += 1
            count += _flow_references(child, names, under or key in FLOW_CLASS_KEYS)
    elif isinstance(value, list):
        for child in value:
            count += _flow_references(child, names, under)
    elif under and isinstance(value, str) and name_key(value) in names:
        count += 1
    return count


def _read_json(path: Path) -> Optional[Any]:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _advisories(project: dict, ledger: dict, names: set[str]) -> dict:
    """Stores that name the class and are never rewritten: pending drafts, flow versions, model receipts, truth."""
    root = Path(project['project_dir'])
    drafts = {'pending': 0, 'candidates': 0}
    suggestions = root / 'label_suggestions'
    if suggestions.is_dir() and not suggestions.is_symlink():
        for path in sorted(suggestions.glob('suggestion_*.json')):
            item = _read_json(path)
            if not isinstance(item, dict) or item.get('status') != 'pending' or item.get('project_id') != project['id']:
                continue
            hits = sum(1 for candidate in item.get('candidates', []) if isinstance(candidate, dict)
                       and isinstance(candidate.get('annotation'), dict)
                       and isinstance(candidate['annotation'].get('label'), str)
                       and name_key(candidate['annotation']['label']) in names)
            if hits:
                drafts['pending'] += 1
                drafts['candidates'] += hits
    flows = {'versions': 0, 'references': 0}
    versions = root / 'flowcharts' / 'versions'
    if versions.is_dir() and not versions.is_symlink():
        for path in sorted(versions.glob('*.json')):
            document = _read_json(path)
            hits = _flow_references(document, names) if isinstance(document, dict) else 0
            if hits:
                flows['versions'] += 1
                flows['references'] += hits
    models = {'count': 0, 'job_ids': []}
    models_dir = Path(project.get('models_dir') or root / 'models')
    if models_dir.is_dir() and not models_dir.is_symlink():
        for path in sorted(models_dir.glob('job_*/model_meta.json')):
            meta = _read_json(path)
            if not isinstance(meta, dict):
                continue
            classes = meta.get('class_names') if isinstance(meta.get('class_names'), list) else meta.get('classes')
            if isinstance(classes, list) and any(isinstance(name, str) and name_key(name) in names for name in classes):
                models['count'] += 1
                models['job_ids'].append(path.parent.name)
    truth = {'images': 0, 'declarations': 0}
    for row in ledger.get('images', {}).values():
        hits = sum(1 for records in (row.get('explicit_truth') or {}).values() for record in records
                   if isinstance(record, dict) and any(isinstance(name, str) and name_key(name) in names
                                                       for name in (record.get('scope') or {}).get('classes', [])))
        if hits:
            truth['images'] += 1
            truth['declarations'] += hits
    return {'drafts': drafts, 'flows': flows, 'models': models, 'truth': truth}


def _sample(paths: list[str]) -> str:
    shown = ', '.join(paths[:_SAMPLE])
    return f'{shown} 외 {len(paths) - _SAMPLE}장' if len(paths) > _SAMPLE else shown


def _relative(source: Path, image: Path) -> str:
    try:
        return image.relative_to(source).as_posix()
    except ValueError:
        return image.name


def _survey(project: dict, source: Path, annotations: Path, mapping: list[dict[str, str]], ledger: dict,
            actor: str, busy: Optional[str] = None) -> dict:
    """Everything the preview shows and the apply needs, computed once under the ledger lock."""
    targets = {name_key(row['from']): row['to'] for row in mapping}
    names = set(targets)
    bound: dict[str, set[int]] = {}
    spellings: dict[str, set[str]] = {}
    source_spellings: dict[str, set[str]] = {}
    # name_key of a from-name → images whose labels for it live only in a source file (they would keep the old name).
    source_hits: dict[str, list[str]] = {}
    unreadable: list[str] = []
    overlays: dict[str, dict] = {}
    inventory: list[tuple[str, str]] = []
    source_only = {'images': 0, 'unreadable': 0}
    for image in _images(project, source):
        overlay = _overlay_path(image, annotations)
        if not overlay.is_file():
            try:
                labels = [label for label in _source_labels(source, image) if label]
            except (OSError, ValueError, KeyError, TypeError, UnicodeError):
                source_only['unreadable'] += 1
                unreadable.append(_relative(source, image))
                continue
            for label in labels:
                source_spellings.setdefault(name_key(label), set()).add(label)
            hits = {name_key(label) for label in labels} & names
            for key in hits:
                source_hits.setdefault(key, []).append(_relative(source, image))
            if hits:
                source_only['images'] += 1
            continue
        key = str(overlay)
        entry = overlays.get(key)
        if entry is None:
            raw = overlay.read_bytes()
            data = _read_overlay(overlay)
            _bindings(data, bound, spellings)
            rewritten, shapes, palette, by_type = _rewrite(data, targets)
            entry = overlays[key] = {'path': overlay, 'images': [], 'shapes': shapes, 'palette': palette,
                                     'by_type': by_type, 'has_mask': bool(data.get('mask_file')),
                                     'bytes': json.dumps(rewritten, indent=2, ensure_ascii=False).encode('utf-8')
                                     if shapes or palette else None}
            inventory.append((overlay.relative_to(annotations).as_posix(), hashlib.sha256(raw).hexdigest()))
        entry['images'].append(image)
    touched = {key: entry for key, entry in overlays.items() if entry['bytes'] is not None}
    overlay_names = set(spellings)
    # The spellings the rewrite unifies: every overlay spelling of a from-name becomes the one target spelling.
    overlay_spellings = {key: set(values) for key, values in spellings.items()}
    for key, values in source_spellings.items():
        spellings.setdefault(key, set()).update(values)

    conflicts: list[dict[str, str]] = []
    if busy:
        conflicts.append({'code': 'busy', 'message': busy})
    lease = _foreign_leases(ledger, actor)
    if lease:
        conflicts.append({'code': 'lease', 'message': lease})
    books = td._state(ledger)['books']
    book = books[-1] if books else None
    if book:
        for category in book['categories']:
            key = name_key(category['name'])
            bound.setdefault(key, set()).add(category['id'])
            spellings.setdefault(key, set()).add(category['name'])
        for row in mapping:
            if name_key(row['from']) in {name_key(category['name']) for category in book['categories']}:
                conflicts.append({'code': 'labelbook', 'message':
                                  f'라벨 기준서 v{book["version"]}에 있는 "{row["from"]}" 클래스는 이름을 바꿀 수 없습니다. {LABELBOOK_REFUSAL}.'})

    plan = []
    for row in mapping:
        from_key, target_key = name_key(row['from']), name_key(row['to'])
        source_ids = sorted(bound.get(from_key, set()))
        variants = sorted(overlay_spellings.get(from_key, set()))
        existing = spellings.get(target_key, set())
        # A merge: the target is another class (in an overlay, the label book or a source label file), or shapes
        # already carry the target spelling (case variants unify).
        merge = (target_key not in names and bool(existing)) or row['to'] in existing
        contributors = [other['from'] for other in mapping if name_key(other['to']) == target_key]
        combined: set[int] = set()
        for name in contributors:
            combined |= bound.get(name_key(name), set())
        if merge:
            combined |= bound.get(target_key, set())
        ids = ', '.join(str(i) for i in sorted(combined))
        if (merge or len(contributors) > 1) and len(combined) > 1:
            conflicts.append({'code': 'id_collision', 'message':
                              f'"{row["from"]}"→"{row["to"]}" 병합은 클래스 ID가 다릅니다({ids}). '
                              '마스크 픽셀이 ID를 담고 있어 이름만 바꾸는 이 작업으로는 합칠 수 없습니다. 같은 ID의 클래스만 병합하거나 다른 이름을 고르세요.'})
        elif len(variants) > 1 and len(combined) > 1:
            # Spellings that differ only in case (or width, composition) are two classes until the rewrite gives them one name.
            conflicts.append({'code': 'id_collision', 'message':
                              f'"{row["from"]}"과(와) 대소문자 등 표기만 다른 이름({", ".join(variants)})이 모두 "{row["to"]}"(으)로 합쳐지는데 클래스 ID가 다릅니다({ids}). '
                              '마스크 픽셀이 ID를 담고 있어 이름만 바꾸는 이 작업으로는 합칠 수 없습니다. 2단계에서 한 가지 이름만 남긴 뒤 다시 미리보기하세요.'})
        if target_key not in names and existing and row['to'] not in existing:
            spelled = ', '.join(f'"{name}"' for name in sorted(existing))
            conflicts.append({'code': 'spelling', 'message':
                              f'"{row["to"]}"과(와) 대소문자 등 표기만 다른 {spelled} 클래스가 이미 있어 이대로 바꾸면 클래스가 둘로 나뉩니다. '
                              f'합치려면 새 이름 칸의 목록에서 {spelled}을(를) 고르세요.'})
        role_from, role_to = class_role(row['from']), class_role(row['to'])
        if role_from != role_to:
            conflicts.append({'code': 'role_change', 'message':
                              f'"{row["from"]}"({ROLE_NAMES[role_from]})을(를) "{row["to"]}"({ROLE_NAMES[role_to]})(으)로 바꾸면 검사 판정 역할이 바뀝니다. '
                              '이름만 바꾸는 이 작업에서는 역할이 같은 이름을 고르세요.'})
        hits = source_hits.get(from_key, [])
        if from_key not in overlay_names:
            conflicts.append({'code': 'nothing_to_do', 'message':
                              f'"{row["from"]}"은(는) 원본 라벨 파일에만 있습니다({_sample(hits)}). 원본 파일은 바꾸지 않으니 '
                              '그 이미지들을 2단계에서 열어 저장해 편집 라벨로 만든 뒤 다시 미리보기하세요.' if hits else
                              f'편집 라벨에 "{row["from"]}" 클래스가 없습니다. 원본 파일에만 있는 라벨은 바꾸지 않습니다. 클래스 이름을 확인하세요.'})
        elif hits:
            conflicts.append({'code': 'source_only', 'message':
                              f'원본 라벨 파일에만 "{row["from"]}"이(가) 있는 이미지 {len(hits)}장({_sample(hits)})은 이름이 바뀌지 않아 클래스가 둘로 나뉩니다. '
                              '그 이미지들을 2단계에서 열어 저장해 편집 라벨로 만든 뒤 다시 미리보기하세요.'})
        plan.append({'from': row['from'], 'to': row['to'], 'merge': merge,
                     'source_ids': source_ids, 'target_ids': sorted(bound.get(target_key, set())) if merge else [],
                     'existing_spellings': sorted(existing) if merge else [],
                     'source_spellings': variants, 'role_from': role_from, 'role_to': role_to})
    if unreadable:
        conflicts.append({'code': 'source_only', 'message':
                          f'원본 라벨 파일을 읽지 못한 이미지 {len(unreadable)}장({_sample(unreadable)})은 바꿀 클래스가 있는지 알 수 없어 '
                          '클래스가 둘로 나뉠 수 있습니다. 그 원본 라벨 파일을 고치거나 2단계에서 열어 저장해 편집 라벨로 만든 뒤 다시 미리보기하세요.'})
    if not touched and not any(conflict['code'] == 'nothing_to_do' for conflict in conflicts):
        conflicts.append({'code': 'nothing_to_do', 'message':
                          '편집 라벨 중 바꿀 것이 없습니다. 원본 파일에만 있는 라벨은 바꾸지 않습니다. 클래스 이름을 확인하세요.'})

    by_type: dict[str, int] = {}
    for entry in touched.values():
        for kind, count in entry['by_type'].items():
            by_type[kind] = by_type.get(kind, 0) + count
    # Every rewritten image goes back to review (needs_review, reviews invalidated): say how many approvals that drops.
    rows = ledger.get('images', {})
    approved = sum(1 for entry in touched.values() for image in entry['images']
                   if (rows.get(_relative(source, image)) or {}).get('workflow_state') == 'approved')
    signature = hashlib.sha256(json.dumps({
        'labelset_id': project.get('active_labelset_id', 'default'), 'mapping': mapping,
        'overlays': sorted(inventory), 'book_sha256': book['sha256'] if book else None,
        'source_only': source_only, 'source_names': sorted(name for values in source_spellings.values() for name in values),
    }, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()
    return {
        'labelset_id': project.get('active_labelset_id', 'default'),
        'mapping': plan,
        'overlays': {'files': len(touched), 'shapes': sum(entry['shapes'] for entry in touched.values()),
                     'shapes_by_type': by_type, 'palette_entries': sum(entry['palette'] for entry in touched.values()),
                     'images': sum(len(entry['images']) for entry in touched.values()),
                     'images_with_masks': sum(len(entry['images']) for entry in touched.values() if entry['has_mask'])},
        'review': {'approved': approved},
        'source_only': source_only,
        'labelbook': {'version': book['version'] if book else None,
                      'names_old_class': any(conflict['code'] == 'labelbook' for conflict in conflicts)},
        **_advisories(project, ledger, names),
        'conflicts': conflicts,
        'preview_sha256': signature,
        '_touched': touched,
    }


def _public(view: dict) -> dict:
    return {key: value for key, value in view.items() if not key.startswith('_')}


def _refuse_task(project: dict) -> None:
    if project.get('task') == 'classification':
        raise ValueError('분류 프로젝트의 클래스는 폴더 이름입니다. 폴더 이름을 바꾸고 다시 가져오거나, 2단계에서 이미지마다 클래스를 지정하세요.')


def preview(project: dict, source: Path, mapping: Any, actor: str = 'operator') -> dict:
    """Counts per store and conflicts for the mapping, including what would refuse the apply right now (a running job,
    another actor's edit lease); nothing is written."""
    _refuse_task(project)
    mapping = normalize_mapping(mapping)
    actor = td._actor(actor)
    busy = active_work(project)
    root = Path(project['project_dir']).resolve()
    annotations = Path(project['annotations_dir'])
    source = Path(source).resolve()
    tokens = _enter(project)
    try:
        with dm.metadata_transaction(root, source, annotations) as ledger:
            return {**_public(_survey(project, source, annotations, mapping, ledger, actor, busy)), 'applied': False}
    finally:
        _leave(tokens)


def active_work(project: dict) -> Optional[str]:
    """Why a bulk rewrite must wait: training, this project's specialized/family training (OCR, rotation, defect
    generation, rotated detection, enhancement, automated search), a live draft batch or a labeling job."""
    from backend.api.routes_training import training_job_manager
    if training_job_manager.get_active_job() is not None:
        return TRAINING_BUSY
    from backend.engine import specialized_training_jobs as specialized
    models = Path(project.get('models_dir') or Path(project['project_dir']) / 'models')
    # OCR, rotation and defect generation keep models/<task>/<job id>/job.json (routes_ocr, routes_rotation,
    # routes_defect_gan); read_job settles a job no live process of this app owns as interrupted.
    for task in ('ocr', 'rotation', 'defect_gan'):
        root = models / task
        if root.is_symlink() or not root.is_dir():
            continue
        for path in sorted(root.glob('*/job.json')):
            if path.parent.is_symlink():
                continue
            try:
                record = specialized.read_job(root, path.parent.name)
            except (OSError, ValueError, KeyError):
                continue
            if record.get('status') in specialized.ACTIVE:
                return SPECIALIZED_BUSY
    # Rotated detection, enhancement and automated search keep their live jobs in memory, keyed by this project.
    from backend.api import routes_automated_training as automated, routes_enhancement as enhancement
    from backend.api import routes_rotated_detection as rotated
    project_key = str(Path(project['project_dir']).resolve())
    with rotated._LOCK:
        if any(key[0] == project_key and job.status in specialized.ACTIVE for key, job in rotated._JOBS.items()):
            return SPECIALIZED_BUSY
    enhancement_root = (models / 'enhancement').resolve()
    with enhancement._JOB_LOCK:
        if any(Path(key).parent == enhancement_root for key in enhancement._CANCEL_EVENTS):
            return SPECIALIZED_BUSY
    models_key = str(models.resolve())
    with automated._LOCK:
        if any(key[0] == models_key for key in automated._EVENTS):
            return AUTOMATED_BUSY
    from backend.api import routes_label_suggestions as suggestions
    with suggestions._BATCH_LOCK:
        if suggestions._project_batch_key(project) in suggestions._LIVE_BATCHES:
            return '일괄 초안 만들기가 진행 중입니다. 끝나거나 취소한 뒤 다시 시도하세요.'
    from backend.engine import labeling_tasks
    if labeling_tasks.active(project):
        return '라벨 작업(후보 일괄 생성·특징 학습)이 진행 중입니다. 끝나거나 취소한 뒤 다시 시도하세요.'
    return None


def _foreign_leases(ledger: dict, actor: str) -> Optional[str]:
    """With team editing on, another actor's live edit lease means their labels are mid-edit: wait, never bypass."""
    settings = td._state(ledger)['settings']
    if not settings['editing_enabled']:
        return None
    now = time.time()
    holders: dict[str, int] = {}
    for row in ledger['images'].values():
        lease = (row.get('team') or {}).get('edit_lease')
        if lease and lease.get('expires_at', 0) > now and lease.get('owner') != actor:
            holders[lease['owner']] = holders.get(lease['owner'], 0) + 1
    if not holders:
        return None
    names = ', '.join(f'{owner}({count}장)' for owner, count in sorted(holders.items()))
    return f'팀 편집이 켜져 있고 다른 작업자가 편집 중입니다: {names}. 편집 잠금이 풀린 뒤 다시 시도하세요.'


def apply(project: dict, source: Path, mapping: Any, actor: str, expected_preview_sha256: Any,
          snapshot: Callable[[str, str], dict]) -> dict:
    """Back up (``snapshot(name, note)`` → data version), then rewrite the overlays and record a ledger event per image.

    The caller holds the version lock and the project mutation fence; this holds the metadata transaction, so a failure
    after the backup restores every overlay file and leaves the ledger unchanged (the backup version stays). A write
    failure is refused in Korean; a failure whose rollback also failed raises RollbackFailed naming the backup.
    """
    _refuse_task(project)
    mapping = normalize_mapping(mapping)
    actor = td._actor(actor)
    if not isinstance(expected_preview_sha256, str) or re.fullmatch(r'[0-9a-f]{64}', expected_preview_sha256) is None:
        raise ValueError('미리보기를 먼저 하세요. 미리보기 결과의 확인값이 없습니다.')
    busy = active_work(project)
    if busy:
        raise Refused(busy)
    root = Path(project['project_dir']).resolve()
    annotations = Path(project['annotations_dir'])
    source = Path(source).resolve()
    tokens = _enter(project)
    backup_id: Optional[str] = None
    try:
        with dm.metadata_transaction(root, source, annotations) as ledger:
            view = _survey(project, source, annotations, mapping, ledger, actor)
            if view['conflicts']:
                raise Refused(view['conflicts'][0]['message'])
            if view['preview_sha256'] != expected_preview_sha256:
                raise Stale(STALE_PREVIEW)
            touched = view['_touched']
            # Ledger rows first, while the files still carry the old names: a later _ensure must not mistake this
            # rewrite for an unexplained external edit.
            rows: dict[str, dict] = {}
            for entry in touched.values():
                for image in entry['images']:
                    rows[str(image)] = dm._ensure(ledger, root, source, image, annotations)
            summary = ' · '.join(f"{row['from']}→{row['to']}" for row in mapping)
            backup = snapshot(f'클래스 이름 변경 전 · {summary}'[:120], f'Before renaming classes: {summary}'[:2000])
            backup_id = backup['id']
            dm.annotation_file_transaction(ledger).publish({entry['path']: entry['bytes'] for entry in touched.values()})
            totals = {'files': view['overlays']['files'], 'shapes_total': view['overlays']['shapes']}
            affected = []
            for entry in touched.values():
                for image in entry['images']:
                    row = rows[str(image)]
                    row['workflow_state'] = 'needs_review'
                    row['reviewer'] = None
                    row['annotation_hash'] = dm._annotation_hash(root, source, image, annotations)
                    td.annotation_written(row, actor)
                    dm._event(row, actor, 'class_renamed', {
                        'mapping': [dict(item) for item in mapping], 'shapes': entry['shapes'],
                        'palette_entries': entry['palette'], **totals, 'backup_version_id': backup['id']})
                    affected.append(row['relative_path'])
            return {**_public(view), 'applied': True, 'backup_version_id': backup['id'], 'actor': actor,
                    'affected_images': sorted(affected)}
    except OSError as exc:
        # After the backup, an OSError reaching here means the transaction put every overlay and the ledger back (a
        # rollback that failed arrives as RuntimeError instead). The message never echoes the absolute path.
        if backup_id is None:
            raise
        raise Refused(PUBLISH_FAILED) from exc
    except RuntimeError as exc:
        if backup_id is None:
            raise
        raise RollbackFailed(backup_id) from exc
    finally:
        _leave(tokens)
