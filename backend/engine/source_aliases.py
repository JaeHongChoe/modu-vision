"""Scope immutable original-root aliases to a verified portable worker input."""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

_ALIASES=ContextVar('verified_source_aliases',default={})


def resolve_source_root(value):
    original=Path(value).expanduser().resolve()
    return _ALIASES.get().get(str(original),original)


@contextmanager
def source_alias_scope(mapping):
    aliases={}
    for original,root in mapping.items():
        path=Path(root)
        if path.is_symlink() or not path.is_dir():raise ValueError('Source alias requires a verified snapshot directory')
        aliases[str(Path(original).expanduser().resolve())]=path.resolve()
    token=_ALIASES.set(aliases)
    try:yield
    finally:_ALIASES.reset(token)
