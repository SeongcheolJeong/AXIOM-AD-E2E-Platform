"""Submit durable reference jobs and inspect their original evidence through SDK.

Run scripts/dev.py first, or run API and ade2e.worker against the same workspace.
"""
import argparse
import os
from uuid import uuid4

from axiom_sdk import AxiomClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("AXIOM_URL", "http://127.0.0.1:8000"))
    args = parser.parse_args()
    with AxiomClient(args.url, token=os.environ.get("ADE2E_API_TOKEN")) as api:
        scenarios = api.scenarios()
        if not scenarios:
            raise SystemExit("Create a scenario before running this example")
        key = "queued-example-" + uuid4().hex
        job = api.submit_job(scenarios[0]["id"], idempotency_key=key)
        print(f"Submitted {job['id']} at revision {job['scenario_revision']}", flush=True)
        assert api.submit_job(scenarios[0]["id"], idempotency_key=key)["id"] == job["id"]
        result = api.wait_job(job["id"], timeout=30)
        if result["status"] != "completed" or not result["run_id"]:
            raise SystemExit(f"Job ended as {result['status']}: {result['error']}")
        run = api.get_run(result["run_id"])
        assert run["provenance"]["input_hash"] == job["input_hash"]
        assert run["scenario_snapshot"] == job["scenario_snapshot"]
        print(f"Run {run['id']}: {run['verdict']}; immutable input and idempotent submission verified")


if __name__ == "__main__":
    main()
