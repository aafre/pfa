from __future__ import annotations

import logging
from time import perf_counter

logger = logging.getLogger("pfa")


class TimedOperation:
    """Small local instrumentation boundary; logs metadata, never financial payloads."""

    def __init__(self, name: str, **fields: object):
        self.name = name
        self.fields = fields
        self.started = 0.0

    def __enter__(self) -> TimedOperation:
        self.started = perf_counter()
        return self

    def __exit__(
        self, exception_type: type[BaseException] | None, _: BaseException | None, __: object
    ) -> None:
        logger.info(
            "operation=%s duration_ms=%.1f success=%s fields=%s",
            self.name,
            (perf_counter() - self.started) * 1000,
            exception_type is None,
            self.fields,
        )
