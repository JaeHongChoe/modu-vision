"""A terminal budget outcome waits for its cancellation journal outcome."""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.engine.runtime_budget import RuntimeBudget


@pytest.mark.parametrize('journal_failure', [False, True])
def test_check_waits_for_inflight_cancel_journal_and_preserves_failure(journal_failure):
    entered, release, recorded = threading.Event(), threading.Event(), threading.Event()
    cancel = threading.Event()

    def expire():
        entered.set()
        assert release.wait(5)
        if journal_failure:
            raise OSError('Controlled asynchronous journal failure')
        recorded.set()

    budget = RuntimeBudget(.02, cancel, lambda _: None, expire)
    with budget:
        try:
            assert entered.wait(2)
            with ThreadPoolExecutor(max_workers=1) as pool:
                def observe():
                    try:
                        budget.check()
                    except Exception as error:
                        return type(error), recorded.is_set()
                    raise AssertionError('Expired budget was accepted')
                result = pool.submit(observe)
                premature = threading.Event()
                result.add_done_callback(lambda _: premature.set())
                observed_early = premature.wait(.1)
                release.set()
                outcome = result.result(2)
                assert not observed_early, 'Terminal outcome escaped while cancellation was being recorded'
                assert outcome == (OSError if journal_failure else InterruptedError, not journal_failure)
        finally:
            release.set()


def test_stalled_cancel_journal_fails_closed_instead_of_claiming_acknowledgement():
    entered, release = threading.Event(), threading.Event()
    def stalled():
        entered.set()
        assert release.wait(5)
    budget = RuntimeBudget(.02, threading.Event(), lambda _: None, stalled)
    with budget:
        try:
            assert entered.wait(2)
            with pytest.raises(OSError, match='journal did not finish'):
                budget.check()
        finally:
            release.set()
    assert not budget._thread.is_alive()
