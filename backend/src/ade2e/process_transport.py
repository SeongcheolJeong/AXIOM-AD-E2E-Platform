"""Bounded JSON child transport shared by optional compute adapters.

A managed subprocess supports cancellation and pipe/operation deadlines; it is
not an OS security sandbox. Native libraries are owned by optional children.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import tempfile
import threading
import time

from .adapters import AdapterCancelled

class ManagedJsonProcess:
    def __init__(self, command, initial_message, should_cancel, *, timeout=30,
                 max_response_bytes=2 * 1024 * 1024, max_request_bytes=12 * 1024 * 1024,
                 input_error=ValueError, execution_error=RuntimeError, cancel_error=AdapterCancelled):
        self.command, self.initial_message = list(command), initial_message
        self.should_cancel, self.timeout = should_cancel, timeout
        self.max_response_bytes, self.max_request_bytes = max_response_bytes, max_request_bytes
        self.input_error, self.execution_error, self.cancel_error = input_error, execution_error, cancel_error
        self.process = self.stderr = self.reader = self.writer = None
        self.messages = queue.Queue(maxsize=2)
        self.closed = False

    def _put(self, message):
        while not self.closed:
            try:
                self.messages.put(message, timeout=.05)
                return
            except queue.Full:
                continue

    def __enter__(self):
        if self.should_cancel():
            raise self.cancel_error("Inference cancelled before model initialization")
        environment = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "LANG", "TMPDIR") if key in os.environ}
        environment.update(OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1", PYTHONUNBUFFERED="1")
        self.stderr = tempfile.TemporaryFile()
        try:
            self.process = subprocess.Popen(self.command,
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr,
                                            env=environment, start_new_session=True)
        except BaseException:
            self.close()
            raise

        def read_messages():
            try:
                while not self.closed:
                    line = self.process.stdout.readline(self.max_response_bytes + 1)
                    if len(line) > self.max_response_bytes:
                        self._put({"type": "error", "category": "execution", "error": "Inference response exceeds its bound"})
                        return
                    if not line:
                        self._put({"type": "error", "category": "execution", "error": "Inference child exited without a result"})
                        return
                    self._put(json.loads(line))
            except (ValueError, OSError) as error:
                self._put({"type": "error", "category": "execution", "error": f"Malformed inference transport: {type(error).__name__}"})

        self.reader = threading.Thread(target=read_messages, name="managed-child-output", daemon=True)
        self.reader.start()
        try:
            deadline = time.monotonic() + self.timeout
            self._send(self.initial_message, self.should_cancel, deadline)
            ready = self._receive(self.should_cancel, deadline)
            if ready.get("type") != "ready":
                raise self.execution_error("Inference child did not acknowledge model initialization")
            return self
        except BaseException:
            self.close()
            raise

    def _check_operation(self, should_cancel, deadline):
        try:
            if should_cancel():
                raise self.cancel_error("Inference child stopped after cancellation request")
            if time.monotonic() >= deadline:
                raise self.execution_error(f"Inference child exceeded its {self.timeout:g} second operation deadline and was stopped")
        except BaseException:
            self.close()
            raise

    def _send(self, message, should_cancel, deadline):
        if self.closed or self.process is None or self.process.poll() is not None:
            raise self.execution_error("The inference session is no longer running")
        payload = json.dumps(message, allow_nan=False, separators=(",", ":")).encode() + b"\n"
        if len(payload) > self.max_request_bytes:
            raise self.input_error("Child request exceeds its byte bound")
        result = queue.Queue(maxsize=1)

        def write_message():
            try:
                self.process.stdin.write(payload)
                self.process.stdin.flush()
                result.put(None)
            except BaseException as error:
                result.put(error)

        # A child can stall before consuming stdin. Never block the lease and
        # cancellation loop on a full pipe; terminating the child releases its
        # pipe and lets close() drain the owned writer before acknowledging.
        self.writer = threading.Thread(target=write_message, name="managed-child-input", daemon=True)
        self.writer.start()
        while True:
            self._check_operation(should_cancel, deadline)
            try:
                error = result.get(timeout=.05)
            except queue.Empty:
                continue
            self.writer.join()
            if error is not None:
                self.close()
                raise self.execution_error("The inference child disconnected during input transfer") from error
            return

    def _receive(self, should_cancel, deadline=None):
        deadline = time.monotonic() + self.timeout if deadline is None else deadline
        while True:
            self._check_operation(should_cancel, deadline)
            try:
                message = self.messages.get(timeout=.05)
            except queue.Empty:
                continue
            if not isinstance(message, dict):
                self.close()
                raise self.execution_error("Invalid inference response envelope")
            if message.get("type") == "error":
                error_type = self.input_error if message.get("category") == "input" else self.execution_error
                raise error_type(str(message.get("error", "Inference failed"))[:1500])
            return message

    def request(self, message, should_cancel):
        deadline = time.monotonic() + self.timeout
        self._send(message, should_cancel, deadline)
        return self._receive(should_cancel, deadline)

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            if self.writer:
                self.writer.join(timeout=2)
            for stream in (self.process.stdin, self.process.stdout):
                stream.close()
        if self.reader:
            self.reader.join(timeout=1)
        if self.stderr:
            self.stderr.close()

    def __exit__(self, *_args):
        self.close()
