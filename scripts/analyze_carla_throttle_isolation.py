#!/usr/bin/env python3
"""Read-only analysis of captured throttle input-isolation diagnostics.

Captured Python files are hashed and copied, never imported or executed.
Results are descriptive input/response observations, not calibration or causal proof.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys

import analyze_carla_brake_response as common
from carla_brake_protocol import DT, canonical, signed_heading, spatial_check

VERSION = "carla-throttle-isolation-analysis/1"
MAX_FILE = common.MAX_FILE
MAX_TOTAL = common.MAX_TOTAL
InvalidEvidence = common.InvalidEvidence
require = common.require
sha = common.sha
strict_json = common.strict_json
read_bounded = common.read_bounded
LIMITATIONS = [
    "Standalone diagnostic evidence; no platform Run, simulator call, quality verdict or calibration is produced by this analyzer.",
    "Historical and new script bundles are separately verified. New diagnostic hooks may differ from the historical runner; identical old runner bytes are not claimed.",
    "A recorded API ACK and float32 cached control match do not establish wheel torque, drivetrain engagement or actuator force.",
    "Prefix requests are replayed exactly. Observations are independently retained; numerical history differences are not silently overwritten.",
    "Direct branch pairing requires the declared common conditions and handoff bounds. Two successful comparisons to the historical anchor do not imply that the two new branches match each other.",
    "Raw RPM and wheel omega values have unverified installed-build units. Missing, nonfinite or incoherent optional data remain gaps.",
    "Sample sign changes compare consecutive nonzero finite raw values. Difference sign changes compare consecutive nonzero finite sample differences. Zeros and missing values break the respective chain. Neither count proves an oscillation mechanism.",
    "All extension samples, stopped intervals, reversals, failures and incomplete denominators remain. No smoothing, resampling, alignment adjustment or gain fitting is performed.",
]


def sequence_summary(values):
    """Exact-sample range and adjacent difference sign changes, with gaps intact."""
    known = [value for value in values if common.finite(value)]
    previous = previous_sign = previous_sample_sign = None
    differences, changes, assessable = [], 0, 0
    sample_changes = sample_pairs = 0
    for value in values:
        if not common.finite(value):
            differences.append(None); previous = previous_sign = previous_sample_sign = None
            continue
        sample_sign = None if value == 0 else 1 if value > 0 else -1
        if sample_sign is not None and previous_sample_sign is not None:
            sample_pairs += 1
            sample_changes += sample_sign != previous_sample_sign
        previous_sample_sign = sample_sign
        delta = None if previous is None else value - previous
        differences.append(delta)
        sign = None if delta is None or delta == 0 else 1 if delta > 0 else -1
        if sign is not None and previous_sign is not None:
            assessable += 1
            changes += sign != previous_sign
        previous, previous_sign = value, sign
    return {"samples": len(values), "available": len(known), "unknown": len(values) - len(known),
            "minimum": min(known, default=None), "maximum": max(known, default=None),
            "range": max(known) - min(known) if known else None, "adjacent_differences": differences,
            "sample_sign_changes": sample_changes, "assessable_adjacent_sample_pairs": sample_pairs,
            "assessable_adjacent_difference_pairs": assessable, "difference_sign_changes": changes,
            "zero_or_missing_breaks_chain": True}


def raw_rpm(row):
    envelope = row.get("telemetry") or {}
    if envelope.get("status") != "available" or envelope.get("before_world_frame") != row["world_frame"] or envelope.get("after_world_frame") != row["world_frame"]:
        return None
    field = envelope.get("scalars", {}).get("engine_rpm") or {}
    return field["value"] if field.get("status") == "available" and field.get("reason") is None and common.finite(field.get("value")) else None


def derived(rows, origin, commands, spawn_height):
    values = common.derive_samples(rows, origin, commands, spawn_height=spawn_height)
    for output, original in zip(values, rows):
        output["raw_engine_rpm"] = raw_rpm(original)
        output["raw_ego"] = deepcopy(original["ego"])
    return values


def summaries(samples):
    keys = ["signed_speed_mps", "vertical_speed_mps", "interval_dv_dt_mps2", "engine_heading_acceleration_mps2",
            "outgoing_attempted_throttle", "reported_throttle", "raw_engine_rpm", *[f"raw_wheel_{i}_omega" for i in range(4)]]
    return {key: sequence_summary([sample[key] for sample in samples]) for key in keys}


def verify_observations(rows, phase, maximum, actor, intent):
    require(type(rows) is list and len(rows) <= maximum, f"{phase} observation denominator exceeds bound")
    if rows:
        require(type(actor) is dict and common.integer(actor.get("actor_id"), 1, 2**32 - 1) and
                actor.get("type_id") == intent["protocol"]["policy"]["vehicle_blueprint"] and
                type(actor.get("role_name")) is str and bool(actor["role_name"]), "Invalid spawned actor identity")
    previous = None
    for index, row in enumerate(rows):
        require(type(row) is dict and type(row.get("index")) is int and row["index"] == index and row.get("phase") == phase,
                f"{phase} index/phase differs")
        require(common.integer(row.get("world_frame")) and common.finite(row.get("source_time_s")) and common.finite(row.get("delta_seconds")),
                f"{phase} frame/time invalid")
        require(abs(row["delta_seconds"] - DT) <= common.TIME_TOLERANCE, f"{phase} source delta differs")
        if previous:
            require(row["world_frame"] == previous["world_frame"] + 1 and abs(row["source_time_s"] - previous["source_time_s"] - DT) <= common.TIME_TOLERANCE,
                    f"{phase} source frame/time gap")
        ego = row.get("ego")
        require(type(ego) is dict and actor is not None and {k: ego.get(k) for k in ("actor_id", "type_id", "role_name")} == actor,
                f"{phase} actor identity differs")
        for key in ("position_m", "forward_vector", "velocity_mps", "acceleration_mps2", "angular_velocity_deg_s"):
            common.verify_vector(ego.get(key))
        common.verify_vector(ego.get("rotation_deg"), ("pitch", "yaw", "roll"))
        transform = ego.get("snapshot_transform") or {}
        require(transform.get("position_m") == ego["position_m"] and transform.get("rotation_deg") == ego["rotation_deg"], "Raw snapshot transform differs")
        speed = signed_heading(ego["velocity_mps"], ego["rotation_deg"]["yaw"])
        require(common.finite(row.get("signed_heading_speed_mps")) and abs(row["signed_heading_speed_mps"] - speed) <= 1e-9,
                "Raw signed heading speed differs")
        common.optional_rpc(row.get("reported_control"), row["world_frame"], control=True)
        common.optional_rpc(row.get("telemetry"), row["world_frame"])
        expected = spatial_check(ego["position_m"], ego["rotation_deg"], ego["velocity_mps"], intent["spawn_transform"],
                                 phase=phase, protocol=intent["protocol"]["controller_protocol"])
        require(row.get("bounds") == expected, "Raw bounds differ from declared controller protocol")
        previous = row


def same_physics(first, second):
    return all(first.get(key) == second.get(key) for key in
               ("world_frame", "source_time_s", "delta_seconds", "ego", "signed_heading_speed_mps", "bounds"))


def verified_bundle(directories, expected):
    require(type(expected) is dict and expected and all(Path(name).name == name and common.digest(value) for name, value in expected.items()),
            "Invalid captured source descriptor")
    for directory in directories:
        try:
            content = {name: read_bounded(Path(directory) / name, limit=2 * 1024 * 1024) for name in expected}
            if all(sha(content[name]) == digest for name, digest in expected.items()):
                return {"source-" + name: data for name, data in content.items()}, str(Path(directory).resolve())
        except (InvalidEvidence, OSError):
            continue
    raise InvalidEvidence("No whole captured source bundle matches all pinned hashes")


def load_reference(capture, source_dir, source_evidence):
    from carla_throttle_isolation_protocol import reference_from_report
    from prepare_carla_throttle_isolation_intent import require_reference
    old_analysis, raw = common.load_trial(capture, source_dir, source_evidence)
    require(old_analysis["integrity_status"] == "verified", "Historical reference is not verified")
    report, intent = strict_json(raw["report.json"]), strict_json(raw["source-intent.json"])
    require_reference(old_analysis, report, intent)
    names = ("source-intent.json", "report.json", "events.jsonl", "reservation-release.json")
    require(all(name in raw for name in names), "Historical reference lacks its four sealed/release artifacts")
    reference = reference_from_report(report, intent, {name: sha(raw[name]) for name in names})
    return {"reference": reference, "report": report, "intent": intent, "analysis": old_analysis, "raw": raw}


def verify_receipt(directory, report, intent, raw):
    path = Path(directory) / "reservation-release.json"
    if not path.exists():
        return {"status": "not_confirmed", "publication_time_reservation_retained": report.get("reservation_retained")}
    content = read_bounded(path, limit=64 * 1024)
    receipt = strict_json(content)
    require(type(receipt) is dict and set(receipt) == {"schema_version", "experiment_id", "diagnostic_id", "store_id", "report_sha256", "status", "released_at"},
            "Release receipt shape differs")
    require(receipt.get("schema_version") == "carla-brake-reservation-release/1" and receipt.get("status") == "released" and
            receipt.get("experiment_id") == intent["experiment_id"] and receipt.get("diagnostic_id") == report["diagnostic_id"] and
            receipt.get("store_id") == intent["expected_store_id"] and receipt.get("report_sha256") == sha(raw["report.json"]),
            "Release receipt identity/hash differs")
    require(report.get("release_eligible") is True and report.get("reservation_retained") is True and report["cleanup"]["status"] == "confirmed",
            "Release receipt contradicts sealed publication")
    raw["reservation-release.json"] = content
    return {"status": "release_receipt_verified", "receipt": receipt, "scope": "Recorded receipt, not a current remote/world query."}


HTML = r'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Throttle input isolation · 입력 분리 관찰</title><style>
*{box-sizing:border-box}body{margin:0;background:#eef2f5;color:#193043;font:15px/1.5 system-ui,sans-serif}main{max-width:1380px;margin:auto;padding:24px}h1{font-size:27px;margin-bottom:8px}h2{font-size:19px}.panel{background:white;border:1px solid #cdd9e1;border-radius:8px;padding:18px;margin:18px 0}.warning{background:#fff1d6;padding:12px;border-left:4px solid #b97900}.muted{color:#536b7e}label{display:inline-flex;gap:8px;margin:5px 18px 5px 0}select,button{font:inherit;padding:5px;background:white;border:1px solid #acbac7;border-radius:4px}svg{width:100%;height:250px;display:block}table{border-collapse:collapse;width:100%;font-size:12px}th,td{padding:7px;border-bottom:1px solid #dce5eb;text-align:left;vertical-align:top;overflow-wrap:anywhere}th{background:#eff4f8;position:sticky;top:0}.scroll{overflow:auto;max-height:460px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f0f5f7;padding:10px;max-height:350px;overflow:auto}code{overflow-wrap:anywhere;font-size:12px}a{color:#075e94}summary{cursor:pointer}details{margin:10px 0}.bad{color:#a13521}#cursor{min-height:30px}@media(max-width:600px){main{padding:10px}.panel{padding:10px}}
</style><main><h1>Throttle input isolation · 입력 분리 관찰</h1>
<p class="warning">진단 기록의 비교입니다. Calibration, vehicle-quality acceptance and proof of a physical cause are not established. No filtering, smoothing or forced alignment.</p>
<section class="panel"><h2>Trials / 실행 선택</h2><div id="trials"></div><label>Phase <select id="phase"><option value="measurement">Extension / 추가 40명령·41관측</option><option value="preparation">Replayed preparation / 준비 입력 재생</option><option value="rest">Rest / 정차</option><option value="all">All retained / 전체 원본</option></select></label><div class="scroll" id="summary"></div></section>
<section class="panel"><h2>Branch comparison / 두 분기 비교</h2><div id="comparison"></div></section>
<section class="panel"><h2>Signed speed / 종방향 부호 속도 · m/s</h2><p class="muted">Solid: horizontal heading projection. Dashed: raw vertical velocity. Time zero is extension F0 when present; otherwise the final retained frame.</p><svg id="speed" aria-label="Signed and vertical speed"></svg>
<h2>Acceleration / 가속도 · m/s²</h2><p class="muted">Solid: interval Δv/Δt. Dashed: engine acceleration projected onto current horizontal heading. The first interval is unknown.</p><svg id="acceleration" aria-label="Interval and engine acceleration"></svg>
<h2>Throttle / 구동 명령 · normalized</h2><p class="muted">Solid: attempted requested control following frame N. Dashed: cached reported control in that frame, normally from command N−1. ACK and force are different.</p><svg id="throttle" aria-label="Requested and cached throttle"></svg>
<h2>Engine RPM / 엔진 API 값 · units unverified</h2><svg id="rpm" aria-label="Raw engine RPM"></svg>
<h2>Wheel omega / 바퀴 API 값 · units unverified</h2><p class="muted">Trial colors; wheel 0 solid, 1 short dash, 2 long dash, 3 dash-dot. Unavailable and nonfinite fields remain gaps.</p><svg id="omega" aria-label="Raw wheel omega"></svg><p id="cursor" class="muted">Hover to inspect exact world frames and raw values.</p></section>
<section class="panel"><h2>All selected samples / 선택 구간 전체 표본</h2><div class="scroll" id="samples"></div></section>
<section class="panel"><h2>Ranges and sign changes / 범위·차분 부호 변화</h2><p class="muted">Sample sign changes: adjacent nonzero finite raw values (for acceleration, direction of acceleration). Difference sign changes: adjacent nonzero finite changes (for acceleration, direction of its change). Zero and unknown values break the respective chain. No deadband or smoothing.</p><div id="statistics"></div></section>
<section class="panel"><h2>Original evidence downloads / 원본 다운로드</h2><div id="sources"></div></section>
<section class="panel"><h2>Interpretation limits / 해석 경계</h2><ul id="limits"></ul></section><a href="analysis.json">Download analysis.json</a> · Self-contained; no CDN, external network or runtime library.</main>
<script id="payload" type="application/json">__DATA__</script><script>
'use strict';const data=JSON.parse(document.getElementById('payload').textContent),colors=['#136bab','#cd6020','#008875','#8642a0'];const el=id=>document.getElementById(id),esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),txt=(v,n=5)=>v===null||v===undefined?'unknown':typeof v==='number'?Number(v.toFixed(n)).toString():String(v),label=t=>t.label||t.experiment_id||t.input_directory;let selected=new Set(data.trials.map((_,i)=>i));
el('trials').innerHTML=data.trials.map((t,i)=>`<label style="color:${colors[i%colors.length]}"><input data-index="${i}" type="checkbox" checked>${esc(label(t))}</label>`).join('');el('trials').addEventListener('change',e=>{const i=Number(e.target.dataset.index);e.target.checked?selected.add(i):selected.delete(i);render()});el('phase').onchange=render;
el('summary').innerHTML='<table><thead><tr><th>Trial / outcome</th><th>Replayed prefix</th><th>Extension denominator</th><th>Handoff / cleanup</th></tr></thead><tbody>'+data.trials.map(t=>`<tr><td>${esc(label(t))}<br>${esc(t.integrity_status)} / ${esc(t.diagnostic_status||'unverified')}<p class="bad">${esc(t.verification_error||'')}${t.error?esc(JSON.stringify(t.error)):''}</p></td><td>${txt(t.prefix?.completed_transitions)} / ${txt(t.prefix?.expected_commands)} completed commands<br>${txt(t.prefix?.observations)} / ${txt(t.prefix?.expected_observations)} observations<details><summary>Inspect prefix counts</summary><pre>${esc(JSON.stringify(t.prefix||{},null,2))}</pre></details></td><td>${txt(t.extension?.completed_transitions)} / ${txt(t.extension?.expected_commands)} completed commands<br>${txt(t.extension?.observations)} / ${txt(t.extension?.expected_observations)} observations<details><summary>Inspect extension counts</summary><pre>${esc(JSON.stringify(t.extension||{},null,2))}</pre></details></td><td>Source handoff: ${txt(t.handoff?.prefix?.admitted)}<br>Fresh handoff: ${txt(t.handoff?.fresh?.admitted)}<details><summary>Inspect gates</summary><pre>${esc(JSON.stringify(t.handoff||{},null,2))}</pre></details><br>${esc(t.cleanup?.status||'unknown')} / ${esc(t.reservation_release?.status||'unknown')}</td></tr>`).join('')+'</tbody></table>';
el('comparison').innerHTML=`<strong>${esc(data.comparison.status)}</strong><ul>${data.comparison.mismatches.map(v=>`<li>${esc(v)}</li>`).join('')}</ul><p class="muted">${esc(data.comparison.interpretation)}</p><details><summary>All comparison checks and retained differences</summary><pre>${esc(JSON.stringify(data.comparison,null,2))}</pre></details>`;
el('limits').innerHTML=data.limitations.map(v=>`<li>${esc(v)}</li>`).join('');el('sources').innerHTML=[{label:'Analysis tools / 로컬 분석 코드',source_files:data.analysis_tools},{label:'Historical reference / 고정 원본',source_files:data.reference.source_files},...data.trials].map(t=>`<details><summary>${esc(label(t))}</summary>${(t.source_files||[]).map(f=>`<p><a href="${esc(f.path)}">${esc(f.name||f.path)}</a> · ${f.size_bytes} bytes<br><code>${esc(f.sha256)}</code></p>`).join('')}</details>`).join('');el('statistics').innerHTML=data.trials.map(t=>`<details><summary>${esc(label(t))} · extension acceleration sample sign changes: interval ${txt(t.statistics?.extension?.interval_dv_dt_mps2?.sample_sign_changes)}, engine ${txt(t.statistics?.extension?.engine_heading_acceleration_mps2?.sample_sign_changes)}</summary><pre>${esc(JSON.stringify(t.statistics||{},null,2))}</pre></details>`).join('');
function items(){const phase=el('phase').value;return data.trials.flatMap((t,i)=>selected.has(i)&&t.integrity_status==='verified'?[{trial:t,color:colors[i%colors.length],rows:phase==='all'?[...t.inventory_samples,...t.rest_samples,...t.preparation_samples,...t.samples]:phase==='preparation'?t.preparation_samples:phase==='rest'?t.rest_samples:t.samples}]:[])}
function chart(id,series,fields){const svg=el(id),w=Math.max(520,svg.clientWidth),h=250,L=80,R=28,T=16,B=34,rows=series.flatMap(s=>s.rows),xx=rows.map(r=>r.time_s),yy=rows.flatMap(r=>fields.map(f=>r[f[0]])).filter(Number.isFinite);let xmin=xx.length?Math.min(...xx):0,xmax=xx.length?Math.max(...xx):2,ymin=yy.length?Math.min(0,...yy):0,ymax=yy.length?Math.max(...yy):1;if(xmax<=xmin)xmax=xmin+1;if(ymax<=ymin)ymax=ymin+1;const pad=(ymax-ymin)*.08;ymin-=pad;ymax+=pad;const X=x=>L+(x-xmin)/(xmax-xmin)*(w-L-R),Y=y=>h-B-(y-ymin)/(ymax-ymin)*(h-T-B);let s='';for(let i=0;i<=5;i++){const v=ymin+(ymax-ymin)*i/5,y=Y(v);s+=`<line x1="${L}" x2="${w-R}" y1="${y}" y2="${y}" stroke="#dfe8ed"/><text x="${L-8}" y="${y+4}" text-anchor="end" font-size="11">${txt(v,3)}</text>`}for(let i=0;i<=5;i++){const v=xmin+(xmax-xmin)*i/5;s+=`<text x="${X(v)}" y="${h-10}" text-anchor="middle" font-size="11">${txt(v,2)}</text>`}for(const item of series)for(const[key,dash]of fields){let d='',prev=null;for(const row of item.rows){if(!Number.isFinite(row[key])){prev=null;continue}d+=(prev&&row.phase===prev.phase&&row.index===prev.index+1?'L':'M')+X(row.time_s)+','+Y(row[key])+' ';prev=row}s+=`<path d="${d}" fill="none" stroke="${item.color}" stroke-width="1.7" ${dash?`stroke-dasharray="${dash}"`:''}/>`}svg.setAttribute('viewBox',`0 0 ${w} ${h}`);svg.innerHTML=s;svg.onpointermove=e=>{const rect=svg.getBoundingClientRect(),time=xmin+((e.clientX-rect.left)*w/rect.width-L)/(w-L-R)*(xmax-xmin);el('cursor').textContent=series.map(item=>{const r=item.rows.reduce((a,b)=>!a||Math.abs(b.time_s-time)<Math.abs(a.time_s-time)?b:a,null);return r?`${label(item.trial)} ${r.phase} #${r.index} world ${r.world_frame}, t ${txt(r.time_s)}, v ${txt(r.signed_speed_mps)}, interval a ${txt(r.interval_dv_dt_mps2)}, engine a ${txt(r.engine_heading_acceleration_mps2)}, requested throttle ${txt(r.outgoing_requested_throttle)}, cached ${txt(r.reported_throttle)}, RPM ${txt(r.raw_engine_rpm)}, omega [${[0,1,2,3].map(i=>txt(r['raw_wheel_'+i+'_omega'])).join(', ')}]`:''}).join(' | ')}}
function render(){el('cursor').textContent='';const series=items();chart('speed',series,[['signed_speed_mps',''],['vertical_speed_mps','5 4']]);chart('acceleration',series,[['interval_dv_dt_mps2',''],['engine_heading_acceleration_mps2','5 4']]);chart('throttle',series,[['outgoing_attempted_throttle',''],['reported_throttle','5 4']]);chart('rpm',series,[['raw_engine_rpm','']]);chart('omega',series,[['raw_wheel_0_omega',''],['raw_wheel_1_omega','2 3'],['raw_wheel_2_omega','8 3'],['raw_wheel_3_omega','10 3 2 3']]);el('samples').innerHTML='<table><thead><tr><th>Trial / phase / index</th><th>World frame / source time</th><th>v / interval a / engine a</th><th>Throttle requested / cached</th><th>Raw original fields</th></tr></thead><tbody>'+series.flatMap(s=>s.rows.map(r=>`<tr><td>${esc(label(s.trial))}<br>${r.phase} ${r.index}</td><td>${r.world_frame}<br>${r.source_time_s}</td><td>${txt(r.signed_speed_mps)} / ${txt(r.interval_dv_dt_mps2)} / ${txt(r.engine_heading_acceleration_mps2)}</td><td>${txt(r.outgoing_requested_throttle)} / ${txt(r.reported_throttle)}<br>${esc(r.outgoing_application_status||'none')}</td><td><details><summary>Inspect original values</summary><pre>${esc(JSON.stringify({ego:r.raw_ego,control:r.reported_control,telemetry:r.telemetry,bounds:r.bounds},null,2))}</pre></details></td></tr>`)).join('')+'</tbody></table>'}render();window.addEventListener('resize',render);
</script></html>'''


def write_output(output, result):
    data = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False).encode()
    require(len(data) <= MAX_FILE, "Analysis output exceeds bounded size")
    common.write_exclusive(output / "analysis.json", data)
    safe = data.decode().replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    common.write_exclusive(output / "index.html", HTML.replace("__DATA__", safe).encode())


def build_report(trials, source_dirs, reference_capture, reference_sources, source_evidence, output):
    require(0 < len(trials) <= 16 and len({str(Path(p).resolve()) for p in trials}) == len(trials), "One to 16 distinct trial directories required")
    output = Path(output)
    require(not output.exists(), "Output directory exists; refusing to overwrite evidence")
    reference = load_reference(reference_capture, reference_sources, source_evidence)
    output.mkdir(parents=True, exist_ok=False)
    result = {"schema_version": VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
              "analyzer_sha256": sha(Path(__file__).read_bytes()), "trials": [], "limitations": LIMITATIONS,
              "scope": "Offline source verification and conditional descriptive response comparison only."}
    total = 0
    def copy_raw(name, raw):
        nonlocal total
        folder = output / name; folder.mkdir()
        files = []
        for filename, content in raw.items():
            require(Path(filename).name == filename, "Unsafe output filename")
            total += len(content); require(total <= MAX_TOTAL, "Aggregate output exceeds 256 MiB")
            common.write_exclusive(folder / filename, content)
            files.append({"name": filename, **common.file_info(content, f"{name}/{filename}")})
        return files
    tool_names = ("analyze_carla_throttle_isolation.py", "analyze_carla_brake_response.py", "carla_brake_protocol.py",
                  "carla_throttle_isolation_protocol.py", "prepare_carla_throttle_isolation_intent.py")
    result["analysis_tools"] = copy_raw("analysis-tools", {name: read_bounded(Path(__file__).resolve().parent / name) for name in tool_names})
    result["reference"] = {"experiment_id": reference["reference"]["experiment_id"], "reference": reference["reference"],
                           "source_files": copy_raw("reference", reference["raw"])}
    for index, directory in enumerate(trials):
        raw = {}
        try:
            trial, raw = load_trial(directory, source_dirs, reference)
        except (InvalidEvidence, ValueError, KeyError, TypeError, IndexError, OSError, OverflowError) as error:
            trial = {"integrity_status": "invalid_evidence", "verification_error": type(error).__name__ + ": " + str(error)}
            for name in ("source-intent.json", "report.json", "events.jsonl", "reservation-release.json"):
                try: raw[name] = read_bounded(Path(directory) / name)
                except (InvalidEvidence, OSError): pass
        trial["input_directory"] = str(Path(directory).resolve())
        trial["label"] = trial.get("branch", "unverified") + " · " + trial.get("experiment_id", Path(directory).name)
        physics = trial.get('continuation_physics') or {}
        substep = physics.get('requested', {}).get('max_substep_delta_time_s')
        if substep in (.01, .005):
            trial['label'] = f"{substep:.3f}s {'sham / 대조' if substep == .01 else 'treatment / 변경'} · " + trial['experiment_id']
        trial["source_files"] = copy_raw(f"trial-{index+1:02d}", raw)
        result["trials"].append(trial)
    result["comparison"] = compare_trials(result["trials"])
    result["counts"] = {"trials": len(trials), "verified": sum(t["integrity_status"] == "verified" for t in result["trials"]),
        "invalid": sum(t["integrity_status"] != "verified" for t in result["trials"]),
        "collected": sum(t.get("diagnostic_status") == "collected" for t in result["trials"]),
        "failed_prefixes": sum(t.get("diagnostic_status") == "failed" for t in result["trials"])}
    result["status"] = "verified" if not result["counts"]["invalid"] else "invalid_evidence_present"
    write_output(output, result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", type=Path, action="append", required=True)
    parser.add_argument("--source-dir", type=Path, action="append", required=True)
    parser.add_argument("--reference-capture", type=Path, required=True)
    parser.add_argument("--reference-source-dir", type=Path, action="append", required=True)
    parser.add_argument("--source-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New exclusive output directory")
    args = parser.parse_args(argv)
    try:
        report = build_report(args.trial, args.source_dir, args.reference_capture, args.reference_source_dir, args.source_evidence, args.output)
    except (InvalidEvidence, ValueError, TypeError, KeyError, OSError) as error:
        print(json.dumps({"status": "error", "error": str(error)}), file=sys.stderr)
        return 2
    print(json.dumps({"status": report["status"], "counts": report["counts"], "comparison": report["comparison"]["status"], "output": str(args.output)}))
    return 0 if report["status"] == "verified" else 2


def verify_stage(stage, phase, expected_count, actor_id, intent, *, state=None):
    from carla_throttle_isolation_protocol import prefix_step, extension_step
    protocol, reference = intent["protocol"], intent["reference"]
    rows, commands = stage.get("observations"), stage.get("commands")
    proposal_key = "pi_steps" if phase == "preparation" else "control_steps"
    proposals = stage.get(proposal_key)
    require(stage.get("expected_commands") == expected_count and stage.get("expected_observations") == expected_count + 1,
            phase + " expected denominator differs")
    require(type(commands) is list and len(commands) == expected_count and type(proposals) is list and len(proposals) <= expected_count,
            phase + " command/proposal denominator differs")
    require("pi_state" in stage and "handoff_gate" in stage, phase + " explicit state/handoff is missing")
    require(len(rows) <= len(proposals) + 1, phase + " observations exceed proposed control prefix")
    applied, ended = 0, False
    for i, command in enumerate(commands):
        require(type(command) is dict and type(command.get("step")) is int and command["step"] == i and command.get("time_s") == i * DT,
                phase + " command index/time differs")
        status = command.get("application_status")
        require(status in common.COMMAND_STATES, phase + " command status unknown")
        if phase == "preparation":
            require(canonical(command.get("control")) == canonical(reference["controls"][i]), "Replayed source control bytes differ")
        if i >= len(proposals):
            require(status == "not_attempted" and all(command.get(k) is None for k in
                ("world_frame", "next_world_frame", "next_observation_index", "rpc_before_world_frame", "rpc_after_world_frame", "rpc_response")),
                "Unproposed command claims dispatch/observation")
            if phase == "measurement": require(command.get("control") is None, "Unproposed extension command invents control")
            continue
        require(not ended and i < len(rows), "Control proposal lacks its available source observation")
        source = rows[i]
        expected = prefix_step(protocol, reference, i, source, state) if phase == "preparation" else extension_step(
            protocol, i, source, state, reference["controls"][-1])
        require(canonical(proposals[i]) == canonical({"command_index": i, **expected}), "Control proposal/state/trace differs from raw observations")
        require(canonical(command.get("control")) == canonical(expected["control"]), "Proposed control differs from stored request")
        if not expected.get("matched", True):
            require(status == "not_attempted", "Unmatched prefix proposal was dispatched")
        if status != "not_attempted":
            require(command.get("world_frame") == command.get("rpc_before_world_frame") == source["world_frame"], "Command source frame differs")
        if status in ("sent_acknowledged", "observed_next_frame"):
            require(command.get("rpc_response") == {"actor_id": actor_id, "error": None}, "Acknowledged actor identity differs")
        if status == "observed_next_frame":
            require(i + 1 < len(rows), "Completed command lacks next observation")
            common.verify_transition(command, source, rows[i + 1], actor_id)
            state, applied = deepcopy(expected["state"]), applied + 1
        else:
            ended = True
            require(i == len(proposals) - 1 and command.get("next_world_frame") is None and command.get("next_observation_index") is None,
                    "Unresolved command claims next observation or continued execution")
            require(status == "sent_acknowledged" or len(rows) == i + 1, "Unacknowledged control claims subsequent physics")
    require(canonical(stage.get("pi_state")) == canonical(state), phase + " committed state differs from completed transitions")
    return {"expected_commands": expected_count, "expected_observations": expected_count + 1,
            "proposals": len(proposals), "observations": len(rows), "completed_transitions": applied,
            "command_states": dict(Counter(c["application_status"] for c in commands))}, state


def verify_rest_before_preparation(report, intent, actor_id):
    # Same independently recomputed startup contract as the brake diagnostic.
    # Isolation changes preparation/extension only; no rest result is inferred
    # from an author-supplied status string.
    rest = report["rest"]
    rows, trace = rest.get("observations", []), rest.get("trace", [])
    require(rest.get("status") == "passed" and 8 <= len(rows) == len(trace) <= 100, "Rest denominator/status differs")
    policy = {"version": "carla-startup-stability/1", "min_ticks": 8, "max_ticks": 100,
        "consecutive_stable_samples": 5, "max_linear_speed_mps": .03, "max_vertical_speed_mps": .05,
        "max_angular_speed_deg_s": 1., "max_position_step_m": .005, "max_rotation_step_deg": .05,
        "max_acceleration_mps2": .1, "max_wall_time_s": 60., "max_tick_rpc_time_s": 10., "clock_tolerance_s": 1e-6}
    require(canonical(rest.get("policy")) == canonical(policy), "Rest stability policy differs")
    streak, previous = 0, None
    for i, (sample, row) in enumerate(zip(trace, rows)):
        ego, actor = row["ego"], sample.get("actor") or {}
        require(sample.get("tick") == i + 1 and sample.get("world_frame") == row["world_frame"] and sample.get("source_time_s") == row["source_time_s"],
                "Rest stability sample identity differs")
        require(actor.get("actor_id") == actor_id and actor.get("invalid_fields") == [], "Rest actor invalid")
        require(all(actor.get(k) == ego[k] for k in ("position_m", "rotation_deg", "velocity_mps", "angular_velocity_deg_s", "acceleration_mps2")), "Rest raw actor differs")
        position_step = math.dist([ego["position_m"][k] for k in ("x", "y", "z")], [previous["position_m"][k] for k in ("x", "y", "z")]) if previous else None
        rotation_step = max(abs((ego["rotation_deg"][k] - previous["rotation_deg"][k] + 180) % 360 - 180) for k in ("pitch", "yaw", "roll")) if previous else None
        values = {"linear_speed_mps": math.hypot(*ego["velocity_mps"].values()), "absolute_vertical_speed_mps": abs(ego["velocity_mps"]["z"]),
            "angular_speed_deg_s": math.hypot(*ego["angular_velocity_deg_s"].values()), "acceleration_norm_mps2": math.hypot(*ego["acceleration_mps2"].values()),
            "position_step_m": position_step, "rotation_step_deg": rotation_step}
        criteria = {"linear_speed": values["linear_speed_mps"] <= .03, "vertical_speed": values["absolute_vertical_speed_mps"] <= .05,
            "angular_speed": values["angular_speed_deg_s"] <= 1., "acceleration": values["acceleration_norm_mps2"] <= .1,
            "position_step": position_step is not None and position_step <= .005, "rotation_step": rotation_step is not None and rotation_step <= .05}
        stable = all(criteria.values()); streak = streak + 1 if stable else 0
        require(actor.get("derived") == values and actor.get("criteria") == criteria and actor.get("stable") is stable and sample.get("stable_streak") == streak,
                "Rest stability does not follow raw observations")
        require(common.control_comparison(intent["protocol"]["policy"]["rest_control"], row.get("reported_control"))["status"] == "matched", "Rest cached control mismatch")
        previous = ego
    require(streak >= 5 and rest.get("stable_streak") == streak, "Rest stable streak differs")
    initial = rest.get("initial_control") or {}
    require(canonical(initial.get("control")) == canonical(intent["protocol"]["policy"]["rest_control"]), "Rest request differs")
    refresh = report.get("inventory_refresh_observations", [])
    common.verify_transition(initial, {"world_frame": initial.get("world_frame")}, refresh[0] if refresh else rows[0], actor_id)
    prepared = report["preparation"]["observations"]
    require(prepared and all(prepared[0][k] == rows[-1][k] for k in ("world_frame", "source_time_s", "delta_seconds", "ego")), "Rest/preparation raw boundary differs")


def analyze_report(report, intent):
    from carla_throttle_isolation_protocol import report_version, compare_entry, handoff_gate, MODULES
    protocol, reference = intent["protocol"], intent["reference"]
    require(type(report) is dict and report.get("schema_version") == report_version(protocol) and report.get("status") in ("collected", "failed"), "Unsupported/nonterminal isolation report")
    for key in ("experiment_id", "protocol_sha256", "sequence_sha256", "source_files", "engine_build", "adapter_version", "source_evidence", "requested_weather"):
        require(report.get(key) == intent[key], "Report/intent differs: " + key)
    require(report.get("intent_sha256") == sha(canonical(intent)) and report.get("store_id") == intent["expected_store_id"], "Intent/store identity differs")
    require(report.get("diagnostic_id") == "diagnostic_brake_" + intent["experiment_id"].replace("-", ""), "Diagnostic identity differs")
    require(canonical(report.get("isolation_protocol")) == canonical(protocol) and canonical(report.get("reference")) == canonical(reference), "Saved protocol/reference differs")
    require(report.get("coordinate_convention") == "carla_left_handed_world" and report.get("angular_velocity_basis") == "official_carla_0.9.16_degrees_per_second_installed_build_separate", "Coordinate/API declaration differs")
    require(type(report.get("reservation_retained")) is bool and type(report.get("cleanup")) is dict and report["cleanup"].get("status") in ("confirmed", "uncertain", "not_started"), "Cleanup/reservation state differs")
    complete = report["status"] == "collected"
    prep, extension, rest = report["preparation"], report["measurement"], report["rest"]
    require(prep.get("mode") == "throttle" and prep.get("velocity_request") is None, "Isolation preparation contains velocity injection")
    actor = report.get("spawn", {}).get("ego", {}).get("returned_identity")
    n = protocol["prefix_steps"]
    for rows, phase, maximum in ((rest.get("observations", []), "rest", 101), (report.get("inventory_refresh_observations", []), "inventory_refresh", 1),
            (prep["observations"], "preparation", n + 1), (extension["observations"], "measurement", 41)):
        verify_observations(rows, phase, maximum, actor, intent)
        if complete: require(all(row["bounds"]["status"] == "within_bounds" for row in rows), "Collected trial violates bounds")
    require(type(report.get("inventory_refresh_ticks", 0)) is int and report.get("inventory_refresh_ticks", 0) in (0, 1) and
            report.get("inventory_refresh_ticks", 0) == len(report.get("inventory_refresh_observations", [])), "Inventory refresh denominator differs")
    actor_id = actor["actor_id"] if actor else None
    prefix, state = verify_stage(prep, "preparation", n, actor_id, intent)
    extension_initialized = extension.get("pi_state") is not None or bool(extension.get("control_steps"))
    count, final_state = verify_stage(extension, "measurement", 40, actor_id, intent, state=state if extension_initialized else None)
    verify_continuation_physics(report, intent)
    observed_preparation = prep["observations"]
    if observed_preparation:
        verify_rest_before_preparation(report, intent, actor_id)
        expected_initial = compare_entry(protocol, observed_preparation[0], reference["initial_observation"], rotating=False)
        if prep.get("initial_gate") is not None:
            require(canonical(prep["initial_gate"]) == canonical(expected_initial), "Initial source-anchor gate differs")
        if prefix["proposals"]:
            require(prep.get("initial_gate") is not None and expected_initial["admitted"], "Prefix proposed before initial source agreement")
    if prep.get("handoff_gate") is not None:
        expected = handoff_gate(protocol, reference, observed_preparation, len(rest["trace"]), report.get("inventory_refresh_ticks", 0))
        require(canonical(prep["handoff_gate"]) == canonical(expected), "Stored source-anchor handoff differs")
    if extension["observations"]:
        require(prefix["completed_transitions"] == n and prep.get("status") == "passed" and (prep.get("handoff_gate") or {}).get("admitted") is True,
                "Extension observed before full prefix/source handoff")
        first = extension["observations"][0]
        require(same_physics(first, observed_preparation[-1]), "Fresh handoff F0 raw physics/frame differs")
        expected = handoff_gate(protocol, reference, [*observed_preparation[:-1], first], len(rest["trace"]), report.get("inventory_refresh_ticks", 0))
        if extension.get("handoff_gate") is not None:
            require(canonical(extension["handoff_gate"]) == canonical(expected) and extension.get("entry_observation_frame") == first["world_frame"], "Fresh handoff gate/frame differs")
        if extension_initialized:
            require(extension.get("handoff_gate") is not None and expected["admitted"], "Extension state initialized after rejected/unknown fresh handoff")
            require(common.control_comparison(reference["controls"][-1], first.get("reported_control"))["status"] == "matched", "Fresh handoff cached control differs")
    require(not extension_initialized or len(extension["observations"]) > 0, "Extension state has no handoff source")
    config = report.get("physics_configuration") or {}
    physics_matched = config.get("status") == "available" and config.get("friction_status") == "matched" and canonical({k: config.get(k) for k in ("scalars", "wheels")}) == canonical(intent["expected_physics"])
    if observed_preparation:
        require(physics_matched, "Prepared trial physical configuration differs")
        require(report.get("installed_module_hashes") == reference["installed_module_hashes"], "Installed native modules differ from reference")
    if complete:
        require(report.get("error") is None and report["cleanup"]["status"] == "confirmed" and report.get("original") == intent["expected_world"] and not report.get("collision_callbacks"), "Collected outcome/world/contact state differs")
        require(prefix["completed_transitions"] == n and count["completed_transitions"] == 40 and count["observations"] == 41, "Collected trial lacks full prefix and 40/41 extension")
        shutdown = report.get("callback_shutdown") or {}
        require(shutdown.get("accepting") is False and shutdown.get("active") == 0 and shutdown.get("drained") is True and
                common.integer(shutdown.get("started")) and shutdown["started"] == shutdown.get("finished"), "Collected callbacks not drained")
    retained = extension["observations"] or observed_preparation or rest.get("observations", [])
    origin = extension["observations"][0]["source_time_s"] if extension["observations"] else retained[-1]["source_time_s"] if retained else 0.
    height = intent["spawn_transform"]["position_m"]["z"]
    # Unproposed extension requests are null; omit only from the drawing helper,
    # preserving all forty templates and proposal counts in the report evidence.
    filled = [c for c in extension["commands"] if c.get("control") is not None]
    samples = derived(extension["observations"], origin, filled, height)
    pre_samples = derived(observed_preparation, origin, prep["commands"], height)
    return {"integrity_status": "verified", "experiment_id": intent["experiment_id"], "branch": protocol["branch"],
        "diagnostic_status": report["status"], "error": deepcopy(report.get("error")), "prefix": prefix, "extension": count,
        "continuation_physics": deepcopy(report.get("continuation_physics")),
        "handoff": {"initial": deepcopy(prep.get("initial_gate")), "prefix": deepcopy(prep.get("handoff_gate")), "fresh": deepcopy(extension.get("handoff_gate"))},
        "cleanup": deepcopy(report["cleanup"]), "samples": samples, "preparation_samples": pre_samples,
        "rest_samples": derived(rest.get("observations", []), origin, None, height),
        "inventory_samples": derived(report.get("inventory_refresh_observations", []), origin, None, height),
        "statistics": {"extension": summaries(samples), "preparation": summaries(pre_samples)},
        "pair_evidence": {"intent": deepcopy(intent), "installed_module_hashes": deepcopy(report.get("installed_module_hashes")),
            "prefix_controls": [deepcopy(c["control"]) for c in prep["commands"]],
            "prefix_gears": [((row.get("reported_control") or {}).get("data") or {}).get("gear") for row in observed_preparation],
            "entry": deepcopy(extension["observations"][0]) if extension["observations"] else None},
        "scope": "Standalone input isolation. Execution completion and physical interpretation remain separate."}


def load_trial(directory, source_dirs, reference):
    from carla_throttle_isolation_protocol import parse_intent, BASE_SOURCES
    directory = Path(directory)
    raw = {name: read_bounded(directory / name) for name in ("source-intent.json", "report.json", "events.jsonl")}
    intent = parse_intent(raw["source-intent.json"])
    require(canonical(intent["reference"]) == canonical(reference["reference"]), "Original captured reference reconstruction differs")
    bundle, source_directory = verified_bundle(source_dirs, intent["source_files"]); raw.update(bundle)
    report = strict_json(raw["report.json"])
    events = common.verify_chain(raw["events.jsonl"], report, intent)
    result = analyze_report(report, intent)
    result["source_identity"] = {"historical_shared_files_differing": [name for name in BASE_SOURCES if intent["source_files"][name] != intent["reference"]["source_files"][name]],
        "scope": "Historical and new source bundles are verified separately. New runner hooks may differ; pairing requires both new branches to use the same exact five source files and installed modules."}
    result.update(event_count=len(events), event_head_sha256=report["event_chain"]["head_sha256"], source_bundle_directory=source_directory,
                  reservation_release=verify_receipt(directory, report, intent, raw))
    return result, raw


def compare_trials(trials):
    from carla_throttle_isolation_protocol import compare_entry
    if len(trials) == 2 and all(t.get('branch') == 'hold_throttle' and t.get('continuation_physics') for t in trials):
        return compare_substep_trials(trials)
    reasons, checks, aligned = [], {}, []
    def check(condition, reason):
        if not condition: reasons.append(reason)
    left = [t for t in trials if t.get("branch") == "continue_pi"]
    right = [t for t in trials if t.get("branch") == "hold_throttle"]
    check(len(trials) == 2 and len(left) == len(right) == 1, "Exactly one continue_pi and one hold_throttle trial required; no automatic subset selection")
    if len(trials) == 2 and len(left) == len(right) == 1:
        a, b = left[0], right[0]
        for name, trial in (("continue_pi", a), ("hold_throttle", b)):
            check(trial.get("integrity_status") == "verified", name + " evidence is not verified")
            check(trial.get("diagnostic_status") == "collected" and trial.get("extension", {}).get("completed_transitions") == 40 and trial.get("extension", {}).get("observations") == 41,
                  name + " does not contain a collected full 40/41 extension")
            check((trial.get("handoff", {}).get("fresh") or {}).get("admitted") is True, name + " fresh handoff not admitted")
        check(a.get("experiment_id") != b.get("experiment_id"), "Branch experiment identities are not distinct")
        ea, eb = a.get("pair_evidence"), b.get("pair_evidence")
        if ea and eb:
            def identity(evidence):
                value = deepcopy(evidence["intent"])
                for key in ("experiment_id", "protocol_sha256", "sequence_sha256"): value.pop(key, None)
                value["protocol"].pop("branch", None)
                return value
            x, y = identity(ea), identity(eb)
            checks["differing_pinned_fields"] = [k for k in sorted(set(x) | set(y)) if canonical(x.get(k)) != canonical(y.get(k))]
            check(not checks["differing_pinned_fields"], "Pinned source/version/environment/reference conditions differ")
            checks["installed_modules_equal"] = ea["installed_module_hashes"] == eb["installed_module_hashes"]
            check(checks["installed_modules_equal"], "Installed modules differ between branches")
            checks["prefix_control_history_equal"] = canonical(ea["prefix_controls"]) == canonical(eb["prefix_controls"])
            checks["prefix_history_sha256"] = {"continue_pi": sha(canonical(ea["prefix_controls"])), "hold_throttle": sha(canonical(eb["prefix_controls"]))}
            checks["prefix_gear_history_equal"] = ea["prefix_gears"] == eb["prefix_gears"]
            check(checks["prefix_control_history_equal"] and checks["prefix_gear_history_equal"], "Exact replay control/gear history differs")
            if ea.get("entry") and eb.get("entry"):
                checks["direct_handoff"] = compare_entry(ea["intent"]["protocol"], ea["entry"], eb["entry"])
                check(checks["direct_handoff"]["admitted"], "Direct branch-to-branch handoff differs, even if both historical-anchor checks passed")
            else: reasons.append("Direct branch entry observation missing")
        else: reasons.append("Verified branch identity/handoff evidence missing")
        sa, sb = a.get("samples", []), b.get("samples", [])
        checks["relative_time_mismatch_indices"] = [i for i, (u, v) in enumerate(zip(sa, sb)) if u["index"] != v["index"] or abs(u["time_s"] - v["time_s"]) > common.TIME_TOLERANCE]
        check(len(sa) == len(sb) == 41 and not checks["relative_time_mismatch_indices"], "Full relative source-time/index alignment differs; no interpolation")
        if not reasons:
            for u, v in zip(sa, sb):
                aligned.append({"index": u["index"], "time_s": u["time_s"], "continue_pi_world_frame": u["world_frame"],
                    "hold_throttle_world_frame": v["world_frame"], "continue_pi_speed_mps": u["signed_speed_mps"], "hold_throttle_speed_mps": v["signed_speed_mps"],
                    "speed_difference_pi_minus_hold_mps": u["signed_speed_mps"] - v["signed_speed_mps"],
                    "continue_pi_interval_acceleration_mps2": u["interval_dv_dt_mps2"], "hold_throttle_interval_acceleration_mps2": v["interval_dv_dt_mps2"]})
    return {"status": "comparable" if not reasons else "not_comparable", "mismatches": reasons, "checks": checks, "aligned_samples": aligned,
            "interpretation": "Conditional descriptive branch response only. Matching observed entry/history is not hidden-state equality or proof of a physical cause."}


def verify_continuation_physics(report, intent):
    """Version2's settings RPC is an observed ACKed interval, never a gap."""
    from carla_throttle_isolation_protocol import SUBSTEP_VERSION
    p = intent['protocol']
    if p['schema_version'] != SUBSTEP_VERSION:
        require(not report.get('continuation_physics'), 'Legacy trial claims undeclared continuation physics')
        return
    evidence = report.get('continuation_physics')
    require(type(evidence) is dict and evidence.get('requested') == p['continuation_physics'],
            'Continuation physics intent/evidence differs')
    rows = report['measurement']['observations']
    applied = evidence.get('status') == 'applied_and_read_back'
    if report['status'] == 'collected' or len(rows) > 1:
        require(applied, 'Observed continuation lacks verified physics transition')
    if not applied:
        require(report['status'] == 'failed' and evidence.get('status') in ('not_attempted', 'mutation_intent', 'failed_or_uncertain'),
                'Unknown physics transition status')
        return
    before, target, after = (evidence.get(k) for k in ('before', 'target', 'after'))
    require(type(before) is dict and before.get('invalid_fields') == {} and type(before.get('values')) is dict,
            'Missing pre-change settings')
    values = before['values']
    require(values.get('synchronous_mode') is True and values.get('substepping') is True
            and values.get('fixed_delta_seconds') == DT and values.get('max_substep_delta_time') == .01
            and values.get('max_substeps') == 10, 'Invalid pre-change physics')
    expected = deepcopy(before)
    expected['values']['max_substep_delta_time'] = p['continuation_physics']['max_substep_delta_time_s']
    require(target == after == report.get('requested_settings') == expected,
            'Settings changed outside the declared substep or readback differs')
    require(len(rows) >= 1 and evidence.get('handoff_frame') == rows[0]['world_frame']
            and evidence.get('before_time_s') == rows[0]['source_time_s'], 'Physics handoff does not match F0')
    require(common.integer(evidence.get('after_frame')) and evidence['after_frame'] == evidence['handoff_frame'] + 1
            and common.finite(evidence.get('after_time_s'))
            and abs(evidence['after_time_s'] - evidence['before_time_s'] - DT) <= common.TIME_TOLERANCE,
            'Physics transition frame/time gap')
    require(common.integer(evidence.get('api_returned_frame')) and
            evidence['handoff_frame'] <= evidence['api_returned_frame'] <= evidence['after_frame'], 'Settings API return frame differs')
    if len(rows) > 1:
        require(evidence['after_frame'] == rows[1]['world_frame'] and evidence['after_time_s'] == rows[1]['source_time_s'],
                'Settings transition is not retained as interval zero')


