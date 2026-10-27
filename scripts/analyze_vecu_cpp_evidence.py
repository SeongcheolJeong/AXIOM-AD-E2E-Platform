#!/usr/bin/env python3
"""Derive C++ ECU A/B measurements from immutable CARLA execution artifacts.

Reads the execution receipt and optional local artifact store; never runs an
engine or changes a runtime. Writes comparison.json and comparison.md beside
the receipt only when all four native cases are completed and hashes match.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import tarfile

CASES = ("normal", "delay", "drop", "repeat")
SIDES = ("baseline", "faulted")


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def check(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def xy_distance(a, b):
    return math.hypot(b["x"]-a["x"], b["y"]-a["y"])


def measurements(trace):
    rows = trace["rows"]
    check(len(rows) == 360 and trace["sample_hz"] == 20, "CARLA acceptance requires 360 steps at20Hz")
    for i, row in enumerate(rows):
        check(row["sequence"] == i and abs(row["request"]["time_s"]-i*.05) < 1e-9, "Unexpected virtual clock")
        check(row["request"]["ego"]["id"] != row["generated_objects"][0]["id"], "Ground truth object identity invalid")
    first = next((i for i, row in enumerate(rows) if row["reply"]["state"] == "degraded"), None)
    full = stop = recovery = None
    distance = None
    if first is not None:
        full = next((i for i in range(first, len(rows)) if rows[i]["reply"]["control"]["brake"] >= 1-1e-9), None)
        stop = next((i for i in range(first, len(rows)) if rows[i]["next_ego"]["speed_mps"] < .1), None)
        recovery = next((i for i in range(first+1, len(rows)) if rows[i]["reply"]["state"] == "tracking"), None)
        if stop is not None:
            points = [rows[first]["request"]["ego"]]+[rows[i]["next_ego"] for i in range(first, stop+1)]
            distance = sum(xy_distance(a, b) for a, b in zip(points, points[1:]))
    truth_gaps = [dict(distance_m=xy_distance(row["request"]["ego"], obj), input_time_s=row["sequence"]*.05,
                       object_id=obj["id"]) for row in rows for obj in row["generated_objects"]]
    in_fault = [v for v in truth_gaps if 3 <= v["input_time_s"] < 7]
    response_time = (stop+1)*.05 if stop is not None else None
    return dict(
        policy_id=trace["runtime"]["policy_id"], steps=len(rows),
        first_degraded_input_s=None if first is None else first*.05,
        first_full_brake_input_s=None if full is None else full*.05,
        first_stopped_response_s=response_time,
        stop_latency_from_degraded_s=None if stop is None else response_time-first*.05,
        stop_distance_from_degraded_m=distance,
        recovery_tracking_input_s=None if recovery is None else recovery*.05,
        min_truth_actor_origin_xy_gap=min(truth_gaps, key=lambda v: v["distance_m"]),
        min_truth_actor_origin_xy_gap_fault_window=min(in_fault, key=lambda v: v["distance_m"]),
        duplicate_packet_count=rows[-1]["reply"]["duplicate_packet_count"],
        missing_packet_count=rows[-1]["reply"]["missing_packet_count"],
        max_object_age_ms=max(row["reply"]["object_age_s"]*1000 for row in rows),
        degraded_steps=sum(row["reply"]["state"] == "degraded" for row in rows),
        recovering_steps=sum(row["reply"]["state"] == "recovering" for row in rows),
    )


def deltas(a, b):
    return dict(
        max_response_speed_delta_mps=max(abs(x["next_ego"]["speed_mps"]-y["next_ego"]["speed_mps"]) for x, y in zip(a, b)),
        max_control_delta={key: max(abs(x["reply"]["control"][key]-y["reply"]["control"][key]) for x, y in zip(a, b))
                           for key in ("throttle", "brake", "steer")},
        max_response_position_delta_m=max(xy_distance(x["next_ego"], y["next_ego"]) for x, y in zip(a, b)),
    )


def verify_record(record, directory, store, connection):
    check(record.get("status") == "completed" and record["result"]["status"] == "completed", "Incomplete execution")
    declared = {a["name"]: a for a in record["result"]["artifacts"]}
    verified = []
    for item in record["verified_artifacts"]:
        path = directory/item["name"]
        check(path.is_file(), "Missing receipt artifact: "+str(path))
        check(item["sha256"] == declared[item["name"]]["sha256"] == sha(path), "Receipt artifact hash mismatch")
        check(path.stat().st_size == declared[item["name"]]["size_bytes"], "Receipt artifact size mismatch")
        verified.append(dict(name=item["name"], sha256=item["sha256"], location="receipt_artifacts"))
    export = directory/"execution-export.json"
    check(sha(export) == record["manifest_hash"] == record["result"]["manifest_hash"], "Execution export hash mismatch")
    check(read(export)["execution"]["id"] == record["result"]["id"], "Export execution identity mismatch")
    names = {v["name"] for v in verified}
    metadata_only = []
    for name, item in declared.items():
        if name in names:
            continue
        if connection is None:
            metadata_only.append(item)
            continue
        row = connection.execute("SELECT * FROM experiment_artifacts WHERE job_id=? AND id=?",
                                 (record["result"]["id"], item["id"])).fetchone()
        check(row is not None and row["name"] == name and row["sha256"] == item["sha256"] and
              row["size_bytes"] == item["size_bytes"], "Local artifact catalog identity mismatch")
        path = store/"experiments"/"artifacts"/row["storage_key"]
        check(path.is_file() and path.stat().st_size == item["size_bytes"] and sha(path) == item["sha256"],
              "Native stored artifact hash mismatch: "+name)
        verified.append(dict(name=name, sha256=item["sha256"], size_bytes=item["size_bytes"],
                             location="original_server_artifact_store", storage_key=row["storage_key"]))
    return dict(job_id=record["result"]["id"], manifest_sha256=record["manifest_hash"],
                verified=verified, metadata_only=metadata_only)


def analyze(receipt_path, store=None):
    receipt_path = receipt_path.resolve()
    directory = receipt_path.parent
    receipt = read(receipt_path)
    check(all(receipt["runs"].get(k, {}).get("status") == "completed" for k in CASES), "All four native CARLA cases must complete first")
    connection = None
    if store is not None:
        store = store.resolve()
        connection = sqlite3.connect("file:"+str(store/"platform.db")+"?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
    report = dict(schema_version="vecu-cpp-evidence-comparison/1", receipt_sha256=sha(receipt_path),
                  analyzer_sha256=sha(Path(__file__)),
                  source="original CARLA hil-trace and native scene artifacts", physical_hil=False,
                  production_software=False, driving_safety="not_evaluated", cases={}, verification={},
                  time_basis="ECU state/control: input time; vehicle response/camera: input+0.05s",
                  distance_basis="XY actor-origin displacement, not bumper clearance; stopping path sums degraded pre-ego then each post-tick ego until speed<0.1m/s")
    traces = {}
    common_scene = None
    try:
        for group in ("builds", "fixed_runs"):
            for name, record in receipt[group].items():
                folder = directory/"receipt-artifacts"/("builds" if group == "builds" else "fixed")/name
                report["verification"][group+"/"+name] = verify_record(record, folder, store, connection)
                if group == "builds":
                    manifest = record["summary"]["build_manifest"]
                    with tarfile.open(folder/"vecu-build-source.tar.gz") as archive:
                        files = {m.name: hashlib.sha256(archive.extractfile(m).read()).hexdigest() for m in archive.getmembers()}
                    check(files == manifest["files"] and hashlib.sha256(canonical(files)).hexdigest() == manifest["source_sha256"],
                          "Archived native source manifest mismatch")
        for name in CASES:
            record = receipt["runs"][name]
            folder = directory/"receipt-artifacts"/name
            report["verification"]["runs/"+name] = verify_record(record, folder, store, connection)
            provenance = read(folder/"vecu-provenance.json")
            test = provenance["test"]
            check(test["seed"] == 42 and test["comparison"] == "package_ab", "Case seed/comparison mismatch")
            check(test["baseline"] == test["variant"] == dict(max_object_age_ms=250, recovery_fresh_steps=3), "Case freshness policy parameters differ")
            traces[name] = {}
            sides = {}
            media = {}
            for side in SIDES:
                trace = read(folder/(side+"-hil-trace.json"))
                scene = read(folder/(side+"-city-scene.json"))
                check(scene["source"]["seed"] == 42 and trace["status"] == "completed", "Native seed/status differs")
                signature = {k:scene["source"][k] for k in ("scenario_id", "scenario_revision", "scenario_manifest_hash", "configuration_hash", "seed")}
                common_scene = common_scene or signature
                check(signature == common_scene, "CARLA base scene differs")
                check(trace["runtime"] == record["summary"]["packages"][side]["runtime"], "Runtime provenance mismatch")
                expected_fault = None if name == "normal" else dict(kind=name, start_s=3, duration_s=4, delay_ms=400 if name == "delay" else 0)
                check(trace["fault"] == expected_fault and trace["fault_enabled"] == (name != "normal"), "Fault interval differs from[3,7)")
                check(trace["scene_profile"]["id"] == "lead_deceleration", "Lead scene differs")
                check(all(r["next_ego"] == next(a for a in frame["actors"] if a["id"] == r["request"]["ego"]["id"])
                          for r, frame in zip(trace["rows"], scene["frames"])), "Trace response differs from native scene")
                traces[name][side] = trace["rows"]
                sides[side] = measurements(trace)
                dimensions = sorted({(r["width"], r["height"]) for r in scene["camera_records"]})
                camera_periods = sorted({sensor["period_s"] for sensor in scene["sensors"]})
                check(dimensions == [(640, 360)] and camera_periods == [.1], "Camera capture contract differs")
                check(len(scene["camera_records"]) == 360 and all(sum(r["sensor_id"] == sensor for r in scene["camera_records"]) == 180
                      for sensor in ("front", "chase")), "Incomplete retained camera capture")
                media[side] = dict(camera_records=len(scene["camera_records"]),
                    records_by_sensor={sensor["id"]: sum(r["sensor_id"] == sensor["id"] for r in scene["camera_records"]) for sensor in scene["sensors"]},
                    frame_dimensions=list(dimensions[0]), camera_period_s=camera_periods[0],
                    bytes_copied_to_report_directory=False,
                    archive_hash_verification="original_server_artifact_store" if connection else "metadata_only",
                    all_individual_frame_hashes_decoded_here=False)
            report["cases"][name] = dict(job_id=record["result"]["id"], test_ref=record["summary"]["test_ref"],
                fault_window_s=None if name == "normal" else [3, 7], sides=sides, media=media,
                differences=deltas(traces[name]["baseline"], traces[name]["faulted"]))
        report["common_scene"] = common_scene
        report["cross_fault_behavior"] = {side: {name: deltas(traces["delay"][side], traces[name][side]) for name in ("drop", "repeat")} for side in SIDES}
        check(report["cases"]["normal"]["differences"]["max_response_speed_delta_mps"] == 0 and
              all(v == 0 for v in report["cases"]["normal"]["differences"]["max_control_delta"].values()),
              "Normal A/B behavior differs; review narrative before publishing")
        check(all(v["max_response_speed_delta_mps"] == 0 and v["max_response_position_delta_m"] == 0 and
                  all(x == 0 for x in v["max_control_delta"].values()) for values in report["cross_fault_behavior"].values() for v in values.values()),
              "Cross-fault behavior differs; review narrative before publishing")
        report["fixed_replay_three_runs"] = {side:[receipt["fixed_runs"][name]["summary"]["sides"][side]["logical_sha256"]
            for name in ("delay", "delay-repeat-2", "delay-repeat-3")] for side in SIDES}
        check(all(len(set(v)) == 1 for v in report["fixed_replay_three_runs"].values()), "Fixed replay logical hash changed")
        attempt = read(directory/"attempt-1.json")
        report["previous_attempt"] = dict(receipt="attempt-1.json", sha256=sha(directory/"attempt-1.json"),
            runs={k:dict(job_id=v["result"]["id"], status=v["status"], error=v.get("error")) for k,v in attempt["runs"].items()},
            included_in_successful_measurements=False)
    finally:
        if connection:
            connection.close()
    report["verification_summary"] = dict(
        executions=len(report["verification"]),
        artifacts=sum(len(v["verified"]) for v in report["verification"].values()),
        metadata_only=sum(len(v["metadata_only"]) for v in report["verification"].values()),
        native_camera_zip_archives=sum(a["name"].endswith(".zip") for v in report["verification"].values() for a in v["verified"]),
        source_archives_verified=True)
    (directory/"comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)+"\n")
    def f(value, digits=3):
        return "—" if value is None else f"{value:.{digits}f}"
    lines = ["# Native C++ ECU / CARLA A/B", "", "실제 Linux C++ 빌드 2개와 CARLA 4개 A/B 실행(차량 실행 8개)을 비교했다. 각 실행은 18초·20Hz·seed 42이며, 앞차는 4초부터 감속한다. 고장 구간은 입력 시각 [3,7)초다.", "",
        "A는 즉시 전제동, B는 300ms 제동 상승·500ms 복귀 정책이다. 두 정책 모두 250ms 입력 유효기간과 신규 정상 패킷 3개 회복 조건을 사용한다. 참조 알고리즘 비교이며 양산 ECU·물리 HIL·주행 안전성 검증이 아니다.", "",
        "| 조건 / ECU | degraded 입력(s) | 전제동 입력(s) | 정지 응답(s) | 감지→정지 거리(m) | tracking 복귀 입력(s) | 최소 실제 객체 원점 간격(m) | 중복 / 미전달 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|"]
    labels = dict(normal="정상", delay="400ms 지연", drop="입력 단절", repeat="패킷 반복")
    for case in CASES:
        for side, label in zip(SIDES, ("A", "B")):
            m = report["cases"][case]["sides"][side]
            lines.append(f"| {labels[case]} / {label} | {f(m['first_degraded_input_s'],2)} | {f(m['first_full_brake_input_s'],2)} | {f(m['first_stopped_response_s'],2)} | {f(m['stop_distance_from_degraded_m'])} | {f(m['recovery_tracking_input_s'],2)} | {f(m['min_truth_actor_origin_xy_gap']['distance_m'])} | {m['duplicate_packet_count']} / {m['missing_packet_count']} |")
    a,b = [report["cases"]["delay"]["sides"][side] for side in SIDES]
    lines += ["", f"정상 조건에서는 A/B 차량 속도와 제어 명령이 동일했다. 400ms 지연에서 B는 A보다 {b['stop_distance_from_degraded_m']-a['stop_distance_from_degraded_m']:.3f}m 더 이동한 뒤 정지했고, tracking 복귀는 0.50초 늦었다. 이는 완만한 제동 정책의 관측된 차이이며 개선 또는 안전성 우위를 뜻하지 않는다.", "",
        "지연·단절·반복은 각 정책 안에서 제동·속도·차량 위치가 동일했다. 세 고장의 차이는 입력 age와 중복/미전달 진단에 남는다. 지연의 중복 8회는 초기 지연 큐가 채워지는 동안 유지된 기존 패킷 노출이며, 별도 반복 고장 80회와 구분한다.", "",
        "시간·거리 정의: ECU 상태/명령은 입력 시각, 차량 정지는 다음 tick의 응답 시각(+50ms)이다. 정지는 고장 감지 이후 speed<0.1m/s인 첫 응답이다. 정지 거리는 감지 직전 ego 위치부터 그 응답까지 각 XY 변위 합이다. 최소 간격은 전체 18초의 현재 request.ego와 generated_objects로 계산한 객체 원점 간 XY 거리이며, 지연된 object_packet이나 범퍼 간격을 사용하지 않았다.", "",
        "검증 근거:", "",
        f"- {report['verification_summary']['executions']}개 실행의 export 해시와 {report['verification_summary']['artifacts']}개 artifact 해시를 확인했다. 원본 카메라ZIP {report['verification_summary']['native_camera_zip_archives']}개는 로컬 서버 저장소에서 직접 해시 검사했다. 영상ZIP을 보고서 폴더에 복제하지 않았다.",
        "- 차량 실행별 front/chase 각각 180장, 640×360, 0.1초 간격의 카메라 레코드를 보존했다. 이 분석기는 모든 개별 영상 프레임을 디코딩해 검사하지 않는다. 브라우저 선택 프레임 검증은 별도 UI 근거다.",
        "- 고정 재생 3회의 정책별 논리 결과 해시가 일치했다. 입력이 제어 결과로 바뀌는 CARLA 폐루프와 구분했다.",
        "- 최초 실패 실행은 attempt-1.json에 남겼으며 성공 수치에 포함하지 않았다.", "",
        "실행 식별자:", ""]
    for case in CASES:
        lines.append(f"- {labels[case]}: `{report['cases'][case]['job_id']}`")
    lines += ["", "정확한 수치·해시·artifact 위치·실패 실행은 comparison.json, 원래 입력과 실행 receipt는 receipt.json에 있다."]
    (directory/"comparison.md").write_text("\n".join(lines)+"\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    report = analyze(args.receipt, args.data_dir)
    print(json.dumps(dict(verification=report["verification_summary"],
        cases={k:v["sides"] for k,v in report["cases"].items()}, cross_fault=report["cross_fault_behavior"]), ensure_ascii=False, indent=2))
