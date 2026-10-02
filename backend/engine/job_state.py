"""Job lifecycle states shared by the persistent ledger (S1-02).

One state machine for every job kind. Existing training records keep their own
status words; ``from_legacy`` maps them so the ledger never invents a state.
"""
from __future__ import annotations

ACTIVE = frozenset({'accepted', 'preparing', 'queued', 'running', 'stopping', 'detached', 'disconnected'})
TERMINAL = frozenset({'completed', 'failed', 'aborted', 'interrupted'})
STATES = ACTIVE | TERMINAL

_MOVES = {
    ('accepted', 'prepare'): 'preparing',
    ('accepted', 'queue'): 'queued',
    ('accepted', 'start'): 'running',
    ('preparing', 'start'): 'running',
    ('queued', 'start'): 'running',
    ('queued', 'claim'): 'running',  # the scheduler claims a queued job under a new attempt
    ('running', 'stop'): 'stopping',
    ('running', 'detach'): 'detached',
    ('running', 'disconnect'): 'disconnected',
    ('stopping', 'detach'): 'detached',
    ('stopping', 'disconnect'): 'disconnected',
    ('detached', 'disconnect'): 'disconnected',
    ('detached', 'reattach'): 'running',
    ('disconnected', 'reattach'): 'running',
    ('disconnected', 'requeue'): 'queued',  # exit confirmed and the budget allows another attempt
}
# Any active job can end; how it ended is the event.
_ENDINGS = {'complete': 'completed', 'fail': 'failed', 'abort': 'aborted', 'interrupt': 'interrupted'}

# Status words used by existing records and receipts.
_LEGACY = {
    'queued': 'queued', 'preparing': 'preparing', 'started': 'running', 'running': 'running',
    'stopping': 'stopping', 'disconnected': 'disconnected', 'completed': 'completed', 'failed': 'failed',
    'aborted': 'aborted', 'stopped': 'aborted', 'cancelled': 'aborted', 'canceled': 'aborted',
    'interrupted': 'interrupted',
}


class IllegalTransition(ValueError):
    """The event does not apply to the job's current state."""


def transition(state: str, event: str) -> str:
    if state in ACTIVE and event in _ENDINGS:
        return _ENDINGS[event]
    try:
        return _MOVES[(state, event)]
    except KeyError:
        raise IllegalTransition(f'{event!r} does not apply to a {state!r} job') from None


def from_legacy(status: str) -> str:
    """Ledger state for an existing record's status word; unknown words are refused."""
    try:
        return _LEGACY[str(status).strip().lower()]
    except KeyError:
        raise ValueError(f'Unknown job status {status!r}') from None
