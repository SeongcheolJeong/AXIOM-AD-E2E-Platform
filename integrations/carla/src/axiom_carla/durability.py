"""Committed native checkpoints; importing this module never imports CARLA.

The active sink is confined to the worker request thread. Native callbacks must
pass their raw bytes back to that thread before journalling them. A commit error
propagates before the next external mutation. No checkpoint resumes execution.
"""
from contextlib import contextmanager
from contextvars import ContextVar

_active = ContextVar('carla_durable_attempt', default=None)


@contextmanager
def recording(sink):
    token = _active.set(sink)
    try:
        yield
    finally:
        _active.reset(token)


def event(kind: str, data: dict) -> None:
    sink = _active.get()
    if sink is not None:
        sink.event(kind, data)


def blob(kind: str, content: bytes, data: dict) -> None:
    sink = _active.get()
    if sink is not None:
        sink.event(kind, data, content=content)


def checkpoint(payloads, phase: str) -> None:
    sink = _active.get()
    if sink is not None:
        sink.checkpoint(payloads, phase)


def role_name(role: str) -> str:
    sink = _active.get()
    return sink.role_name(role) if sink is not None else role


def enabled() -> bool:
    return _active.get() is not None
