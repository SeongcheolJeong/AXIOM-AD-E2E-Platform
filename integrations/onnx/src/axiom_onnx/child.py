"""Bounded line transport to a single trusted local ONNX model."""
import base64
import json
from pathlib import Path
import sys


def send(value):
    sys.stdout.write(json.dumps(value, allow_nan=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def receive(limit):
    line = sys.stdin.buffer.readline(limit + 1)
    if len(line) > limit:
        raise ValueError("Inference request exceeds its transport bound")
    if not line:
        return None
    result = json.loads(line)
    if not isinstance(result, dict):
        raise ValueError("Inference request must be an object")
    return result


def main():
    from ade2e.inference_runtime import InferenceInputError
    from .engine import Engine
    try:
        configuration = receive(1024 * 1024)
        engine = Engine(Path(sys.argv[1]), configuration["definition"], configuration["sha256"], configuration["seed"])
        send({"type": "ready"})
    except Exception as error:
        send({"type": "error", "category": "input", "error": f"Model initialization failed: {str(error)[:1200]}"})
        return 1
    while True:
        try:
            request = receive(12 * 1024 * 1024)
            if request is None:
                return 0
            png = base64.b64decode(request.pop("png"), validate=True)
            prediction = engine.infer(png, **request)
            send({"type": "prediction", "prediction": prediction.model_dump(mode="json")})
        except Exception as error:
            send({"type": "error", "category": "input" if isinstance(error, (InferenceInputError, ValueError)) else "execution",
                  "error": f"{type(error).__name__}: {str(error)[:1200]}"})


if __name__ == "__main__":
    raise SystemExit(main())
