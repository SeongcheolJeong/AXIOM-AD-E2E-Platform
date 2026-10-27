"""One bounded evaluation operation in an owned, cancellable CPU process."""
import json
import sys
import time

LIMIT = 32 * 1024 * 1024


def receive():
    line = sys.stdin.buffer.readline(LIMIT + 1)
    if len(line) > LIMIT:
        raise ValueError("Evaluation input exceeds 32 MiB")
    if not line:
        raise ValueError("Evaluation child received no input")
    value = json.loads(line, parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("Nonfinite JSON number")))
    if not isinstance(value, dict):
        raise ValueError("Evaluation request must be an object")
    return value


def send(value):
    data = json.dumps(value, allow_nan=False, separators=(",", ":")) + "\n"
    if len(data.encode()) > LIMIT:
        raise RuntimeError("Evaluation output exceeds 32 MiB")
    sys.stdout.write(data)
    sys.stdout.flush()


def main():
    try:
        if receive() != {}:
            raise ValueError("Unsupported evaluation initialization")
        from .engine import evaluate
        send({"type": "ready"})
        request = receive()
        started = time.monotonic()
        report = evaluate(request)
        report["elapsed_ms"] = (time.monotonic() - started) * 1000
        send({"type": "evaluation", "report": report})
        return 0
    except Exception as error:
        send({"type": "error", "category": "input" if isinstance(error, ValueError) else "execution",
              "error": f"{type(error).__name__}: {str(error)[:1200]}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
