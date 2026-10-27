"""Local test adapter with an observable dispatch marker; never a simulator claim."""
from pathlib import Path
import sys
import time

from ade2e.adapters import Registry
from ade2e.database import Database, Settings
from ade2e.reference import ReferenceAdapter
from ade2e.worker import run_once, worker_identity


class ObservableAdapter(ReferenceAdapter):
    def __init__(self, marker: Path, delay: float):
        self.marker, self.delay = marker, delay

    def metadata(self):
        return super().metadata().model_copy(update={"id": "observable-test", "version": "test-1"})

    def execute(self, scenario):
        with self.marker.open("ab", buffering=0) as stream:
            stream.write(b"dispatched\n")
        time.sleep(self.delay)
        return super().execute(scenario)


def registry(marker: Path, delay: float):
    result = Registry(load_plugins=False)
    result.register(ObservableAdapter(marker, delay))
    return result


if __name__ == "__main__":
    data, marker, delay, lease = Path(sys.argv[1]), Path(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
    db = Database(Settings(data_dir=data, seed_examples=False))
    db.initialize()
    try:
        result = run_once(db, registry(marker, delay), worker_identity("test"), lease_seconds=lease)
        print(result.model_dump_json() if result else "no claim", flush=True)
    finally:
        db.engine.dispose()
