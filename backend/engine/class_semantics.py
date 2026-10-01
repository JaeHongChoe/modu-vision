"""Versioned normal/defect meaning of model class names.

One resolver serves flow decisions, generated runtime packages, evaluation
analysis and (through a tested port) the renderer. Explicit roles recorded for a
model take precedence over name aliases. A name without a recognised alias is a
defect class, and a name that mixes normal and defect aliases is a defect class
too, so neither can silently become normal. "unknown" exists only as a recorded
or explicit role.

The block between the runtime markers is copied verbatim into each exported
``infer.py``. Keep it self-contained and standard-library only.
"""
from __future__ import annotations

import inspect
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

# --- class semantics runtime: begin ---
import re as _class_semantics_re
import unicodedata as _class_semantics_unicodedata

CLASS_SEMANTICS_VERSION = 1
ROLE_NORMAL = "normal"
ROLE_DEFECT = "defect"
ROLE_UNKNOWN = "unknown"
CLASS_ROLES = (ROLE_NORMAL, ROLE_DEFECT, ROLE_UNKNOWN)

# Whole names only: "0", "bg" or "background" inside a longer name mean nothing.
_NORMAL_NAMES = frozenset({"0", "bg", "background", "nodefect", "nondefect", "nondefective",
                           "notdefective", "defectfree"})
_NORMAL_TOKENS = frozenset({"ok", "good", "normal", "pass", "passed", "정상", "정상품", "양품", "합격"})
_DEFECT_TOKENS = frozenset({"ng", "nok", "defect", "defective", "fail", "failed",
                            "불량", "불량품", "불합격", "비정상", "결함"})
_NEGATIONS = frozenset({"no", "non", "not"})


def class_name_tokens(name):
    """Case-, width- and separator-insensitive tokens of a class name."""
    text = _class_semantics_unicodedata.normalize("NFKC", str(name)).casefold().strip()
    return tuple(token for token in _class_semantics_re.split(r"[\s_\-./]+", text) if token)


def resolve_class_role(name, roles=None):
    """Return (role, basis); basis is explicit, alias, conflict or default."""
    if roles is not None:
        explicit = roles.get(name) if name in roles else roles.get(str(name))
        if explicit in CLASS_ROLES:
            return explicit, "explicit"
    tokens = class_name_tokens(name)
    if "".join(tokens) in _NORMAL_NAMES:
        return ROLE_NORMAL, "alias"
    found = set()
    negate = False
    for token in tokens:
        if token in _NEGATIONS:
            negate = True
            continue
        role = ROLE_NORMAL if token in _NORMAL_TOKENS else ROLE_DEFECT if token in _DEFECT_TOKENS else None
        if role is not None:
            found.add((ROLE_DEFECT if role == ROLE_NORMAL else ROLE_NORMAL) if negate else role)
        negate = False
    if len(found) > 1:
        return ROLE_DEFECT, "conflict"
    if found:
        return found.pop(), "alias"
    return ROLE_DEFECT, "default"


def class_role(name, roles=None):
    return resolve_class_role(name, roles)[0]


def is_defect_class(name, roles=None):
    """True unless the class is normal; unknown classes are never treated as normal."""
    return class_role(name, roles) != ROLE_NORMAL


def normal_class_indices(classes, roles=None):
    return [index for index, name in enumerate(classes) if class_role(name, roles) == ROLE_NORMAL]
# --- class semantics runtime: end ---

SEGMENTATION_TASKS = ("segmentation",)
_SEGMENTATION_RULE = ("Segmentation roles must keep mask channel 0 normal and every positive mask id a defect, "
                      "as mask evidence and blob decisions do")

_RUNTIME_BEGIN = "# --- class semantics runtime: begin ---"
_RUNTIME_END = "# --- class semantics runtime: end ---"
_EXPLICIT_ROLES = (ROLE_NORMAL, ROLE_DEFECT)


def runtime_source() -> str:
    """The self-contained resolver block embedded in generated runtime clients."""
    source = inspect.getsource(inspect.getmodule(runtime_source))
    start, end = source.index(_RUNTIME_BEGIN), source.index(_RUNTIME_END) + len(_RUNTIME_END)
    return source[start:end] + "\n"


