"""Platform-independent publisher errors and step execution."""

from __future__ import annotations

from typing import Callable, TypeVar


T = TypeVar("T")


class PublisherError(RuntimeError):
    def __init__(self, step: str, message: str):
        super().__init__(message)
        self.step = step
        self.message = message


def run_step(step: str, action: Callable[[], T]) -> T:
    try:
        return action()
    except PublisherError:
        raise
    except Exception as exc:
        raise PublisherError(step, f"{type(exc).__name__}: {exc}") from exc


__all__ = ["PublisherError", "run_step"]
