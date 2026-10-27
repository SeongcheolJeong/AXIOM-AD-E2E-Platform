"""Original illustrative scenarios. Every displayed seed result is executed."""

from sqlalchemy import select

from .database import ScenarioRow
from .schemas import RequirementCreate, RunCreate, ScenarioCreate
from .service import execute_run, requirement_create, scenario_create


def seed_examples(db, registry):
    with db.session.begin() as session:
        if session.scalar(select(ScenarioRow.id).limit(1)) is not None:
            return
        specs = [
            ("Highway cut-in · generous gap", "cut_in", "clear", "day", 18, 16, 55, 2, 0.85, 0.25),
            ("Late cut-in · delayed response", "cut_in", "rain", "dusk", 24, 8, 16, 0.5, 0.35, 1.6),
            ("Lead braking · early response", "emergency_brake", "clear", "day", 12, 12, 65, 2, 0.9, 0.2),
            ("Lead braking · short wet-road gap", "emergency_brake", "rain", "night", 23, 20, 13, 1, 0.3, 1.4),
            ("Pedestrian crossing · low-speed approach", "pedestrian_crossing", "clear", "day", 7, 1.4, 38, 0.5, 0.85, 0.2),
            ("Pedestrian crossing · late response", "pedestrian_crossing", "fog", "night", 18, 1.8, 39, 0, 0.45, 1.7),
            ("Lane follow · stable headway", "lane_follow", "clear", "day", 15, 15, 50, 2, 0.85, 0.3),
            ("Lane follow · slower lead in rain", "lane_follow", "rain", "dusk", 14, 12, 65, 2, 0.65, 0.4),
        ]
        scenarios = []
        for index, (name, category, weather, daylight, ego, target, gap, trigger, friction, delay) in enumerate(specs):
            scenarios.append(scenario_create(session, ScenarioCreate(
                name=name, description="Original synthetic example for exercising platform authoring, execution and evidence. Uses a straight-road kinematic reference model, not sensor or safety certification evidence.",
                category=category, weather=weather, time_of_day=daylight,
                tags=["original-example", "synthetic", "reference"], seed=42 + index,
                parameters={"ego_speed_mps": ego, "target_speed_mps": target, "initial_gap_m": gap, "trigger_time_s": trigger, "friction": friction, "response_delay_s": delay},
            )))
        for scenario in scenarios:
            execute_run(session, db, registry, RunCreate(scenario_id=scenario.id))
        requirements = [
            RequirementCreate(name="Lane-follow baseline: no ego collision", description="Latest current-revision baseline execution for each linked lane-follow scenario must have zero ego-target collisions. Reference evidence only.", metric_name="collision_count", operator="eq", threshold=0, category="lane_follow", scenario_ids=[scenario.id for scenario in scenarios if scenario.category == "lane_follow"]),
            RequirementCreate(name="Cut-in envelope: retain 1 s TTC", description="Illustrative regression threshold for both cut-in examples. A failure is retained as actionable evidence, not hidden by an earlier pass.", metric_name="min_ttc_s", operator="gte", threshold=1, category="cut_in", scenario_ids=[scenario.id for scenario in scenarios if scenario.category == "cut_in"]),
            RequirementCreate(name="Extended regression suite: evidence pending", description="No scenarios have been linked to this next work package. It must remain untested until scope and execution evidence exist.", metric_name="collision_count", operator="eq", threshold=0),
            RequirementCreate(name="Emergency braking: peak deceleration ≤ 6 m/s²", description="Illustrative comfort threshold; not a calibrated production acceptance criterion.", metric_name="max_deceleration_mps2", operator="lte", threshold=6, category="emergency_brake"),
        ]
        for requirement in requirements:
            requirement_create(session, requirement)
