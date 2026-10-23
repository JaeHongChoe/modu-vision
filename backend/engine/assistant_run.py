"""The built-in assistant's run identity for one request (docs/product-direction-easy-labeling-agent.md §4.6).

The desktop main process adds ``X-Modu-Assistant-Run`` to the backend writes a person confirmed in the assistant panel.
ProjectContextMiddleware binds it here for that request, and the central audit writers (the image metadata ledger's
``_event`` and the job ledger's ``_event``) record ``via: "assistant"`` with the run id beside the person's own actor.
It is attribution only: the request keeps the person's identity and role, so the header grants nothing, and a forged
one can only mislabel the sender's own write.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
import re
from typing import Iterator, Optional

HEADER = 'x-modu-assistant-run'
RUN_ID = re.compile(r'run_[a-f0-9]{24}')
_CURRENT: ContextVar[Optional[str]] = ContextVar('assistant_run', default=None)


def run_id_from_headers(headers) -> Optional[str]:
    """The run id of a request, or None when the header is absent or not exactly ``run_`` and 24 hex digits."""
    value = (headers.get(HEADER) or '').strip()
    return value if RUN_ID.fullmatch(value) else None


def bind(run_id: Optional[str]) -> Token:
    return _CURRENT.set(run_id if run_id and RUN_ID.fullmatch(run_id) else None)


def reset(token: Token) -> None:
    _CURRENT.reset(token)


@contextmanager
def bound(run_id: Optional[str]) -> Iterator[None]:
    token = bind(run_id)
    try:
        yield
    finally:
        reset(token)


def current_run() -> Optional[str]:
    return _CURRENT.get()


def marks() -> dict:
    """Fields an audit event adds while an assistant run is bound; empty for everything a person does directly."""
    run = _CURRENT.get()
    return {'via': 'assistant', 'assistant_run': run} if run else {}