def validate_class_roles(classes: Sequence[Any], roles: Any) -> Dict[str, str]:
    """Validate requested explicit roles: normal or defect, for existing class names only."""
    if roles is None:
        return {}
    if not isinstance(roles, Mapping):
        raise ValueError("class_roles must map class names to normal or defect")
    names = {str(name) for name in classes}
    validated = {}
    for name, role in roles.items():
        if not isinstance(name, str) or name not in names:
            raise ValueError(f"class_roles names an unknown class: {name!r}")
        if role not in _EXPLICIT_ROLES:
            raise ValueError(f"class_roles[{name!r}] must be 'normal' or 'defect', got {role!r}")
        validated[name] = role
    return validated


def _is_segmentation(task: Any) -> bool:
    return str(task or "").strip().lower() in SEGMENTATION_TASKS


def _segmentation_violation(names: Sequence[str], roles: Mapping[str, str], *, complete: bool) -> Optional[str]:
    for index, name in enumerate(names):
        expected = ROLE_NORMAL if index == 0 else ROLE_DEFECT
        if name not in roles:
            if complete:
                return f"{_SEGMENTATION_RULE}; {name!r} has no recorded role"
            continue
        if roles[name] != expected:
            return f"{_SEGMENTATION_RULE}; {name!r} cannot be {roles[name]!r}"
    return None


def class_semantics_record(classes: Iterable[Any], roles: Optional[Mapping[str, str]] = None,
                           task: Any = None) -> Dict[str, Any]:
    """Freeze the role of every class with the rule version that produced it.

    Segmentation roles follow mask channels (0 normal, positive ids defect);
    explicit roles may only restate that, and names alone never change it.
    """
    names = [str(name) for name in classes]
    if _is_segmentation(task):
        explicit = dict(roles or {})
        violation = _segmentation_violation(names, explicit, complete=False)
        if violation:
            raise ValueError(violation)
        resolved = {name: (ROLE_NORMAL if index == 0 else ROLE_DEFECT,
                           "explicit" if name in explicit else "segmentation_structure")
                    for index, name in enumerate(names)}
    else:
        resolved = {name: resolve_class_role(name, roles) for name in names}
    return {
        "version": CLASS_SEMANTICS_VERSION,
        "roles": {name: role for name, (role, _) in resolved.items()},
        "basis": {name: basis for name, (_, basis) in resolved.items()},
        "normal_classes": [name for name in names if resolved[name][0] == ROLE_NORMAL],
        "defect_classes": [name for name in names if resolved[name][0] == ROLE_DEFECT],
        "unknown_classes": [name for name in names if resolved[name][0] == ROLE_UNKNOWN],
    }


def training_class_semantics(classes: Sequence[Any], requested_roles: Any = None, task: Any = None) -> Dict[str, Any]:
    """Record for a newly trained model, honouring validated explicit roles."""
    return class_semantics_record(classes, validate_class_roles(classes, requested_roles) or None, task)


def recorded_roles(metadata: Optional[Mapping[str, Any]], task: Any = None,
                   classes: Optional[Sequence[Any]] = None) -> Optional[Dict[str, str]]:
    """Roles frozen in model or package metadata; None when the model predates records.

    A present but unsupported or malformed record raises instead of being
    reinterpreted with today's aliases. Segmentation records must also match
    the mask-channel roles for the model's class order.
    """
    if not isinstance(metadata, Mapping) or metadata.get("class_semantics") is None:
        return None
    record = metadata["class_semantics"]
    if not isinstance(record, Mapping):
        raise ValueError("class_semantics must be an object")
    version = record.get("version")
    if type(version) is not int or version != CLASS_SEMANTICS_VERSION:
        raise ValueError(f"Unsupported class_semantics version {version!r}")
    roles = record.get("roles")
    if not isinstance(roles, Mapping) or not all(
            isinstance(name, str) and role in CLASS_ROLES for name, role in roles.items()):
        raise ValueError("class_semantics roles must map class names to normal, defect or unknown")
    if _is_segmentation(task):
        names = classes if classes is not None else metadata.get("classes")
        if not isinstance(names, (list, tuple)) or not names:
            raise ValueError(f"{_SEGMENTATION_RULE}; the checkpoint class order is missing")
        violation = _segmentation_violation([str(name) for name in names], roles, complete=True)
        if violation:
            raise ValueError(violation)
    return dict(roles)


__all__ = [
    "CLASS_SEMANTICS_VERSION", "CLASS_ROLES", "ROLE_DEFECT", "ROLE_NORMAL", "ROLE_UNKNOWN",
    "class_name_tokens", "class_role", "class_semantics_record", "is_defect_class", "normal_class_indices",
    "recorded_roles", "resolve_class_role", "runtime_source", "training_class_semantics", "validate_class_roles",
]
