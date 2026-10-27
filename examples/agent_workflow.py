"""Run: .venv/bin/python examples/agent_workflow.py

Creates a requirement, executes a bounded speed sweep and downloads evidence.
Run against a local workspace started with scripts/dev.py.
"""
import json
import os
from pathlib import Path
from uuid import uuid4

from axiom_sdk import AxiomClient


def main() -> None:
    with AxiomClient(os.getenv("AXIOM_URL", "http://127.0.0.1:8000"),
                     token=os.getenv("ADE2E_API_TOKEN")) as client:
        scenario = client.create_scenario(
            name="Agent experiment · emergency braking", category="emergency_brake",
            description="Authored reference experiment; not certified AEB evaluation.",
            tags=["agent-created", "reference", "speed-sweep"], seed=42,
            parameters={"ego_speed_mps": 15, "target_speed_mps": 9, "initial_gap_m": 30,
                        "trigger_time_s": 3, "friction": 0.85, "response_delay_s": 0.4},
        )
        requirement = client.create_requirement(
            name="Experiment · no modeled collisions", description="Reference geometry only.",
            metric_name="collision_count", operator="eq", threshold=0,
            scenario_ids=[scenario["id"]],
        )
        results = list(client.sweep(scenario["id"], {"ego_speed_mps": [10, 15, 22]},
                                    max_runs=3, campaign_key=f"agent-example-{uuid4()}"))
        destination = Path("artifacts") / f"agent-experiment-{results[-1]['id']}.json"
        destination.parent.mkdir(exist_ok=True)
        client.export_run(results[-1]["id"], destination)
        evaluated = next(row for row in client.requirements() if row["id"] == requirement["id"])
        print(json.dumps({"scenario_id": scenario["id"], "requirement_id": requirement["id"],
                          "requirement_status": evaluated["status"],
                          "scope_note": "Latest sweep variant is not current baseline evidence; create independent variant scenarios for baseline evaluation.",
                          "runs": [{"id": r["id"], "verdict": r["verdict"]} for r in results],
                          "evidence": str(destination.resolve())}, indent=2))


if __name__ == "__main__":
    main()
