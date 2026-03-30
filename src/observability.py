"""Shared request-scoped observability helpers."""
from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from time import perf_counter
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_request_context: ContextVar[Dict[str, Any]] = ContextVar("request_context", default={})


def now_ms() -> float:
    """High-resolution clock for latency measurement."""
    return perf_counter() * 1000.0


def set_request_context(**values: Any) -> Token:
    """Replace the current request context."""
    return _request_context.set({k: v for k, v in values.items() if v is not None})


def update_request_context(**values: Any) -> None:
    """Merge values into the current request context."""
    current = dict(_request_context.get())
    current.update({k: v for k, v in values.items() if v is not None})
    _request_context.set(current)


def reset_request_context(token: Token) -> None:
    """Restore the previous request context."""
    _request_context.reset(token)


def get_request_context() -> Dict[str, Any]:
    """Return a copy of the current request context."""
    return dict(_request_context.get())


def _format_fields(fields: Dict[str, Any]) -> str:
    parts = []
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, float):
            parts.append(f"{key}={value:.2f}")
        else:
            parts.append(f"{key}={value}")
    return " ".join(parts)


def log_timing(
    stage: str,
    start_ms: float,
    *,
    logger_instance: Optional[logging.Logger] = None,
    level: int = logging.INFO,
    **fields: Any,
) -> float:
    """Log a stage duration and return the elapsed milliseconds."""
    elapsed_ms = now_ms() - start_ms
    payload = {"stage": stage, "duration_ms": elapsed_ms, **get_request_context(), **fields}
    active_logger = logger_instance or logger
    active_logger.log(level, "timing %s", _format_fields(payload))
    return elapsed_ms
