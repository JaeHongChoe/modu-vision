"""Shared cooperative cancellation signal for anomaly model fitting."""

from __future__ import annotations

from typing import Callable


class AnomalyFitCancelled(RuntimeError):
    """The owning training job requested cancellation during anomaly fitting."""


def check_fit_cancelled(cancellation_requested: Callable[[], bool] | None) -> None:
    if cancellation_requested is not None and cancellation_requested():
        raise AnomalyFitCancelled("Anomaly fit cancelled by user request")
