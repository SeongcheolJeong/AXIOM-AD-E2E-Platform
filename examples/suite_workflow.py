"""Create original synthetic fixtures, execute a suite and freeze comparison evidence.

Requires an API and a separate worker sharing the same database. Creates four
scenarios, two requirements, a suite with two revisions, three campaigns (25
jobs), a baseline and a captured comparison. Outputs remain explicitly synthetic.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from axiom_sdk import AxiomClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", default=".runtime/suite-example")
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    intent = uuid4().hex
    with AxiomClient(args.url, token=os.getenv("ADE2E_API_TOKEN")) as api:
        scenarios = []
        for category, speed, gap, delay in [
            ("lane_follow", 14, 45, 0.3),
            ("emergency_brake", 24, 10, 1.4),
            ("cut_in", 23, 28, 0.4),
            ("pedestrian_crossing", 16, 12, 1.2),
        ]:
            scenarios.append(api.create_scenario(
                name=f"Suite example · {category.replace('_', ' ')}", category=category,
                description="Original synthetic suite example. Reference model evidence; no sensor or real-world validation.",
                duration_s=6, parameters={"ego_speed_mps": speed, "initial_gap_m": gap,
                                          "response_delay_s": delay, "trigger_time_s": 1},
            ))
        checks = [api.create_requirement(
            name=name, description="Illustrative synthetic suite criterion; not a production safety limit.",
            metric_name=metric, operator=operator, threshold=threshold,
            scenario_ids=[scenario["id"] for scenario in scenarios],
        ) for name, metric, operator, threshold in [
            ("Suite example · zero recorded collisions", "collision_count", "eq", 0),
            ("Suite example · at least one second TTC", "min_ttc_s", "gte", 1),
        ]]
        cases = [{
            "key": f"{scenario['category']}-{seed}", "name": f"{scenario['category'].replace('_', ' ')} · seed {seed}",
            "scenario_id": scenario["id"], "scenario_revision": scenario["revision"], "seed": seed,
            "parameter_overrides": {}, "requirement_ids": [check["id"] for check in checks],
        } for scenario in scenarios for seed in (41, 42)]
        definition = {"name": "Reference driving · suite workflow", "description": "Four synthetic scenario categories, two seeds per category and explicit saved criteria.", "cases": cases}
        suite = api.create_suite(**definition)

        def execute(revision, label, suffix):
            job_group = api.submit_campaign(suite["id"], suite_revision=revision, label=label, idempotency_key=f"{intent}:{suffix}")
            print(json.dumps({"submitted_campaign": job_group["id"], "label": label}), flush=True)
            replay = api.submit_campaign(suite["id"], suite_revision=revision, label=label, idempotency_key=f"{intent}:{suffix}")
            assert replay["id"] == job_group["id"], "Lost-response retry changed execution identity"
            return api.wait_campaign(job_group["id"])

        first = execute(1, "Reference capture · eight cases", "reference")
        baseline = api.create_baseline(first["id"], name="Synthetic reference · full case set",
                                       note="Captures every result, including failures. This is a comparison reference, not approval.",
                                       idempotency_key=f"{intent}:baseline")
        baseline_path = api.export_baseline(baseline["id"], output / "baseline.json")
        frozen_bytes = baseline_path.read_bytes()
        # Editing the library must not change already saved suite inputs.
        api.update_scenario(scenarios[0]["id"], parameters={"ego_speed_mps": 20})
        repeated = execute(1, "Same fixed conditions · repeatability", "repeat")
        repeat_comparison = api.compare_campaign(repeated["id"], baseline_id=baseline["id"])
        assert repeat_comparison["counts"]["total"] == 8
        assert sum(repeat_comparison["counts"][key] for key in ("unchanged_pass", "unchanged_fail")) == 8

        changed_cases = json.loads(json.dumps(cases))
        changed_cases[0]["parameter_overrides"] = {"initial_gap_m": 55}
        changed_cases.append({**cases[0], "key": "lane_follow-43", "name": "lane follow · seed 43", "seed": 43})
        revised = api.revise_suite(suite["id"], expected_revision=1, **{**definition, "cases": changed_cases})
        candidate = execute(revised["revision"], "Changed gap and added case · explicit scope change", "candidate")
        comparison = api.compare_campaign(candidate["id"], baseline_id=baseline["id"])
        assert comparison["counts"]["total"] == 9 and comparison["counts"]["added"] == 1
        assert comparison["counts"]["not_comparable"] == 1
        captured = api.capture_comparison(candidate["id"], baseline_id=baseline["id"],
                                          name="Synthetic suite · scope change review",
                                          note="One input changed and one case was added; neither is called regression or improvement.",
                                          idempotency_key=f"{intent}:comparison")
        api.export_comparison(captured["id"], output / "comparison.json")
        api.export_baseline(baseline["id"], output / "baseline-after.json")
        assert frozen_bytes == (output / "baseline-after.json").read_bytes()
        summary = {"suite_id": suite["id"], "baseline_id": baseline["id"], "comparison_id": captured["id"],
                   "campaign_ids": [first["id"], repeated["id"], candidate["id"]],
                   "baseline_sha256": hashlib.sha256(frozen_bytes).hexdigest(),
                   "reference_counts": first["counts"], "comparison_counts": comparison["counts"],
                   "fidelity": "synthetic reference object model; no CARLA server or perception execution"}
        (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
