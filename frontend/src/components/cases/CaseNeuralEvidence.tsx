import type { CaseDetail } from "./CaseTypes";
import { neuralCaseReviewHref } from "../neural-demo/neural-perception";
export default function CaseNeuralEvidence({ value }: { value: CaseDetail }) {
  const link = value.neural_sensitivity;
  if (!link) return null;
  const variant = { original: "원본", baseline: "같은 시점 재구성", novel: "변경 시점" }[link.selected_variant];
  return <section className="panel case-section" aria-label="Neural 모델 비교 근거">
    <h2>Neural 모델 출력 비교</h2><p>원본 frame {link.source_frame_index} · {link.source_time_s.toFixed(3)} s · 선택 영상: {variant}</p>
    <p>검출 개수: 원본 <strong>{link.summary.original_count}</strong> → 재구성 <strong>{link.summary.baseline_count}</strong> → 새 시점 <strong>{link.summary.novel_count}</strong></p>
    <p>모델 출력 변화에 대한 조사 근거입니다. 정확도나 인식 실패로 판정하지 않았습니다.</p>
    <a className="button secondary" href={neuralCaseReviewHref(link)}>Neural Sim에서 같은 모델 비교 열기 →</a>
    <details><summary>고정된 모델 · 렌더 실행</summary><dl><dt>모델</dt><dd>{link.model_id} · r{link.model_revision}</dd><dt>모델 출력 실행</dt><dd>{link.sensitivity_pin.execution_id}</dd><dt>렌더 실행</dt><dd>{link.render_pin.execution_id}</dd><dt>렌더 시점</dt><dd>{link.frame_index}</dd></dl></details>
  </section>;
}
