"""Bounded worker polling diagnostics and optional private rotating logs."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import stat
import time


class PrivateRotatingFileHandler(RotatingFileHandler):
    """Keep newly created logs private, including files created after rotation."""

    def _open(self):
        descriptor = os.open(self.baseFilename, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise ValueError("Worker log must be an owner-only regular file (mode 0600)")
            return os.fdopen(descriptor, self.mode, encoding=self.encoding, errors=self.errors)
        except BaseException:
            os.close(descriptor)
            raise

    def format(self, record):
        message = super().format(record)
        # A single oversized traceback must not bypass the per-file bound.
        encoded = message.encode(self.encoding, self.errors or "replace")
        limit = min(self.maxBytes - len(self.terminator.encode(self.encoding)), 64 * 1024)
        if len(encoded) > limit:
            suffix = " [log record truncated]"
            message = encoded[:max(0, limit - len(suffix.encode(self.encoding)))].decode(self.encoding, "ignore") + suffix
        return message

    def shouldRollover(self, record):
        if self.stream is None:
            self.stream = self._open()
        self.stream.seek(0, os.SEEK_END)
        size = len((self.format(record) + self.terminator).encode(self.encoding, self.errors or "replace"))
        return self.stream.tell() + size > self.maxBytes


def configure_worker_logging(log_file: Path | None = None, *, max_bytes: int = 10 * 1024 * 1024,
                             backups: int = 3) -> None:
    if not 1024 <= max_bytes <= 1024 * 1024 * 1024 or not 1 <= backups <= 20:
        raise ValueError("max_bytes must be 1024..1073741824 and backups 1..20")
    handlers = [PrivateRotatingFileHandler(log_file, maxBytes=max_bytes, backupCount=backups,
                                          encoding="utf-8")] if log_file else [logging.StreamHandler()]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=handlers, force=True)


def run_polling_loop(poll, stopping, *, poll_interval: float, once: bool = False,
                     logger: logging.Logger, clock=None) -> int:
    """Retry failed polls with backoff, without repeatedly printing tracebacks.

    This retries queue polling only. Dispatch/recovery semantics remain owned by
    run_once/run_claim; an engine operation is never resubmitted here.
    """
    if not math.isfinite(poll_interval) or not 0.05 <= poll_interval <= 60:
        raise ValueError("poll_interval must be finite and within 0.05..60 seconds")
    clock = clock or time.monotonic
    failures, retry_delay, last_summary = 0, poll_interval, 0.0
    while not stopping.is_set():
        try:
            result = poll()
        except ImportError as error:
            logger.error("Worker polling stopped: required module/dependency is unavailable (%s). "
                         "Repair this environment and restart the worker; no automatic retry.", error)
            return 1
        except Exception as error:
            failures += 1
            now = clock()
            if failures == 1:
                logger.exception("Worker polling failed; next poll in %.2fs; no engine is automatically retried",
                                 retry_delay)
                last_summary = now
            elif now - last_summary >= 60:
                logger.error("Worker polling still failing: %d consecutive failures (%s); next poll in %.2fs; "
                             "repeated tracebacks suppressed", failures, type(error).__name__, retry_delay)
                last_summary = now
            if once:
                return 1
            stopping.wait(retry_delay)
            retry_delay = min(60.0, retry_delay * 2)
            continue
        if failures:
            logger.info("Worker polling recovered after %d consecutive failures", failures)
            failures, retry_delay = 0, poll_interval
        if result:
            logger.info("Job %s: %s", result.id, result.status)
        if once:
            return 0
        stopping.wait(poll_interval)
    return 0