def compare_substep_trials(trials):
    """Only an explicit same-source sham/treatment pair can be compared."""
    from carla_throttle_isolation_protocol import compare_entry, SUBSTEP_VERSION
    reasons, checks, aligned = [], {}, []
    def step(t): return t['continuation_physics']['requested'].get('max_substep_delta_time_s')
    ordered = sorted(trials, key=step, reverse=True)
    a, b = ordered
    if [step(a), step(b)] != [.01, .005]: reasons.append('Exactly one 0.01s sham and one 0.005s treatment required')
    for trial in ordered:
        if trial.get('integrity_status') != 'verified' or trial.get('diagnostic_status') != 'collected':
            reasons.append('Each trial must be verified and collected')
        if trial.get('extension', {}).get('completed_transitions') != 40 or len(trial.get('samples', [])) != 41:
            reasons.append('Full 40-command/41-observation denominator required')
        if (trial.get('handoff', {}).get('fresh') or {}).get('admitted') is not True:
            reasons.append('Fresh pre-treatment handoff not admitted')
    ea, eb = a.get('pair_evidence'), b.get('pair_evidence')
    if ea and eb:
        def pin(e):
            value = deepcopy(e['intent'])
            for key in ('experiment_id', 'protocol_sha256', 'sequence_sha256'): value.pop(key)
            value['protocol']['continuation_physics'].pop('max_substep_delta_time_s')
            return value
        checks['same_pins_except_declared_substep'] = canonical(pin(ea)) == canonical(pin(eb))
        checks['version2_pair'] = all(e['intent']['protocol']['schema_version'] == SUBSTEP_VERSION for e in (ea, eb))
        checks['same_installed_modules'] = ea['installed_module_hashes'] == eb['installed_module_hashes']
        checks['same_prefix_controls'] = ea['prefix_controls'] == eb['prefix_controls']
        checks['same_prefix_gears'] = ea['prefix_gears'] == eb['prefix_gears']
        checks['distinct_experiments'] = a['experiment_id'] != b['experiment_id']
        if not all(checks.values()): reasons.append('Pinned environment/source/input identities differ')
        if ea.get('entry') and eb.get('entry'):
            checks['direct_handoff'] = compare_entry(ea['intent']['protocol'], ea['entry'], eb['entry'])
            if not checks['direct_handoff']['admitted']: reasons.append('Direct sham/treatment handoff differs')
        else: reasons.append('Missing direct handoff evidence')
    else: reasons.append('Missing verified pair identities')
    sa, sb = a.get('samples', []), b.get('samples', [])
    checks['relative_times_equal'] = len(sa) == len(sb) == 41 and all(
        u['index'] == v['index'] and abs(u['time_s'] - v['time_s']) <= common.TIME_TOLERANCE for u, v in zip(sa, sb))
    if not checks['relative_times_equal']: reasons.append('Relative time/index alignment differs')
    if not reasons:
        for u, v in zip(sa, sb):
            aligned.append({'index': u['index'], 'time_s': u['time_s'],
                'sham_world_frame': u['world_frame'], 'treatment_world_frame': v['world_frame'],
                'sham_speed_mps': u['signed_speed_mps'], 'treatment_speed_mps': v['signed_speed_mps'],
                'sham_acceleration_mps2': u['interval_dv_dt_mps2'], 'treatment_acceleration_mps2': v['interval_dv_dt_mps2']})
    return {'status': 'comparable' if not reasons else 'not_comparable', 'kind': 'substep_sham_treatment',
        'mismatches': reasons, 'checks': checks, 'aligned_samples': aligned,
        'interpretation': 'Both branches retain the settings API first interval. Conditional response to one substep change, not a calibrated model or proof of a unique physical cause.'}


if __name__ == "__main__":
    raise SystemExit(main())
