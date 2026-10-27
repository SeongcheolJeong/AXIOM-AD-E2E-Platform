import { useEffect, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Pause,
  Play,
  RotateCcw,
} from "lucide-react";
import { secondsFromNanoseconds } from "../recordings/RecordingTime";
import type { StackReplayDetail } from "./StackReplayTypes";
import ConsumedPoints from "./ConsumedPoints";
import StackTickStages from "./StackTickStages";
import StackTraceSignals from "./StackTraceSignals";
import StackV2Signals from "./StackV2Signals";

export default function StackTraceExplorer({
  job,
}: {
  job: StackReplayDetail;
}) {
  const [index, setIndex] = useState(0),
    [playing, setPlaying] = useState(false),
    [mobile, setMobile] = useState<"points" | "stages">("points");
  const tick = job.ticks.find((item) => item.index === index) ?? null;
  useEffect(() => {
    if (!playing) return;
    const position = job.ticks.findIndex((item) => item.index === index);
    const next = job.ticks[position + 1];
    if (!next) {
      setPlaying(false);
      return;
    }
    const timer = setTimeout(() => setIndex(next.index), 250);
    return () => clearTimeout(timer);
  }, [playing, index, job.ticks]);
  function choose(next: number) {
    setPlaying(false);
    setIndex(next);
  }
  const origin = job.inputs[0]?.at_ns;
  const compatible =
    tick?.packet?.lidar &&
    tick.points &&
    tick.points.sha256 === tick.packet.lidar.points_sha256 &&
    tick.points.point_count === tick.packet.lidar.point_count &&
    tick.points.frame_id === tick.packet.lidar.frame_id &&
    tick.packet.lidar.coordinate_convention === "x_forward_y_left_z_up" &&
    (!tick.output || tick.output.input_points_sha256 === tick.points.sha256);
  const hasPoints = tick?.points !== null && tick?.points !== undefined;
  return (
    <div className="stack-trace-explorer" data-testid="stack-trace-explorer">
      <section className="panel stack-trace-panel">
        <div className="stack-section-heading">
          <div>
            <h3>Observation → policy → control</h3>
            <p>
              Exact tick evidence in the declared ego frame. Surrounding actor
              ground truth is not an input.
            </p>
          </div>
          <span>{job.ticks.length} selected ticks</span>
        </div>
        <div className="stack-trace-controls">
          <button
            type="button"
            className="button secondary small"
            aria-label={playing ? "Pause stack trace" : "Play stack trace"}
            disabled={!job.ticks.length}
            onClick={() => setPlaying((value) => !value)}
          >
            {playing ? <Pause size={14} /> : <Play size={14} />}
          </button>
          <button
            type="button"
            className="button secondary small"
            aria-label="Reset stack trace"
            onClick={() => choose(job.ticks[0]?.index ?? 0)}
          >
            <RotateCcw size={14} />
          </button>
          <button
            type="button"
            className="button secondary small"
            aria-label="Previous stack tick"
            disabled={index <= 0}
            onClick={() => choose(index - 1)}
          >
            <ChevronLeft size={14} />
          </button>
          <label>
            Selected tick
            <select
              aria-label="Select stack tick"
              value={index}
              onChange={(event) => choose(Number(event.target.value))}
            >
              {job.ticks.map((item) => (
                <option key={item.index} value={item.index}>
                  {item.index + 1} · ego #{item.input.ego.ordinal} ·{" "}
                  {item.status} ·{" "}
                  {item.output?.decision.mode.replaceAll("_", " ") ??
                    "no output"}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="button secondary small"
            aria-label="Next stack tick"
            disabled={index >= job.ticks.length - 1}
            onClick={() => choose(index + 1)}
          >
            <ChevronRight size={14} />
          </button>
        </div>
        <input
          className="stack-tick-range"
          type="range"
          aria-label="Stack trace tick cursor"
          min={0}
          max={Math.max(0, job.ticks.length - 1)}
          step={1}
          value={index}
          onChange={(event) => choose(Number(event.target.value))}
        />
        <div className="stack-tick-strip" aria-label="Recorded tick decisions">
          {job.ticks.map((item) => (
            <button
              type="button"
              key={item.index}
              className={`stack-tick-dot ${item.output?.decision.mode ?? item.status}`}
              aria-label={`Inspect stack tick ${item.index + 1}, ego ordinal ${item.input.ego.ordinal}`}
              aria-pressed={index === item.index}
              title={`${item.index + 1}: ${item.output?.decision.mode ?? item.status}`}
              onClick={() => choose(item.index)}
            >
              {item.index + 1}
            </button>
          ))}
        </div>
        <div className="stack-trace-clock">
          <code data-testid="stack-tick-time">
            {tick?.input.at_ns ?? "No selected tick"} ns
          </code>
          <span>
            {tick && origin
              ? `+${secondsFromNanoseconds(BigInt(tick.input.at_ns) - BigInt(origin))} s from first selected tick`
              : ""}
          </span>
          <small>
            Playback advances 4 ticks/s for inspection; it does not run a
            simulation.
          </small>
        </div>
        {!tick ? (
          <p className="stack-stage-empty">
            No evidence for the selected tick.
          </p>
        ) : (
          <>
            <div className="stack-trace-source-row">
              <span>
                Tick state:{" "}
                <b data-testid="stack-tick-status">
                  {tick.status.replaceAll("_", " ")}
                </b>
              </span>
              <span>
                Ego channel #{tick.input.ego.channel_id} · ordinal #
                {tick.input.ego.ordinal}
              </span>
              <span>
                LiDAR{" "}
                {tick.input.lidar
                  ? `channel #${tick.input.lidar.channel_id} · ordinal #${tick.input.lidar.ordinal}`
                  : "no prior sample"}
              </span>
              <span data-testid="stack-lidar-age">
                LiDAR age:{" "}
                {tick.input.lidar_age_ns === null
                  ? "unknown"
                  : `${tick.input.lidar_age_ns} ns`}
              </span>
            </div>
            {tick.error && (
              <p className="inline-error" role="alert">
                {tick.error}
              </p>
            )}
            {tick.packet?.unavailable_reasons.length ? (
              <div
                className="stack-notice"
                data-testid="stack-input-unavailable"
              >
                <strong>Unavailable input evidence</strong>
                {tick.packet.unavailable_reasons.map((reason) => (
                  <p key={reason}>{reason.replaceAll("_", " ")}</p>
                ))}
              </div>
            ) : null}
            <div
              className="stack-mobile-tabs"
              role="tablist"
              aria-label="Stack tick inspection views"
            >
              <button
                role="tab"
                aria-selected={mobile === "points"}
                onClick={() => setMobile("points")}
              >
                Consumed points
              </button>
              <button
                role="tab"
                aria-selected={mobile === "stages"}
                onClick={() => setMobile("stages")}
              >
                Decision trace
              </button>
            </div>
            <div className={`stack-tick-workspace mobile-${mobile}`}>
              <div className="stack-tick-observation">
                <div className="stack-preview-heading">
                  <strong>Actual consumed point buffer</strong>
                  <span>x forward · y left · z up · metres</span>
                </div>
                {compatible && tick.points ? (
                  <ConsumedPoints
                    key={`${tick.index}/${tick.points.sha256}`}
                    points={tick.points}
                  />
                ) : (
                  <div
                    className="stack-preview-empty"
                    role={hasPoints ? "alert" : "status"}
                  >
                    {hasPoints
                      ? "Point buffer identity does not match the published stack packet. Rendering is withheld."
                      : tick.status === "not_attempted"
                        ? "This tick has not been attempted. No input buffer was published."
                        : tick.status === "running"
                          ? "This tick is running. A consumed buffer has not been published."
                          : "No usable consumed LiDAR buffer was published. This is not an empty or obstacle-free observation."}
                  </div>
                )}
                {tick.packet?.ego && (
                  <div className="stack-ego-evidence">
                    <span>
                      Recorded ego telemetry · source{" "}
                      <b>{tick.packet.ego.source}</b>
                    </span>
                    <dl>
                      <div>
                        <dt>Speed</dt>
                        <dd>{tick.packet.ego.speed_mps} m/s</dd>
                      </div>
                      <div>
                        <dt>Route heading error (+left)</dt>
                        <dd>{tick.packet.ego.route_heading_error_rad} rad</dd>
                      </div>
                      <div>
                        <dt>Pitch / roll</dt>
                        <dd>
                          {tick.packet.ego.pitch_rad} /{" "}
                          {tick.packet.ego.roll_rad} rad
                        </dd>
                      </div>
                    </dl>
                    <code>Aligned log {tick.packet.ego.timestamp_ns} ns</code>
                    <p>
                      Original capture clock:{" "}
                      {tick.packet.ego.capture_time_ns ?? "unknown"} ns. Route
                      heading is supplied telemetry/oracle input; this stack
                      does not perceive lanes.
                    </p>
                  </div>
                )}
              </div>
              <StackTickStages tick={tick} />
            </div>
            <details className="stack-disclosure">
              <summary>
                Tick source identities, transforms & raw stage output
              </summary>
              <pre>
                {JSON.stringify(
                  {
                    input: tick.input,
                    packet: tick.packet,
                    output: tick.output,
                    output_hash: tick.output_hash,
                    control_application: tick.control_application,
                  },
                  null,
                  2,
                )}
              </pre>
            </details>
            {tick.output?.warnings.length ? (
              <div className="stack-trace-warnings">
                {tick.output.warnings.map((warning) => (
                  <p key={warning}>{warning}</p>
                ))}
              </div>
            ) : null}
          </>
        )}
      </section>
      <StackTraceSignals ticks={job.ticks} selected={index} onSelect={choose} />
      <StackV2Signals
        samples={job.ticks.map((item) => ({
          index: item.index,
          output: item.output,
        }))}
        selected={index}
        onSelect={choose}
      />
    </div>
  );
}
