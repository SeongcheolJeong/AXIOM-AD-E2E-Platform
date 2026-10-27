import { verifiedArtifactBytes } from "../../pages/CarlaCameraFrame";
import { verifiedReceipt } from "../object-scene/load-object-scene";
import { record, sameValue } from "../object-scene/object-scene";
import type { HilExecution, HilSide } from "./demo-contract";
import { hilPresetMatches, hilSealedSummary, hilVerifyTracePair, parseHilTrace } from "./hil-trace";
import { verifyHilProfileHash } from "./dut-profile";
import { verifyHilSceneHash } from "./scene-profile";
import type { HilEvidence } from "./hil-trace";

export async function loadHilTrace(execution: HilExecution, presetId: string, signal: AbortSignal): Promise<HilEvidence> {
  const receipt = await verifiedReceipt(execution, signal, 32 * 1024 * 1024);
  const { summary, source, comparisonKind, profiles, sceneProfile } = hilSealedSummary(receipt, execution), summaries = record(summary.sides, "HIL sides");
  if (sceneProfile) await verifyHilSceneHash(sceneProfile);
  if (profiles) await Promise.all(Object.values(profiles).map(verifyHilProfileHash));
  const read = async (side: HilSide) => {
    const sideSummary = record(summaries[side], "side summary"), name = `${side}-hil-trace.json`;
    const artifacts = execution.artifacts.filter(item => item.name === name);
    if (artifacts.length !== 1 || artifacts[0].media_type !== "application/json" || sideSummary.hil_trace_artifact !== name || sideSummary.hil_trace_sha256 !== artifacts[0].sha256) throw new Error("봉인된 HIL trace가 누락되었거나 원본 hash와 다릅니다.");
    const bytes = await verifiedArtifactBytes(execution.id, artifacts[0], 32 * 1024 * 1024, signal);
    return parseHilTrace(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)), side);
  };
  const [baseline, faulted] = await Promise.all([read("baseline"), read("faulted")]);
  if (!faulted.fault || !sameValue(faulted.fault, summary.fault) || !hilPresetMatches(faulted.fault, presetId)) throw new Error("선택한 프리셋과 저장 결과의 실제 고장 항목이 다릅니다. 해당 프리셋으로 결과를 다시 여세요.");
  const traces = { baseline, faulted };
  hilVerifyTracePair(traces, summary.fault, comparisonKind, profiles, sceneProfile);
  return { traces, fault: faulted.fault, source, comparisonKind, profiles, sceneProfile };
}
