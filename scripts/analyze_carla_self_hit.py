#!/usr/bin/env python3
"""Offline, bounded self-hit evidence analysis. Never imports CARLA or contacts a host.

python analyze-carla-self-hit.py --input CAPTURE_DIR --source-script CAPTURED.py \
    --output NEW_ANALYSIS.json

Original files are read only. Semantic instance truth remains an observer; it is
never a substitute for the original regular point stream or historical labels.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import struct

VERSION = "self-hit-offline-analysis/1"
MAX_POINTS = 20_000
MAX_PAIRS = 16
MAX_RAW = 32 * 1024 * 1024
MAX_JSON = 32 * 1024 * 1024
TOLERANCE_M = .0001
POSE_TRANSLATION_M = .0001
POSE_ROTATION_MATRIX = .00001
STATUSES = ("byte_exact_xyz", "numerical_exact_xyz", "approximate_xyz_1e-4m", "ambiguous", "unmatched")
GEOMETRY = ("channels", "range", "points_per_second", "rotation_frequency", "upper_fov", "lower_fov", "horizontal_fov", "sensor_tick")


class InvalidEvidence(ValueError):
    pass


def require(value, message):
    if not value:
        raise InvalidEvidence(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, "Duplicate JSON key: " + key)
        result[key] = value
    return result


def strict_json(data):
    return json.loads(data, object_pairs_hook=_pairs, parse_constant=lambda x: (_ for _ in ()).throw(InvalidEvidence("Nonfinite JSON: " + x)))


def read_file(directory, name, limit):
    require(isinstance(name, str) and Path(name).name == name and name not in (".", ".."), "Unsafe artifact path")
    path = directory / name
    require(path.is_file() and not path.is_symlink(), "Missing or symlink file: " + name)
    require(path.stat().st_size <= limit, "File bound exceeded: " + name)
    data = path.read_bytes()
    require(len(data) <= limit, "File grew beyond bound: " + name)
    return data


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def integer(value, low=0, high=10**18):
    return type(value) is int and low <= value <= high


def verify_chain(content, report):
    require(len(content) <= MAX_JSON and content.endswith(b"\n"), "Missing/truncated event chain")
    events, previous = [], "0" * 64
    for line in content.splitlines():
        require(len(events) < 12_000 and len(line) <= 2 * 1024 * 1024, "Event count/size bound")
        event = strict_json(line)
        require(set(event) == {"sequence", "previous_sha256", "at", "kind", "data", "sha256"}, "Unknown event envelope")
        require(event["sequence"] == len(events) and type(event["sequence"]) is int, "Event sequence differs")
        require(event["previous_sha256"] == previous, "Event previous hash differs")
        body = {k: v for k, v in event.items() if k != "sha256"}
        require(sha(canonical(body)) == event["sha256"], "Event content hash differs")
        require(isinstance(event["kind"], str) and isinstance(event["data"], dict), "Invalid event kind/data")
        previous = event["sha256"]
        events.append(event)
    chain = report.get("event_chain", {})
    require(set(chain) in ({"entries", "head_sha256"}, {"entries", "head_sha256", "sealed"}) and
        chain.get("entries") == len(events) and type(chain.get("entries")) is int and chain.get("head_sha256") == previous, "Report event head/count differs")
    if "sealed" in chain:
        require(chain["sealed"] is True, "Unsealed callback/event prefix cannot establish final evidence")
        shutdown = report.get("callback_shutdown", {})
        require(shutdown.get("accepting") is False and shutdown.get("active") == 0 and shutdown.get("drained") is True and
            integer(shutdown.get("started"), 0, 64) and shutdown.get("started") == shutdown.get("finished") and
            integer(shutdown.get("rejected_after_stop"), 0), "Callback shutdown is not drained/consistent")
    require(events and events[-1]["kind"] == "diagnostic_final", "No terminal diagnostic event")
    final = events[-1]["data"]
    require(final == {"status": report["status"], "error": report.get("error"), "cleanup_status": report["cleanup"]["status"], "reservation_retained": report["reservation_retained"]}, "Report final state differs from event")
    return events


def matrix(value):
    require(isinstance(value, list) and len(value) == 4 and all(isinstance(row, list) and len(row) == 4 for row in value), "Invalid pose matrix shape")
    require(all(finite(x) for row in value for x in row), "Nonfinite pose matrix")
    require(all(abs(value[3][j] - (1 if j == 3 else 0)) <= 1e-7 for j in range(4)), "Invalid homogeneous matrix")
    return value


def multiply(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def pose(value):
    require(isinstance(value, dict), "Missing pose")
    m, inv = matrix(value["matrix_native"]), matrix(value["inverse_matrix_native"])
    product = multiply(m, inv)
    require(max(abs(product[i][k] - (i == k)) for i in range(4) for k in range(4)) <= 1e-5, "Pose inverse differs")
    require(max(abs(sum(m[k][i] * m[k][j] for k in range(3)) - (i == j)) for i in range(3) for j in range(3)) <= 1e-5, "Nonrigid pose matrix")
    require(all(finite(value["position_m"][k]) and abs(value["position_m"][k] - m[i][3]) <= 1e-6 for i, k in enumerate(("x", "y", "z"))), "Pose position differs")
    require(all(finite(value["rotation_deg"][k]) for k in ("pitch", "yaw", "roll")), "Invalid pose rotation")
    return m, inv


def delta(a, b):
    return {"translation_m": math.dist([a[i][3] for i in range(3)], [b[i][3] for i in range(3)]), "rotation_matrix_max_abs": max(abs(a[i][j] - b[i][j]) for i in range(3) for j in range(3))}


def pose_equal(a, b):
    d = delta(a, b)
    require(d["translation_m"] <= POSE_TRANSLATION_M and d["rotation_matrix_max_abs"] <= POSE_ROTATION_MATRIX, "Pose/mount readback mismatch")
    return d


def decode_cloud(directory, cloud, kind):
    stride, layout = (16, "<ffff") if kind == "regular" else (24, "<ffffII")
    require(cloud["kind"] == kind and cloud["stride_bytes"] == stride and cloud["layout"] == layout, "Cloud layout differs")
    require(cloud["channels"] == 64 and type(cloud["channels"]) is int, "Cloud channel count differs")
    counts = cloud["point_counts_by_channel"]
    require(isinstance(counts, list) and len(counts) == 64 and all(integer(x, 0, MAX_POINTS) for x in counts), "Invalid per-channel counts")
    require(integer(cloud["point_count"], 0, MAX_POINTS) and sum(counts) == cloud["point_count"], "Point denominator differs")
    require(cloud["channel_byte_offsets"] == [sum(counts[:i]) * stride for i in range(65)], "Channel offsets differ")
    descriptor = cloud["raw"]
    data = read_file(directory, descriptor["path"], MAX_POINTS * stride)
    require(len(data) == descriptor["size_bytes"] == sum(counts) * stride and len(data) % stride == 0 and sha(data) == descriptor["sha256"], "Raw hash/size/layout differs")
    records = list(struct.iter_unpack(layout, data))
    require(all(all(finite(v) for v in row[:4]) and all(abs(v) <= 1_000_000 for v in row[:3]) for row in records), "Nonfinite/out-of-range raw fields")
    if kind == "semantic":
        require(all(abs(row[3]) <= 1.00001 for row in records), "Invalid semantic incidence cosine")
        require(cloud["object_index_counts"] == dict(Counter(str(row[4]) for row in records)), "Semantic actor counts differ")
        require(cloud["object_tag_counts"] == dict(Counter(str(row[5]) for row in records)), "Semantic tag counts differ")
    require(cloud["public_iteration_first_records"] == [list(row) for row in records[:3]], "Native endian/public-iteration record mismatch")
    return data, records, counts


def correspondence(regular, semantic, regular_counts, semantic_counts):
    """Full regular denominator; bounded same-channel candidates, no ID inference."""
    require(len(regular) % 16 == len(semantic) % 24 == 0, "Truncated point record")
    require(len(regular_counts) == len(semantic_counts) == 64 and all(integer(x, 0, MAX_POINTS) for x in regular_counts + semantic_counts), "Invalid channel counts")
    rr, ss = list(struct.iter_unpack("<ffff", regular)), list(struct.iter_unpack("<ffffII", semantic))
    require(sum(regular_counts) == len(rr) <= MAX_POINTS and sum(semantic_counts) == len(ss) <= MAX_POINTS, "Point count mismatch")
    require(all(all(finite(x) for x in row[:4]) for row in rr + ss), "Nonfinite point")
    rows, rstart, sstart = [], 0, 0
    for channel, (rc, sc) in enumerate(zip(regular_counts, semantic_counts)):
        bins = defaultdict(list)
        for j in range(sstart, sstart + sc):
            bins[tuple(math.floor(x / TOLERANCE_M) for x in ss[j][:3])].append(j)
        for i in range(rstart, rstart + rc):
            point, candidates, examined, bounded = rr[i], [], 0, False
            key = tuple(math.floor(x / TOLERANCE_M) for x in point[:3])
            for offset in itertools.product((-1, 0, 1), repeat=3):
                for j in bins.get(tuple(key[k] + offset[k] for k in range(3)), ()):
                    examined += 1
                    if examined > 512 or len(candidates) >= 65:
                        bounded = True
                        break
                    distance = math.dist(point[:3], ss[j][:3])
                    if distance <= TOLERANCE_M:
                        candidates.append((j, distance))
                if bounded:
                    break
            item = {"regular_index": i, "channel": channel, "status": "unmatched", "semantic_index": None, "object_idx": None, "object_tag": None, "xyz_residual_m": None}
            if bounded or len(candidates) > 1:
                item.update(status="ambiguous", candidate_count_at_least=len(candidates), candidate_search_bounded=bounded,
                    candidate_actor_ids=sorted({ss[j][4] for j, _ in candidates}), candidate_semantic_indices=[j for j, _ in candidates])
            elif candidates:
                j, distance = candidates[0]
                exact_bytes = regular[i * 16:i * 16 + 12] == semantic[j * 24:j * 24 + 12]
                numerical = point[:3] == ss[j][:3]
                item.update(status="byte_exact_xyz" if exact_bytes else "numerical_exact_xyz" if numerical else "approximate_xyz_1e-4m", semantic_index=j, object_idx=ss[j][4], object_tag=ss[j][5], xyz_residual_m=distance)
            rows.append(item)
        rstart += rc
        sstart += sc
    require(len(rows) == len(rr), "Lost regular denominator")
    return rows


def not_evaluated_rows(counts):
    rows = []
    for channel, count in enumerate(counts):
        for _ in range(count):
            rows.append({"regular_index": len(rows), "channel": channel, "status": "not_evaluated_pair", "semantic_index": None, "object_idx": None, "object_tag": None, "paired_ego_id_exact": False})
    return rows


def identity(value):
    result = {k: value[k] for k in ("actor_id", "type_id", "role_name")}
    require(integer(result["actor_id"], 1, 2**32 - 1) and all(isinstance(result[k], str) and result[k] for k in ("type_id", "role_name")), "Invalid actor identity")
    return result


def pair_gates(pair, lineage, mount, attributes):
    snapshot, clouds, readbacks = pair["snapshot"], pair["clouds"], pair["independent_readbacks"]
    frame = snapshot["world_frame"]
    require(integer(frame, 0) and finite(snapshot["source_time_s"]) and abs(snapshot["delta_seconds"] - .05) <= 1e-6, "Invalid snapshot frame/time")
    require(readbacks["before_world_frame"] == readbacks["after_world_frame"] == frame, "Readback frame bracket differs")
    ego = snapshot["actors"]["ego"]
    require(identity(ego) == lineage["ego"], "Snapshot ego identity differs")
    em, ei = pose(ego["snapshot_transform"])
    observed_ego = readbacks["vehicles"]["ego"]
    require(identity(observed_ego) == lineage["ego"], "Readback ego identity differs")
    pose_equal(pose(observed_ego["snapshot_transform"])[0], em)
    pose_equal(pose(observed_ego["actor_api_transform"])[0], em)
    control = observed_ego["reported_control"]
    require(control["throttle"] == 0 and control["brake"] == 1 and control["hand_brake"] is True and control["reverse"] is False, "Static control readback differs")
    matrices, differences = {}, {}
    for kind in ("regular", "semantic"):
        cloud, sensor = clouds[kind], readbacks["sensors"][kind]
        require(cloud["world_frame"] == frame and finite(cloud["timestamp_s"]) and abs(cloud["timestamp_s"] - snapshot["source_time_s"]) <= 1e-6, "Cloud frame/time differs")
        require(finite(cloud["horizontal_angle_rad"]), "Invalid reported angle")
        require(identity(sensor) == lineage[kind] and identity(sensor["public_parent"]) == lineage["ego"], "Independent sensor/parent identity differs")
        for key, text in attributes[kind].items():
            actual = sensor["attributes"][key]
            require(finite(float(actual)) and float(actual) == float(text), "Actor attribute readback differs: " + key)
        sm = pose(cloud["measurement_world_transform"])[0]
        api = pose(sensor["actor_api_world_transform"])[0]
        differences[kind] = {"measurement_to_actor_api": pose_equal(sm, api), "measurement_to_declared_mount": pose_equal(multiply(ei, sm), mount), "actor_api_to_declared_mount": pose_equal(multiply(ei, api), mount)}
        pose_equal(matrix(cloud["measurement_relative_to_snapshot_ego_matrix"]), multiply(ei, sm))
        pose_equal(matrix(sensor["actor_api_relative_to_ego_matrix"]), multiply(ei, api))
        if sensor["snapshot_status"] == "observed":
            snapshot_sensor = pose(sensor["snapshot_world_transform"])[0]
            pose_equal(snapshot_sensor, sm)
            pose_equal(matrix(sensor["snapshot_relative_to_ego_matrix"]), multiply(ei, snapshot_sensor))
        else:
            require(sensor["snapshot_status"] == "unavailable" and sensor["snapshot_world_transform"] is None and sensor["snapshot_relative_to_ego_matrix"] is None, "Inconsistent sensor snapshot availability")
        matrices[kind] = sm
    # Exact recorded co-location is required for exact raw-coordinate ID transfer.
    require(clouds["regular"]["measurement_world_transform"] == clouds["semantic"]["measurement_world_transform"], "Paired measurement poses differ")
    require(pair["clouds_equal_measurement_pose"] is True, "Producer did not retain equal measurement poses")
    require(pair["clouds_equal_horizontal_phase"] == (clouds["regular"]["horizontal_angle_rad"] == clouds["semantic"]["horizontal_angle_rad"]), "Reported phase comparison differs")
    return differences


def _event_data(events, kind):
    return [e["data"] for e in events if e["kind"] == kind]


def analyze_capture(directory, source_script):
    directory = Path(directory)
    require(directory.is_dir() and not directory.is_symlink(), "Capture directory missing or symlink")
    report_bytes = read_file(directory, "report.json", MAX_JSON)
    report = strict_json(report_bytes)
    require(report["schema_version"] == "carla-static-self-hit-diagnostic/1", "Unsupported capture report schema")
    source = Path(source_script)
    require(source.is_file() and not source.is_symlink() and source.stat().st_size <= 256 * 1024, "Invalid captured source script")
    require(sha(source.read_bytes()) == report["script_sha256"], "Captured source script hash differs")
    intent_bytes = read_file(directory, "source-intent.json", 128 * 1024)
    require(sha(intent_bytes) == report["source_intent_sha256"], "Source intent bytes differ")
    intent = strict_json(intent_bytes)
    require(intent["input_hash"] == report["source_input_hash"] and intent["adapter_version"] == report["adapter_version"] == "0.7.0", "Source input/adapter identity differs")
    profile = intent["context"]["sensor_profile"]
    require(profile["id"] == "carla-lidar-ego-near/1" and profile["mount"] == {"translation_m": [0., 0., 2.2], "rotation_xyzw": [0., 0., 0., 1.]}, "Unsupported declared mount/profile")
    attributes = {"regular": profile["attributes"], "semantic": {k: profile["attributes"][k] for k in GEOMETRY}}
    require(attributes["regular"]["noise_stddev"] == "0.0", "Nonzero regular noise cannot support exact hit transfer")
    require(report["sensor_configuration"]["regular"] == attributes["regular"] and report["sensor_configuration"]["semantic"] == attributes["semantic"], "Report sensor configuration differs")
    planned = report["limits"]["planned_frame_pairs"]
    require(integer(planned, 1, MAX_PAIRS) and report["limits"]["fixed_delta_seconds"] == .05, "Unsupported pair budget/timestep")
    content = read_file(directory, "events.jsonl", MAX_JSON)
    events = verify_chain(content, report)
    require(_event_data(events, "world_original") == [report["original"]], "Original world differs from journal")
    require(isinstance(report["original"]["world_id"], str) and report["original"]["world_id"].isdigit(), "World ID must remain decimal string")
    lineage, intents = {}, {}
    for event in _event_data(events, "spawn_identity_intent"):
        require(event["role"] not in intents, "Duplicate spawn role")
        intents[event["role"]] = event
    for event in _event_data(events, "spawn_identity_returned"):
        role = event["role"]
        require(role not in lineage and role in intents, "Duplicate/unrequested returned actor")
        observed = identity(event)
        require(observed["type_id"] == intents[role]["type_id"] and observed["role_name"] == intents[role]["role_name"] and event["parent_actor_id"] == intents[role]["parent_actor_id"], "Spawn identity/parent differs")
        lineage[role] = observed
    require(all(k in lineage for k in ("ego", "regular", "semantic")), "Required actor lineage missing")
    require(len({x["actor_id"] for x in lineage.values()}) == len(lineage), "Reused actor ID")
    require(lineage["ego"]["type_id"] == "vehicle.tesla.model3", "Unexpected ego actor type")
    mount = [[1., 0., 0., 0.], [0., 1., 0., 0.], [0., 0., 1., 2.2], [0., 0., 0., 1.]]
    for kind in ("regular", "semantic"):
        blueprint = "sensor.lidar.ray_cast" + ("_semantic" if kind == "semantic" else "")
        require(lineage[kind]["type_id"] == blueprint and intents[kind]["parent_actor_id"] == lineage["ego"]["actor_id"], "Sensor type/parent lineage differs")
        pose_equal(pose(intents[kind]["requested_transform"])[0], mount)
        detached = [e for e in _event_data(events, "detached_sensor_blueprint") if e["blueprint_id"] == blueprint]
        require(len(detached) == 1 and set(detached[0]["attributes"]) == set(attributes[kind]), "Detached blueprint attributes differ")
        for key, text in attributes[kind].items():
            actual = detached[0]["attributes"][key]
            is_int = key in ("channels", "points_per_second", "noise_seed")
            target = int(text) if is_int else struct.unpack("<f", struct.pack("<f", float(text)))[0]
            require(actual == {"requested": text, "readback": target, "type": "int" if is_int else "float32"}, "Detached attribute readback differs")
    decoded, callbacks, raw_files = {}, {}, {}
    persisted = _event_data(events, "raw_callback_persisted")
    for event in persisted:
        name = event["path"]
        require(name not in raw_files and event["kind"] in ("regular", "semantic"), "Duplicate/invalid raw callback")
        data = read_file(directory, name, MAX_POINTS * (16 if event["kind"] == "regular" else 24))
        require(len(data) == event["size_bytes"] and sha(data) == event["sha256"], "Persisted callback hash/size differs")
        raw_files[name] = event
    require(sum(x["size_bytes"] for x in raw_files.values()) == report["raw_bytes"] <= MAX_RAW, "Raw byte denominator differs")
    require(dict(Counter(x["kind"] for x in raw_files.values())) == report["raw_callbacks"], "Raw callback denominator differs")
    for cloud in _event_data(events, "cloud_callback"):
        name = cloud["raw"]["path"]
        require(name in raw_files and name not in callbacks, "Missing/duplicate cloud callback")
        require({"kind": cloud["kind"], "world_frame": cloud["world_frame"], **cloud["raw"]} == raw_files[name], "Cloud differs from persisted raw descriptor")
        decoded[name] = decode_cloud(directory, cloud, cloud["kind"])
        callbacks[name] = cloud
    pairs = report["pairs"]
    require(isinstance(pairs, list) and len(pairs) <= planned, "Invalid pair denominator")
    pair_events = _event_data(events, "paired_static_observation")
    indexed = {x["index"]: x for x in pair_events}
    require(len(indexed) == len(pair_events) and all(integer(i, 0, planned - 1) for i in indexed), "Duplicate/invalid paired event index")
    results, used, last_frame, last_time = [], set(), None, None
    for index, pair in enumerate(pairs):
        result = {"index": index, "accepted_for_identity": False, "errors": [], "regular_denominator": None, "direct_semantic_ego_hits": None, "matching_counts": None, "regular_rows": []}
        try:
            require(pair["index"] == index and indexed.get(index) == pair, "Report pair missing/different committed paired event")
            for kind in ("regular", "semantic"):
                cloud = pair["clouds"][kind]
                name = cloud["raw"]["path"]
                require(name in callbacks and name not in used, "Cloud reused/missing committed callback")
                require({k: v for k, v in cloud.items() if k != "measurement_relative_to_snapshot_ego_matrix"} == callbacks[name], "Pair cloud metadata differs from callback")
                used.add(name)
            rb, rr, rc = decoded[pair["clouds"]["regular"]["raw"]["path"]]
            sb, ss, sc = decoded[pair["clouds"]["semantic"]["raw"]["path"]]
            result["regular_denominator"] = len(rr)
            result["regular_rows"] = not_evaluated_rows(rc)
            result["raw_semantic_object_index_counts"] = dict(Counter(str(row[4]) for row in ss))
            result["pose_readback_deltas"] = pair_gates(pair, lineage, mount, attributes)
            frame, at = pair["snapshot"]["world_frame"], pair["snapshot"]["source_time_s"]
            if last_frame is not None:
                require(frame == last_frame + 1 and abs(at - last_time - .05) <= 1e-6, "Paired frame/time progression differs")
            last_frame, last_time = frame, at
            rows = correspondence(rb, sb, rc, sc)
            near = []
            ego_id = lineage["ego"]["actor_id"]
            for row, point in zip(rows, rr):
                x, native_y, z, intensity = point
                # Full identity quaternion arithmetic retains the existing signed-zero policy.
                y = -native_y
                tx, ty, tz = 2 * (0. * z - 0. * y), 2 * (0. * x - 0. * z), 2 * (0. * y - 0. * x)
                xyz = struct.unpack("<fff", struct.pack("<fff", x + tx + 0. * tz - 0. * ty + 0., y + ty + 0. * tx - 0. * tz + 0., z + tz + 0. * ty - 0. * tx + 2.2))
                is_near = 0 <= xyz[0] <= 2.5 and abs(xyz[1]) <= 1.2 and .2 <= xyz[2] <= 2.5
                row["positive_near_candidate"] = is_near
                row["paired_ego_id_exact"] = row["status"] in STATUSES[:2] and row["object_idx"] == ego_id
                if is_near:
                    near.append({**row, "regular_sensor_xyz": list(point[:3]), "declared_ego_xyz": list(xyz)})
            result.update(accepted_for_identity=True, world_frame=frame, timestamp_s=at,
                regular_raw=pair["clouds"]["regular"]["raw"], semantic_raw=pair["clouds"]["semantic"]["raw"],
                direct_semantic_ego_hits=sum(row[4] == ego_id for row in ss),
                matching_counts=dict(Counter(row["status"] for row in rows)),
                paired_exact_ego_hits=sum(row["paired_ego_id_exact"] for row in rows),
                positive_near_denominator=len(near), positive_near_matching_counts=dict(Counter(row["status"] for row in near)),
                positive_near_paired_exact_ego_hits=sum(row["paired_ego_id_exact"] for row in near),
                positive_near_rows=near, regular_rows=rows)
        except (InvalidEvidence, KeyError, TypeError, ValueError) as error:
            result["errors"].append(type(error).__name__ + ": " + str(error))
            # A bad frame/pose must withhold associations, not erase known points.
            if result["regular_denominator"] is None:
                name = pair.get("clouds", {}).get("regular", {}).get("raw", {}).get("path")
                if name in decoded:
                    _, known, counts = decoded[name]
                    result["regular_denominator"] = len(known)
                    result["regular_rows"] = not_evaluated_rows(counts)
        results.append(result)
    accepted = [r for r in results if r["accepted_for_identity"]]
    complete = len(accepted) == len(pairs) == planned and len(used) == len(callbacks) == len(raw_files) == 2 * planned and report["status"] == "collected"
    return {"schema_version": VERSION, "analysis_scope": "Offline source/hash/frame/pose-gated correspondence; no engine calls or original point changes.",
        "analyzer_sha256": sha(Path(__file__).read_bytes()), "capture_script_sha256": report["script_sha256"],
        "capture_report_sha256": sha(report_bytes), "capture_events_sha256": sha(content), "source_intent_sha256": sha(intent_bytes),
        "event_chain": report["event_chain"], "diagnostic_id": report["diagnostic_id"], "world_id": report["original"]["world_id"],
        "actor_lineage": lineage, "capture_status": report["status"], "cleanup": report["cleanup"],
        "capture_cleanup_not_reperformed": True, "status": "verified_complete" if complete else "verified_partial" if accepted else "inconclusive_no_accepted_pairs",
        "identity_claims_qualified": bool(accepted), "errors": [] if accepted else ["No accepted frame pairs; actor-identity association is inconclusive, not a zero-self-hit finding."],
        "coverage": {"planned_pairs": planned, "reported_pairs": len(pairs), "committed_pair_events": len(pair_events), "accepted_pairs": len(accepted), "raw_callbacks": report["raw_callbacks"], "raw_bytes": report["raw_bytes"], "complete": complete},
        "source_files": [{"path": name, "size_bytes": item["size_bytes"], "sha256": item["sha256"]} for name, item in raw_files.items()],
        "matching_policy": {"version": "channel-xyz-association/1", "statuses": [*STATUSES, "not_evaluated_pair"], "approximate_tolerance_m": TOLERANCE_M, "exact_id_transfer_requires": "Unique same-channel XYZ candidate; numerical-exact signed zeros separately labelled; approximate matches never count as exact proof.", "pose_translation_tolerance_m": POSE_TRANSLATION_M, "pose_rotation_matrix_max_abs": POSE_ROTATION_MATRIX, "regular_header_phase_is_not_alignment_proof": True},
        "totals_on_accepted_pairs_only": {"regular_points": sum(r["regular_denominator"] for r in accepted), "direct_semantic_ego_hits": sum(r["direct_semantic_ego_hits"] for r in accepted), "paired_exact_ego_hits": sum(r["paired_exact_ego_hits"] for r in accepted), "positive_near_candidates": sum(r["positive_near_denominator"] for r in accepted), "positive_near_paired_exact_ego_hits": sum(r["positive_near_paired_exact_ego_hits"] for r in accepted)},
        "pairs": results, "interpretation": "Direct semantic ego-ID observations and exact regular associations are separate claims for this diagnostic only. Approximate/ambiguous/unmatched rows retain the full denominator. No accepted pair means inconclusive, not absence of self-return. Historical unlabelled runs retain their original hypothesis status; this is not a driving verdict or learned-perception validation."}


def self_test():
    """Authored offline counterexamples; no remote service or historical capture."""
    import copy
    import tempfile

    reg = lambda p: struct.pack("<ffff", *p, .5)
    sem = lambda p, actor: struct.pack("<ffffII", *p, 1., actor, 14)
    rc = [5] + [0] * 63
    sc = [6, 1] + [0] * 62
    raw = b"".join(reg(p) for p in [(1, 2, 3), (4, 5, 6), (7, 8, 9), (-0., 1, 2), (10, 0, 0)])
    labelled = b"".join(sem(p, actor) for p, actor in [((1, 2, 3), 42), ((4, 5, 6), 42), ((4, 5, 6), 43), ((0., 1, 2), 42), ((10.00005, 0, 0), 42), ((12, 0, 0), 42), ((7, 8, 9), 42)])
    rows = correspondence(raw, labelled, rc, sc)
    assert [r["status"] for r in rows] == ["byte_exact_xyz", "ambiguous", "unmatched", "numerical_exact_xyz", "approximate_xyz_1e-4m"]
    assert rows[1]["candidate_actor_ids"] == [42, 43]
    assert rows[2]["object_idx"] is None  # identical coordinates in another channel.
    try:
        correspondence(raw + b"!", labelled, rc, sc)
    except InvalidEvidence:
        pass
    else:
        raise AssertionError("Truncated raw accepted")

    def authored_fixture(directory, mutate=None, no_pairs=False):
        source = directory / "captured-authoring-example.py"
        source.write_bytes(b"# Authored analyzer test fixture. No CARLA execution.\n")
        ident = [[1., 0., 0., 0.], [0., 1., 0., 0.], [0., 0., 1., 0.], [0., 0., 0., 1.]]
        def p(z=0.):
            m, inv = copy.deepcopy(ident), copy.deepcopy(ident)
            m[2][3], inv[2][3] = z, -z
            return {"position_m": {"x": 0., "y": 0., "z": z}, "rotation_deg": {"pitch": 0., "yaw": 0., "roll": 0.}, "matrix_native": m, "inverse_matrix_native": inv}
        def actor(role, number, type_id):
            return {"actor_id": number, "type_id": type_id, "role_name": "authored_" + role}
        ego, regular, semantic = actor("ego", 42, "vehicle.tesla.model3"), actor("regular", 101, "sensor.lidar.ray_cast"), actor("semantic", 102, "sensor.lidar.ray_cast_semantic")
        attrs = {"channels": "64", "range": "80", "points_per_second": "256000", "rotation_frequency": "20", "upper_fov": "10", "lower_fov": "-65", "horizontal_fov": "360", "sensor_tick": "0.0", "noise_stddev": "0.0"}
        intent = {"input_hash": "a" * 64, "adapter_version": "0.7.0", "context": {"sensor_profile": {"id": "carla-lidar-ego-near/1", "attributes": attrs, "mount": {"translation_m": [0., 0., 2.2], "rotation_xyzw": [0., 0., 0., 1.]}}}}
        intent_data = canonical(intent)
        (directory / "source-intent.json").write_bytes(intent_data)
        events, previous = [], "0" * 64
        def emit(kind, data):
            nonlocal previous
            body = {"sequence": len(events), "previous_sha256": previous, "at": "2026-09-13T00:00:00+00:00", "kind": kind, "data": copy.deepcopy(data)}
            previous = sha(canonical(body)); events.append({**body, "sha256": previous})
        original = {"world_id": "12345678901234567890", "actors": []}
        emit("world_original", original)
        for role, value in (("ego", ego), ("regular", regular), ("semantic", semantic)):
            emit("spawn_identity_intent", {"role": role, "role_name": value["role_name"], "type_id": value["type_id"], "parent_actor_id": None if role == "ego" else 42, "requested_transform": p(0. if role == "ego" else 2.2)})
            emit("spawn_identity_returned", {"role": role, **value, "parent_actor_id": None if role == "ego" else 42})
        aa = {"regular": attrs, "semantic": {k: attrs[k] for k in GEOMETRY}}
        for kind, value in (("regular", regular), ("semantic", semantic)):
            rb = {}
            for key, text in aa[kind].items():
                is_int = key in ("channels", "points_per_second", "noise_seed")
                rb[key] = {"requested": text, "readback": int(text) if is_int else struct.unpack("<f", struct.pack("<f", float(text)))[0], "type": "int" if is_int else "float32"}
            emit("detached_sensor_blueprint", {"blueprint_id": value["type_id"], "attributes": rb})
        clouds = {}
        if not no_pairs:
            for kind, value in (("regular", reg((1, .2, -1))), ("semantic", sem((1, .2, -1), 42))):
                name = kind + ".bin"; (directory / name).write_bytes(value)
                stride, layout = (16, "<ffff") if kind == "regular" else (24, "<ffffII")
                descriptor = {"path": name, "size_bytes": len(value), "sha256": sha(value)}
                emit("raw_callback_persisted", {"kind": kind, "world_frame": 10, **descriptor})
                cloud = {"kind": kind, "world_frame": 10, "timestamp_s": 1., "measurement_world_transform": p(2.2), "horizontal_angle_rad": 0., "channels": 64, "point_counts_by_channel": [1] + [0] * 63, "channel_byte_offsets": [0] + [stride] * 64, "point_count": 1, "stride_bytes": stride, "layout": layout, "public_iteration_first_records": [list(struct.unpack(layout, value))], "raw": descriptor}
                if kind == "semantic": cloud.update(object_index_counts={"42": 1}, object_tag_counts={"14": 1})
                emit("cloud_callback", cloud)
                cloud["measurement_relative_to_snapshot_ego_matrix"] = p(2.2)["matrix_native"]
                clouds[kind] = cloud
        pair = {"index": 0, "snapshot": {"world_frame": 10, "source_time_s": 1., "delta_seconds": .05, "actors": {"ego": {**ego, "snapshot_transform": p(), "velocity_mps": {"x": 0., "y": 0., "z": 0.}}}}, "clouds": clouds, "clouds_equal_measurement_pose": True, "clouds_equal_horizontal_phase": True,
            "independent_readbacks": {"before_world_frame": 10, "after_world_frame": 10, "vehicles": {"ego": {**ego, "snapshot_transform": p(), "actor_api_transform": p(), "reported_control": {"throttle": 0., "brake": 1., "hand_brake": True, "reverse": False}}}, "sensors": {}}}
        for kind, value in (("regular", regular), ("semantic", semantic)):
            pair["independent_readbacks"]["sensors"][kind] = {**value, "attributes": aa[kind], "public_parent": ego, "actor_api_world_transform": p(2.2), "actor_api_relative_to_ego_matrix": p(2.2)["matrix_native"], "snapshot_status": "observed", "snapshot_world_transform": p(2.2), "snapshot_relative_to_ego_matrix": p(2.2)["matrix_native"]}
        if mutate: mutate(pair)
        if not no_pairs: emit("paired_static_observation", pair)
        status = "failed" if no_pairs else "collected"
        emit("diagnostic_final", {"status": status, "error": None, "cleanup_status": "confirmed", "reservation_retained": False})
        report = {"schema_version": "carla-static-self-hit-diagnostic/1", "diagnostic_id": "authored_fixture_not_actual_carla", "script_sha256": sha(source.read_bytes()), "source_intent_sha256": sha(intent_data), "source_input_hash": intent["input_hash"], "adapter_version": "0.7.0", "sensor_configuration": aa, "limits": {"planned_frame_pairs": 1, "fixed_delta_seconds": .05}, "status": status, "error": None, "cleanup": {"status": "confirmed"}, "reservation_retained": False, "original": original, "pairs": [] if no_pairs else [pair], "event_chain": {"entries": len(events), "head_sha256": previous, "sealed": True}, "callback_shutdown": {"accepting": False, "active": 0, "started": 0 if no_pairs else 2, "finished": 0 if no_pairs else 2, "rejected_after_stop": 0, "drained": True}, "raw_callbacks": {} if no_pairs else {"regular": 1, "semantic": 1}, "raw_bytes": 0 if no_pairs else 40}
        (directory / "events.jsonl").write_bytes(b"".join(canonical(e) + b"\n" for e in events))
        (directory / "report.json").write_bytes(canonical(report))
        return source

    checked = []
    with tempfile.TemporaryDirectory(prefix="self-hit-offline-authored-") as tmp:
        root = Path(tmp)
        for name, mutate, empty in (("complete", None, False), ("frame", lambda p: p["independent_readbacks"].update(before_world_frame=11), False), ("parent", lambda p: p["independent_readbacks"]["sensors"]["semantic"]["public_parent"].update(actor_id=999), False), ("pose", lambda p: p["independent_readbacks"]["sensors"]["semantic"]["actor_api_world_transform"]["matrix_native"][0].__setitem__(3, .01), False), ("empty", None, True)):
            folder = root / name; folder.mkdir(); source = authored_fixture(folder, mutate, empty)
            result = analyze_capture(folder, source)
            if name == "complete":
                assert result["status"] == "verified_complete" and result["totals_on_accepted_pairs_only"]["paired_exact_ego_hits"] == 1
                assert result["totals_on_accepted_pairs_only"]["direct_semantic_ego_hits"] == 1
                assert result["pairs"][0]["positive_near_denominator"] == 1
            else:
                assert result["status"] == "inconclusive_no_accepted_pairs" and result["totals_on_accepted_pairs_only"]["paired_exact_ego_hits"] == 0
                if not empty:
                    assert result["pairs"][0]["regular_denominator"] == 1 and len(result["pairs"][0]["regular_rows"]) == 1
                    assert result["pairs"][0]["regular_rows"][0]["status"] == "not_evaluated_pair"
            checked.append(name)
        folder = root / "complete"
        source = folder / "captured-authoring-example.py"
        report_path = folder / "report.json"
        original_report = report_path.read_bytes()
        unsealed = strict_json(original_report); unsealed["event_chain"]["sealed"] = False
        report_path.write_bytes(canonical(unsealed))
        try: analyze_capture(folder, source)
        except InvalidEvidence: checked.append("unsealed_callback_prefix")
        else: raise AssertionError("Unsealed callbacks accepted")
        report_path.write_bytes(original_report)
        events = (folder / "events.jsonl").read_bytes()
        (folder / "events.jsonl").write_bytes(events.replace(b'"world_id":"12345678901234567890"', b'"world_id":"12345678901234567891"', 1))
        try: analyze_capture(folder, source)
        except InvalidEvidence: checked.append("event_hash_tamper")
        else: raise AssertionError("Tampered event accepted")
        (folder / "events.jsonl").write_bytes(events)
        raw_path = folder / "regular.bin"; raw_path.write_bytes(raw_path.read_bytes() + b"!")
        try: analyze_capture(folder, source)
        except InvalidEvidence: checked.append("raw_truncation_or_size_tamper")
        else: raise AssertionError("Changed raw bytes accepted")
    return {"scope": "Authored offline counterexamples only; no simulator/capture CLI", "checked": ["competing_ids", "channel_mismatch", "signed_zero", "approximate_only", "truncated_record", *checked]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--source-script", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = analyze_capture(args.input, args.source_script)
        code = 0 if result["status"] == "verified_complete" else 1
    except Exception as error:
        result = {"schema_version": VERSION, "status": "invalid_evidence", "errors": [type(error).__name__ + ": " + str(error)], "actor_identity_claims": None, "scope": "Offline validation failed closed; no source writes or engine operations."}
        code = 2
    encoded = canonical(result) + b"\n"
    require(len(encoded) <= 128 * 1024 * 1024, "Analysis output bound exceeded")
    with args.output.open("xb") as stream:
        require(stream.write(encoded) == len(encoded), "Short analysis write")
        stream.flush()
        os.fsync(stream.fileno())
    print(json.dumps({"output": str(args.output), "sha256": sha(encoded), "status": result["status"], "coverage": result.get("coverage"), "totals": result.get("totals_on_accepted_pairs_only")}, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
