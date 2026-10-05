"""Allowlisted portable setup; resource bindings and authority never become a template."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path

from backend.engine.artifact_retention import ArtifactRetention
from backend.engine.project_preferences import read_preferences, update_preferences


def _digest(value):
    body = {key: item for key, item in value.items() if key != 'content_sha256'}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def validate_template(value):
    fields = {'format', 'task', 'active_preset', 'tag_colors', 'retention_policy', 'content_sha256'}
    if not isinstance(value, dict) or set(value) != fields or value['format'] != 'modu-project-template-v1':
        raise ValueError('Unsupported project setup template fields or format')
    if value['content_sha256'] != _digest(value):
        raise ValueError('Project setup template checksum mismatch')
    if value['task'] not in ('classification', 'detection', 'segmentation', 'anomaly') or value['active_preset'] not in ('fast', 'precision'):
        raise ValueError('Unsupported template task or preset')
    colors = value['tag_colors']
    if not isinstance(colors, dict) or len(colors) > 1000 or any(
        not isinstance(name, str) or not name.strip() or len(name) > 100
        or not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color)
        for name, color in colors.items()
    ):
        raise ValueError('Invalid template tag palette')
    policy = value['retention_policy']
    if not isinstance(policy, dict) or set(policy) != {'retention_days', 'trash_days', 'quota_bytes'}:
        raise ValueError('Invalid template retention policy')
    if any(type(policy[key]) is not int or not 0 <= policy[key] <= 3650 for key in ('retention_days', 'trash_days')):
        raise ValueError('Invalid template retention days')
    quota = policy['quota_bytes']
    if quota is not None and (type(quota) is not int or not 0 < quota <= 2**63-1):
        raise ValueError('Invalid template quota')
    return json.loads(json.dumps(value))


def export_template(project):
    root = Path(project['project_dir'])
    value = {'format': 'modu-project-template-v1', 'task': project['task'],
             'active_preset': project.get('active_preset', 'fast'),
             'tag_colors': read_preferences(root)['tag_colors'],
             'retention_policy': ArtifactRetention(root).status(project)['policy']}
    value['content_sha256'] = _digest(value)
    return validate_template(value)


def apply_template(root, value):
    value = validate_template(value)
    update_preferences(Path(root), expected_revision=0, actor='project-template', changes={'tag_colors': value['tag_colors']})
    ArtifactRetention(root).configure(**value['retention_policy'])
