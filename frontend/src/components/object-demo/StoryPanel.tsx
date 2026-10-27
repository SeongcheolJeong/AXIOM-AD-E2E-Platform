import { Play, RotateCcw } from "lucide-react";
import type { ObjectStory, StoryController } from "./story-model";
import { storyDecisionSummary } from "./story-model";

export default function StoryPanel({
  story,
  problem,
  loading,
  canStart,
  state,
  onStart,
  onResume,
  onSegment,
}: {
  story: ObjectStory | null;
  problem: string | null;
  loading: boolean;
  canStart: boolean;
  state: StoryController;
  onStart: () => void;
  onResume: () => void;
  onSegment: (index: number) => void;
}) {
  const segment = story?.segments[state.segmentIndex];
  return (
    <section
      className={`object-story-panel ${state.mode === "guided" ? "is-guided" : ""}`}
      aria-label="관측 사건 시연"
      data-story-mode={state.mode}
      data-story-phase={state.phase}
      data-story-segment={state.segmentIndex}
    >
      <div className="object-story-heading">
        <div>
          <span>GUIDED STORY · NATIVE GT</span>
          <h3>
            {state.mode === "guided" && segment
              ? segment.title
              : "관측된 행동을 따라가는 시연"}
          </h3>
        </div>
        <button
          className="button"
          disabled={
            loading ||
            !!problem ||
            !story ||
            !canStart ||
            state.mode === "guided"
          }
          onClick={onStart}
        >
          <Play size={15} />
          {state.mode === "completed" ? "시연 다시 시작" : "시연 시작"}
        </button>
        {state.mode === "manual" && !problem && (
          <button className="button secondary" onClick={onResume}>
            <RotateCcw size={14} />이 구간 시연 재개
          </button>
        )}
      </div>
      {loading ? (
        <p role="status">봉인된 사건에서 시연 구간을 구성하는 중…</p>
      ) : problem ? (
        <p className="object-story-unavailable" role="status">
          {problem} 공통 시간 재생과 사건 탐색은 계속 사용할 수 있습니다.
        </p>
      ) : (
        story && (
          <>
            <div className="object-story-status">
              <strong>
                {state.mode === "guided"
                  ? state.phase === "hold"
                    ? `판단 설명 · ${state.holdRemainingS.toFixed(1)}초`
                    : `자동 시연 ${state.segmentIndex + 1} / ${story.segments.length}`
                  : state.mode === "completed"
                    ? "시연 완료"
                    : state.mode === "manual"
                      ? "수동 조사 · 자동 전환 중지"
                      : `${story.segments.length}개 구간 · ${story.total_presentation_s.toFixed(0)}초 재생`}
              </strong>
              <span>원본 로딩 시간 제외 · 사건 시각에서 2초 정지</span>
            </div>
            {segment && (
              <p className="object-story-description">{segment.description}</p>
            )}
            <div className="object-story-segments" aria-label="시연 구간">
              {story.segments.map((item, index) => (
                <button
                  key={item.id}
                  aria-pressed={state.segmentIndex === index}
                  onClick={() => onSegment(index)}
                >
                  <b>{index + 1}</b>
                  {item.title}
                  <small>{item.anchor_time_s.toFixed(2)}s</small>
                </button>
              ))}
            </div>
            <details className="object-story-provenance">
              <summary>시연 구성 근거</summary>
              <p>
                이 설명은 보존된 planner 상태·사건을 사용합니다. 설정 차이가
                행동 차이의 원인이라고 자동 판정하지 않습니다.
              </p>
              <p>
                규칙 {story.rule_version} · {story.clock}
              </p>
              <ul>
                {story.limitations.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
              {segment && (
                <ul>
                  {segment.events.map((event, index) => (
                    <li key={index}>
                      {event.side === "left" ? "A" : "B"} · {event.actor_id} ·{" "}
                      명령 {event.time_s.toFixed(2)}s · 관측{" "}
                      {event.observation_time_s.toFixed(2)}s · {event.reason} ·
                      frame {event.input_frame}
                      {storyDecisionSummary(event) && <p>{storyDecisionSummary(event)}</p>}
                    </li>
                  ))}
                </ul>
              )}
            </details>
          </>
        )
      )}
    </section>
  );
}
