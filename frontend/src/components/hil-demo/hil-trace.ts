import { record, sameValue } from "../object-scene/object-scene.ts";
import { retainedFrameIndex } from "../city-sim/city-playback.ts";
import type { HilExecution, HilSide } from "./demo-contract";
import { hilProfileComparison, parseHilDutProfile } from "./dut-profile.ts";
import type { HilComparisonKind, HilDutProfile, HilDutProfiles } from "./dut-profile.ts";
import { hilSceneContext, parseHilSceneProfile } from "./scene-profile.ts";
import type { HilSceneProfile } from "./scene-profile.ts";

export type HilFault = { kind: "delay" | "drop" | "repeat"; start_s: number; duration_s: number; delay_ms: number };
export type HilFrame = { time_s: number; input_time_s?: number; input_age_ms: number | null; dut_state: string; ego_speed_mps: number; lead_gap_m: number | null; command: { brake: number; throttle: number; steer: number }; ipc_wall_latency_ms: number; sequence: number; object_sequence: number | null; response_frame: number; lead_actor_id?: string; lead_speed_mps?: number; lead_target_speed_mps?: number; lead_phase?: string };
export type HilEvent = { time_s: number; type: string; label: string; input_time_s?: number; provenance?: string };
export type HilTrace = { side: HilSide; fault: HilFault | null; frames: HilFrame[]; events: HilEvent[]; comparisonKind: HilComparisonKind; profile?: HilDutProfile; sceneProfile?: HilSceneProfile; policy: { maxObjectAgeMs: number; recoveryFreshSteps: number } };
export type HilEvidence = { traces: Record<HilSide, HilTrace>; fault: HilFault; source: Record<string, unknown>; comparisonKind: HilComparisonKind; profiles?: HilDutProfiles; sceneProfile?: HilSceneProfile };
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const optionalNumber = (value: unknown): value is number | null => value === null || finite(value);
export function parseHilTrace(raw: unknown, side: HilSide): HilTrace {
  const trace = record(raw, "HIL trace");
  if (trace.schema_version !== "hil-demo-trace/1" || trace.side !== side || trace.execution_target !== "software_dut" || trace.fault_time_basis !== "simulation_time" || trace.ipc_time_basis !== "parent_monotonic_wall_clock") throw new Error("보존된 Software DUT trace 계약과 시간 기준이 일치하지 않습니다.");
  if (!Array.isArray(trace.frames) || trace.frames.length < 2 || trace.frames.length > 10000) throw new Error("재생할 DUT 입력·응답 표본이 없거나 지원 범위를 넘었습니다.");
  const comparisonKind = trace.comparison_kind ?? "same_scene_fault_ab";
  if (comparisonKind !== "same_scene_fault_ab" && comparisonKind !== "same_fault_dut_ab") throw new Error("지원하지 않는 HIL trace 비교 모드입니다.");
  const profile = comparisonKind === "same_fault_dut_ab" ? parseHilDutProfile(trace.dut_profile) : undefined;
  const sceneProfile = trace.scene_profile === undefined ? undefined : parseHilSceneProfile(trace.scene_profile);
  if (!profile && trace.dut_profile !== undefined) throw new Error("정상/고장 trace에 예상하지 않은 DUT 프로필이 있습니다.");
  const expectedAgeMs = profile?.parameters.max_object_age_ms ?? 250, expectedRecovery = profile?.parameters.recovery_fresh_steps ?? 3;
  if (trace.max_object_age_s !== expectedAgeMs / 1000 || trace.recovery_fresh_steps !== expectedRecovery) throw new Error("보존된 DUT 정책이 실행 프로필과 다릅니다.");
  const frames = trace.frames.map(value => {
    const frame = record(value, "HIL frame"), command = record(frame.command, "DUT command");
    if (!finite(frame.time_s) || frame.time_s < 0 || !optionalNumber(frame.input_age_ms) || frame.input_age_ms !== null && frame.input_age_ms < 0 || !finite(frame.ego_speed_mps) || !optionalNumber(frame.lead_gap_m) || typeof frame.dut_state !== "string" || !finite(frame.ipc_wall_latency_ms) || !finite(frame.sequence) || !finite(command.brake) || !finite(command.throttle) || !finite(command.steer)) throw new Error("DUT 표본의 시간·상태·제어값이 유효하지 않습니다.");
    if (command.brake < 0 || command.brake > 1 || command.throttle < 0 || command.throttle > 1 || command.steer < -1 || command.steer > 1) throw new Error("DUT 명령이 지원 범위를 벗어났습니다.");
    if (frame.input_time_s !== undefined && (!finite(frame.input_time_s) || frame.input_time_s < 0 || frame.input_time_s >= frame.time_s)) throw new Error("DUT 입력과 응답 시간의 순서가 유효하지 않습니다.");
    if (sceneProfile && (typeof frame.lead_actor_id !== "string" || !frame.lead_actor_id || !finite(frame.lead_speed_mps) || frame.lead_speed_mps < 0 || !finite(frame.lead_target_speed_mps) || frame.lead_target_speed_mps < 0 || !["cruise", "deceleration", "stop", "restart"].includes(String(frame.lead_phase)))) throw new Error("새 장면의 앞차 응답 속도·실행 목표·단계가 보존되지 않았습니다.");
    if (!sceneProfile && ["lead_actor_id", "lead_speed_mps", "lead_target_speed_mps", "lead_phase"].some((key) => frame[key] !== undefined)) throw new Error("기존 장면 trace에 출처 없는 앞차 측정값이 포함되어 있습니다.");
    return frame as unknown as HilFrame;
  });
  if (frames.some((frame, index) => index > 0 && (frame.time_s <= frames[index - 1].time_s || frame.sequence <= frames[index - 1].sequence))) throw new Error("DUT 원본 시간 또는 sequence가 순서대로 보존되지 않았습니다.");
  const expectsFault = side === "faulted" || comparisonKind === "same_fault_dut_ab";
  const fault = expectsFault ? record(trace.fault, "injected fault") : null;
  if (!expectsFault && trace.fault !== null || fault && (!["delay", "drop", "repeat"].includes(String(fault.kind)) || !finite(fault.start_s) || !finite(fault.duration_s) || !finite(fault.delay_ms) || fault.start_s < 0 || fault.duration_s <= 0 || fault.delay_ms < 0)) throw new Error("저장된 A/B 고장 조건이 유효하지 않습니다.");
  if (!Array.isArray(trace.events)) throw new Error("DUT 이벤트 목록이 보존되지 않았습니다.");
  const events = trace.events.map(value => {
    const event = record(value, "HIL event");
    if (!finite(event.time_s) || event.time_s < 0 || event.time_s > frames.at(-1)!.time_s + .1 || typeof event.type !== "string" || typeof event.label !== "string") throw new Error("보존된 DUT 이벤트가 유효하지 않습니다.");
    if (event.type.startsWith("lead_") && (!sceneProfile || !finite(event.input_time_s) || event.input_time_s < 0 || event.input_time_s >= event.time_s || event.provenance !== (event.type.endsWith("_commanded") ? "acknowledged_lead_control" : "native_response_speed"))) throw new Error("앞차 명령·실제 응답 이벤트의 시간 기준과 출처가 다릅니다.");
    return event as unknown as HilEvent;
  });
  return { side, fault: fault as HilFault | null, frames, events, comparisonKind, ...(profile ? { profile } : {}), ...(sceneProfile ? { sceneProfile } : {}), policy: { maxObjectAgeMs: Number(trace.max_object_age_s) * 1000, recoveryFreshSteps: Number(trace.recovery_fresh_steps) } };
}
export function hilSealedSummary(raw: unknown, execution: HilExecution) {
  const receipt = record(raw, "sealed receipt"), stored = record(receipt.execution, "execution"), prepared = record(receipt.prepared, "prepared"), summary = record(stored.summary, "HIL summary");
  if (receipt.schema_version !== 1 || execution.status !== "completed" || execution.adapter_id !== "carla-software-dut" || execution.experiment_kind !== "hil_demo" || execution.fidelity !== "actual_carla_software_dut" || summary.schema_version !== "hil-demo-result/1" || summary.execution_target !== "software_dut") throw new Error("완료된 Software DUT · CARLA 실행 원본이 아닙니다.");
  for (const key of ["id", "experiment_id", "project_id", "status", "experiment_kind", "adapter_id", "adapter_version", "input_hash", "fidelity"] as const) if (stored[key] !== execution[key]) throw new Error(`봉인된 실행 ${key}가 선택한 결과와 다릅니다.`);
  if (!sameValue(stored.artifacts, execution.artifacts)) throw new Error("봉인된 산출물 목록이 실행과 다릅니다.");
  const source = record(prepared.input, "prepared input"), context = hilProfileComparison(summary, source, execution.project_id, prepared.spec ? record(prepared.spec, "HIL spec") : undefined);
  const sceneProfile = hilSceneContext(summary, source, prepared.spec ? record(prepared.spec, "HIL spec") : undefined);
  return { summary, source, ...context, sceneProfile };
}
export function hilVerifyTracePair(traces: Record<HilSide, HilTrace>, fault: unknown, comparisonKind: HilComparisonKind, profiles?: HilDutProfiles, sceneProfile?: HilSceneProfile) {
  for (const side of ["baseline", "faulted"] as const) {
    const trace = traces[side];
    if (trace.comparisonKind !== comparisonKind || !sameValue(trace.fault, side === "faulted" || comparisonKind === "same_fault_dut_ab" ? fault : null) || !sameValue(trace.profile, profiles?.[side]) || !sameValue(trace.sceneProfile, sceneProfile)) throw new Error("보존된 A/B trace의 고장·정책·장면·비교 모드가 실행 입력과 다릅니다.");
  }
}
export function hilSideLabel(data: HilEvidence, side: HilSide) { const profile = data.traces[side].profile; return profile ? `${profile.name} · r${profile.revision}` : side === "baseline" ? "정상 입력" : hilFaultLabel(data.fault); }
export function hilSampleAt(trace: HilTrace, timeS: number): HilFrame | null {
  const index = retainedFrameIndex(trace.frames, timeS);
  return index < 0 ? null : trace.frames[index];
}
export function hilObservedFaultWindow(trace: HilTrace) {
  const start = trace.events.find(event => event.type === "fault_started")?.time_s;
  const end = trace.events.find(event => event.type === "fault_ended" && start !== undefined && event.time_s > start)?.time_s;
  return start !== undefined && end !== undefined ? { start, end } : null;
}
export function hilPresetMatches(fault: HilFault, presetId: string) { return fault.kind === (presetId === "input_delay" ? "delay" : presetId === "input_dropout" ? "drop" : presetId === "input_repeat" ? "repeat" : null); }
export function hilFaultLabel(fault: HilFault) { return fault.kind === "delay" ? `${fault.delay_ms} ms 입력 지연` : fault.kind === "repeat" ? `${fault.duration_s} s 패킷 반복` : `${fault.duration_s} s 입력 단절`; }
export function hilFaultIntervalLabel(fault: HilFault) { return `${fault.start_s.toFixed(2)}–${(fault.start_s + fault.duration_s).toFixed(2)}`; }
export const hilDutLabel = (state: string) => ({ driving: "주행 판단", normal: "주행 판단", tracking: "경로 추종", degraded: "입력 오래됨 · 정지", stale_stop: "오래된 입력 · 정지", stale_input_stop: "오래된 입력 · 정지", stale_input: "입력 오래됨", recovering: "정상 입력 확인", hold: "정지 유지", stopped: "정지" } as Record<string, string>)[state] ?? state;
export const hilEventLabel = (event: HilEvent) => ({ lead_deceleration_commanded: "앞차 감속 목표 적용", lead_stop_commanded: "앞차 정지 목표 적용", lead_restart_commanded: "앞차 재출발 목표 적용", lead_slowed: "앞차 감속 관측 (≤0.75 m/s)", lead_stopped: "앞차 정지 관측 (≤0.2 m/s)", lead_moving_again: "앞차 재출발 관측 (>0.5 m/s)", fault_started: "고장 주입 시작", fault_ended: "고장 주입 종료", dut_tracking: "DUT 경로 추종", dut_degraded: "DUT 입력 오래됨 · 정지", dut_recovering: "DUT 정상 입력 확인" } as Record<string, string>)[event.type] ?? event.label;
