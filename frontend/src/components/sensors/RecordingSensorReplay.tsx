import { useEffect, useState } from "react";
import {
  ArrowDownToLine,
  Box,
  ChevronDown,
  FileSearch,
  Link2,
  LoaderCircle,
  Pause,
  Play,
  Plus,
  RefreshCw,
  SkipForward,
} from "lucide-react";
import { formatDate } from "../../api";
import { DownloadLink } from "../ArtifactAccess";
import { CopyButton } from "../ui";
import { MAX_TIMESTAMP_NS } from "../recordings/RecordingTime";
import type { RecordingDetail } from "../recordings/RecordingTypes";
import { useSensorResource } from "./SensorData";
import SensorBindingEditor from "./SensorBindingEditor";
import SensorFrameInspector from "./SensorFrameInspector";
import SensorReplayPanel from "./SensorReplayPanel";
import SensorRigEditor from "./SensorRigEditor";
import type {
  BindingDetail,
  RigDetail,
  RigSensor,
  SensorBundle,
  SensorChannels,
} from "./SensorTypes";
import "../../sensors.css";
import type { InferenceSource } from "../perception/PerceptionTypes";

export default function RecordingSensorReplay({
  recording,
  cursor,
  onCursor,
  onOpenRaw,
  onInfer,
}: {
  recording: RecordingDetail;
  cursor: string;
  onCursor: (cursor: string) => void;
  onOpenRaw: (channel: number, cursor: string) => void;
  onInfer?: (source: InferenceSource) => void;
}) {
  const root = `/recordings/${encodeURIComponent(recording.id)}`;
  const support = useSensorResource<SensorChannels>(`${root}/sensor-channels`);
  const rigs = useSensorResource<RigDetail[]>("/sensor-rigs");
  const bindings = useSensorResource<BindingDetail[]>(
    `${root}/sensor-bindings/revisions`,
  );
  const [cameraSelection, setCameraSelection] = useState<string | null>(null);
  const [lidarSelection, setLidarSelection] = useState<string | null>(null);
  const [bindingSelection, setBindingSelection] = useState(0);
  const [points, setPoints] = useState(20000);
  const [mobileView, setMobileView] = useState("camera");
  const [playing, setPlaying] = useState(false);
  const [rigEditor, setRigEditor] = useState<RigDetail | "new" | null>(null);
  const [bindingOpen, setBindingOpen] = useState(false);
  const [newRigId, setNewRigId] = useState<string | undefined>();
  const [notice, setNotice] = useState("");
  const [inspect, setInspect] = useState<{
    channelId: number;
    ordinal: number | null;
  } | null>(null);
  const [rigId, setRigId] = useState<string | null>(null);
  const [rigRevision, setRigRevision] = useState<number | null>(null);
  const channels =
    support.data?.recording_id === recording.id ? support.data.channels : [];
  const bindingRevision = bindingSelection;
  const binding =
    bindings.data?.find((entry) => entry.revision === bindingRevision) ?? null;
  const replayChannels = binding
    ? channels.filter((channel) =>
        binding.channels.some(
          (entry) => entry.channel_id === channel.channel_id,
        ),
      )
    : channels;
  const cameraId =
    cameraSelection ??
    String(
      replayChannels.find(
        (channel) =>
          channel.kind === "camera" && channel.status === "supported",
      )?.channel_id ?? "",
    );
  const lidarId =
    lidarSelection ??
    String(
      replayChannels.find(
        (channel) => channel.kind === "lidar" && channel.status === "supported",
      )?.channel_id ?? "",
    );
  const channelIds = [cameraId, lidarId].filter(Boolean).map(Number);
  const query = new URLSearchParams({
    at_ns: cursor,
    max_points: String(points),
  });
  channelIds.forEach((channelId) =>
    query.append("channel_id", String(channelId)),
  );
  if (bindingRevision) query.set("binding_revision", String(bindingRevision));
  const bundleResource = useSensorResource<SensorBundle>(
    channelIds.length && !bindings.loading
      ? `${root}/sensor-bundle?${query}`
      : null,
    120,
  );
  const bundle =
    bundleResource.data?.recording_id === recording.id &&
    bundleResource.data.at_ns === cursor &&
    bundleResource.data.binding_revision === (bindingRevision || null)
      ? bundleResource.data
      : null;
  const nextTimes = (bundle?.entries ?? [])
    .map((entry) => entry.next_aligned_time_ns)
    .filter((time): time is string => time !== null)
    .map(BigInt)
    .filter(
      (time) => time > BigInt(cursor) && time >= 0n && time <= MAX_TIMESTAMP_NS,
    );
  const nextTime = nextTimes.length
    ? nextTimes
        .reduce((earliest, time) => (time < earliest ? time : earliest))
        .toString()
    : null;
  useEffect(() => {
    if (!playing || bundleResource.loading) return;
    if (!nextTime || bundleResource.error) {
      setPlaying(false);
      return;
    }
    const timer = setTimeout(() => onCursor(nextTime), 250);
    return () => clearTimeout(timer);
  }, [
    playing,
    nextTime,
    bundleResource.loading,
    bundleResource.error,
    onCursor,
  ]);
  const chosenRigId =
    rigId ?? binding?.rig_id ?? newRigId ?? rigs.data?.[0]?.id ?? "";
  const rigRevisions = useSensorResource<RigDetail[]>(
    chosenRigId
      ? `/sensor-rigs/${encodeURIComponent(chosenRigId)}/revisions`
      : null,
  );
  const rigHead = rigs.data?.find((entry) => entry.id === chosenRigId);
  const chosenRigRevision =
    rigRevision ??
    (binding?.rig_id === chosenRigId
      ? binding.rig_revision
      : (rigHead?.revision ?? 1));
  const chosenRig =
    rigRevisions.data?.find((entry) => entry.revision === chosenRigRevision) ??
    (rigHead?.revision === chosenRigRevision ? rigHead : null);
  const initialSensors: RigSensor[] = channels
    .filter(
      (channel) =>
        channelIds.includes(channel.channel_id) && channel.kind !== null,
    )
    .map((channel) => ({
      key: `${channel.kind}-${channel.channel_id}`,
      name: channel.topic || `Channel ${channel.channel_id}`,
      kind: channel.kind!,
      frame_id:
        bundle?.entries.find((entry) => entry.channel_id === channel.channel_id)
          ?.frame?.decoded?.frame_id ?? "",
      transform: null,
      intrinsics: null,
    }));
  const queryError = support.error || bindings.error || bundleResource.error;
  function refresh() {
    support.reload();
    rigs.reload();
    bindings.reload();
    bundleResource.reload();
    rigRevisions.reload();
  }
  function changeCursor(time: string) {
    setPlaying(false);
    onCursor(time);
  }
  return (
    <div className="sensor-workspace" data-testid="sensor-replay-workspace">
      <div className="sensor-workspace-actions">
        <div>
          <button
            type="button"
            className="button secondary small"
            onClick={() => setRigEditor("new")}
          >
            <Plus size={14} />
            Create sensor rig
          </button>
          <button
            type="button"
            className="button secondary small"
            disabled={
              !rigs.data?.length ||
              !channels.some((channel) => channel.status === "supported")
            }
            onClick={() => setBindingOpen(true)}
          >
            <Link2 size={14} />
            Bind recording
          </button>
        </div>
        <button
          type="button"
          className="icon-button"
          aria-label="Refresh sensor replay"
          onClick={refresh}
        >
          <RefreshCw size={15} />
        </button>
      </div>
      {notice && (
        <p className="sensor-success" role="status">
          {notice}
        </p>
      )}
      <div className="sensor-binding-toolbar">
        <label>
          Replay binding
          <select
            aria-label="Replay binding revision"
            value={bindingRevision}
            onChange={(event) => {
              setBindingSelection(Number(event.target.value));
              setCameraSelection(null);
              setLidarSelection(null);
              setPlaying(false);
            }}
          >
            <option value={0}>Unbound · native channels</option>
            {bindings.data?.map((entry) => (
              <option key={entry.revision} value={entry.revision}>
                Binding r{entry.revision} · {entry.rig_snapshot.name} r
                {entry.rig_revision}
              </option>
            ))}
          </select>
        </label>
        <label>
          Point preview limit
          <select
            aria-label="Point preview limit"
            value={points}
            onChange={(event) => setPoints(Number(event.target.value))}
          >
            <option value={2000}>2,000 points</option>
            <option value={10000}>10,000 points</option>
            <option value={20000}>20,000 points</option>
            <option value={100000}>100,000 points</option>
          </select>
        </label>
      </div>
      <div className="sensor-shared-cursor">
        <div>
          <span>SHARED LOG-TIME CURSOR</span>
          <code data-testid="sensor-cursor-ns">{cursor}</code>
        </div>
        <div>
          <button
            type="button"
            className="button secondary small"
            disabled={!nextTime && !playing}
            onClick={() => setPlaying((value) => !value)}
            aria-label={
              playing ? "Pause sensor frame steps" : "Play sensor frame steps"
            }
          >
            {playing ? <Pause size={14} /> : <Play size={14} />}
            {playing ? "Pause" : "Frame steps"}
          </button>
          <button
            type="button"
            className="button secondary small"
            disabled={!nextTime || bundleResource.loading}
            onClick={() => {
              if (nextTime) changeCursor(nextTime);
            }}
          >
            <SkipForward size={14} />
            Next sample time
          </button>
        </div>
      </div>
      <p className="sensor-replay-boundary">
        {playing
          ? "Frame-step playback · up to 4 cursor advances/s, not real-time video. "
          : ""}
        Each channel selects its latest sample at or before the aligned cursor.
        Source: <strong>{recording.source_kind} · user-declared</strong>. No
        camera projection or ego-motion compensation.
      </p>
      {queryError && (
        <div className="sensor-query-error" role="alert">
          <p>{queryError}</p>
          <button
            type="button"
            className="button secondary small"
            onClick={refresh}
          >
            Retry sensor replay
          </button>
        </div>
      )}
      <div
        className="sensor-mobile-tabs"
        role="tablist"
        aria-label="Sensor viewport"
      >
        <button
          type="button"
          role="tab"
          aria-selected={mobileView === "camera"}
          onClick={() => setMobileView("camera")}
        >
          Camera view
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mobileView === "lidar"}
          onClick={() => setMobileView("lidar")}
        >
          LiDAR view
        </button>
      </div>
      <div className={`sensor-dual-view mobile-${mobileView}`}>
        <SensorReplayPanel
          onInfer={onInfer}
          key="camera"
          kind="camera"
          channels={replayChannels}
          channelId={cameraId}
          onChannel={(id) => {
            setCameraSelection(id);
            setPlaying(false);
          }}
          entry={bundle?.entries.find(
            (entry) => entry.channel_id === Number(cameraId),
          )}
          rig={bundle?.rig_snapshot ?? null}
          loading={
            support.loading || bundleResource.loading || bindings.loading
          }
          error={queryError}
          onInspect={(channelId, ordinal) => setInspect({ channelId, ordinal })}
        />
        <SensorReplayPanel
          key="lidar"
          kind="lidar"
          channels={replayChannels}
          channelId={lidarId}
          onChannel={(id) => {
            setLidarSelection(id);
            setPlaying(false);
          }}
          entry={bundle?.entries.find(
            (entry) => entry.channel_id === Number(lidarId),
          )}
          rig={bundle?.rig_snapshot ?? null}
          loading={
            support.loading || bundleResource.loading || bindings.loading
          }
          error={queryError}
          onInspect={(channelId, ordinal) => setInspect({ channelId, ordinal })}
        />
      </div>
      {bundle?.warnings.map((warning, index) => (
        <p key={index} className="sensor-warning">
          {warning}
        </p>
      ))}
      <details className="sensor-registry panel" open={Boolean(binding)}>
        <summary>
          <Box size={16} />
          Rig registry & binding provenance
          <ChevronDown size={14} />
        </summary>
        <div className="sensor-registry-body">
          {binding ? (
            <>
              <div className="sensor-evidence-title">
                <strong>Pinned binding r{binding.revision}</strong>
                <DownloadLink
                  href={binding.download_url}
                  className="sensor-text-button"
                  download
                >
                  <ArrowDownToLine size={13} />
                  Download binding
                </DownloadLink>
              </div>
              <p>
                {binding.rig_snapshot.name} r{binding.rig_revision} ·{" "}
                {binding.rig_snapshot.reference_frame} ·{" "}
                {binding.rig_snapshot.coordinate_convention.replaceAll(
                  "_",
                  " ",
                )}
              </p>
              <p>
                Rig source: {binding.rig_snapshot.source_kind} ·{" "}
                {binding.rig_snapshot.source || "Not supplied"} ·
                source-declared
              </p>
              <p>{binding.note}</p>
              <div className="sensor-hash">
                <span>Binding SHA-256</span>
                <code>{binding.manifest_hash}</code>
                <CopyButton text={binding.manifest_hash} />
              </div>
            </>
          ) : (
            <p className="sensor-form-help">
              No binding is applied. Native decoding remains available. Choose a
              rig revision and channel mapping to investigate source-declared
              geometry.
            </p>
          )}
          {rigs.error && (
            <p className="inline-error" role="alert">
              {rigs.error}
            </p>
          )}
          <div className="sensor-rig-browser">
            <label>
              Registered sensor rig
              <select
                aria-label="Registered sensor rig"
                value={chosenRigId}
                onChange={(event) => {
                  setRigId(event.target.value);
                  setRigRevision(null);
                }}
              >
                <option value="">Select a rig</option>
                {rigs.data?.map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {entry.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Rig revision
              <select
                aria-label="Rig revision"
                value={chosenRigRevision}
                onChange={(event) => setRigRevision(Number(event.target.value))}
              >
                {(rigRevisions.data ?? (rigHead ? [rigHead] : [])).map(
                  (entry) => (
                    <option key={entry.revision} value={entry.revision}>
                      Revision {entry.revision}
                    </option>
                  ),
                )}
              </select>
            </label>
            <button
              type="button"
              className="button secondary small"
              disabled={!chosenRig || !rigHead}
              onClick={() => setRigEditor(chosenRig!)}
            >
              Create rig revision
            </button>
          </div>
          {rigRevisions.error && (
            <p className="inline-error" role="alert">
              {rigRevisions.error}
            </p>
          )}
          {chosenRig && (
            <div
              className="sensor-rig-definition"
              data-testid="sensor-rig-detail"
            >
              <div className="sensor-evidence-title">
                <strong>
                  {chosenRig.name} · r{chosenRig.revision}
                </strong>
                <DownloadLink
                  href={chosenRig.download_url}
                  className="sensor-text-button"
                  download
                >
                  <ArrowDownToLine size={13} />
                  Download rig
                </DownloadLink>
              </div>
              <p>{chosenRig.note}</p>
              <p>
                {chosenRig.source_kind} ·{" "}
                {chosenRig.source || "No source label"} ·{" "}
                {formatDate(chosenRig.created_at)}
              </p>
              <div className="sensor-rig-sensor-list">
                {chosenRig.sensors.map((sensor) => (
                  <div key={sensor.key}>
                    <strong>
                      {sensor.key} · {sensor.kind}
                    </strong>
                    <span>
                      {sensor.frame_id} → {chosenRig.reference_frame}
                    </span>
                    <code>
                      {sensor.transform
                        ? `T [${sensor.transform.translation_m.join(", ")}] m · q [${sensor.transform.rotation_xyzw.join(", ")}]`
                        : "Static transform unknown"}
                    </code>
                    {sensor.intrinsics && (
                      <details>
                        <summary>Declared camera intrinsics</summary>
                        <pre>{JSON.stringify(sensor.intrinsics, null, 2)}</pre>
                      </details>
                    )}
                  </div>
                ))}
              </div>
              <div className="sensor-hash">
                <span>Rig manifest SHA-256</span>
                <code>{chosenRig.manifest_hash}</code>
                <CopyButton text={chosenRig.manifest_hash} />
              </div>
            </div>
          )}
        </div>
      </details>
      <details className="sensor-capability-panel panel">
        <summary>
          Channels & exact decoder support
          <span>
            {
              channels.filter((channel) => channel.status === "supported")
                .length
            }{" "}
            / {channels.length} supported
          </span>
        </summary>
        <div>
          {support.loading ? (
            <p>
              <LoaderCircle size={15} className="spin" />
              Loading registered capabilities…
            </p>
          ) : (
            channels.map((channel) => (
              <div className="sensor-capability-row" key={channel.channel_id}>
                <strong>
                  #{channel.channel_id} · {channel.topic}
                </strong>
                <span>
                  {channel.status} · {channel.kind ?? "unknown kind"}
                </span>
                <small>
                  {channel.schema_name ?? "No schema"} ·{" "}
                  {channel.message_encoding}
                </small>
                {channel.reason && <p>{channel.reason}</p>}
                {channel.decoders.map((decoder) => (
                  <p key={`${decoder.id}-${decoder.version}`}>
                    {decoder.name} · {decoder.id} / {decoder.version}
                  </p>
                ))}
                <button
                  type="button"
                  className="sensor-text-button"
                  onClick={() =>
                    setInspect({
                      channelId: channel.channel_id,
                      ordinal:
                        bundle?.entries.find(
                          (entry) => entry.channel_id === channel.channel_id,
                        )?.message?.ordinal ?? null,
                    })
                  }
                >
                  <FileSearch size={13} />
                  Inspect file ordinal
                </button>
              </div>
            ))
          )}
        </div>
      </details>
      {rigEditor && (
        <SensorRigEditor
          rig={rigEditor === "new" ? undefined : rigEditor}
          initialSensors={initialSensors.length ? initialSensors : undefined}
          expectedRevision={
            rigEditor === "new"
              ? undefined
              : rigs.data?.find((rig) => rig.id === rigEditor.id)?.revision
          }
          onClose={() => setRigEditor(null)}
          onSaved={(rig) => {
            setRigEditor(null);
            setNewRigId(rig.id);
            setRigId(rig.id);
            setRigRevision(rig.revision);
            rigs.reload();
            rigRevisions.reload();
            setNotice(
              `Sensor rig ${rig.name} r${rig.revision} saved. Bind this immutable revision to the recording to apply its declarations.`,
            );
          }}
        />
      )}
      {bindingOpen && (
        <SensorBindingEditor
          recordingId={recording.id}
          binding={bindings.data?.[0] ?? null}
          rigs={rigs.data ?? []}
          channels={channels}
          initialRigId={newRigId}
          selectedChannelIds={channelIds}
          onClose={() => setBindingOpen(false)}
          onSaved={(binding) => {
            setBindingOpen(false);
            setBindingSelection(binding.revision);
            setCameraSelection(null);
            setLidarSelection(null);
            setNewRigId(undefined);
            bindings.reload();
            bundleResource.reload();
            setNotice(
              `Binding r${binding.revision} saved with ${binding.channels.length} exact channel assignments.`,
            );
          }}
        />
      )}
      {inspect && (
        <SensorFrameInspector
          onInfer={onInfer}
          recordingId={recording.id}
          channels={channels}
          channelId={inspect.channelId}
          ordinal={inspect.ordinal}
          onClose={() => setInspect(null)}
          onOpenRaw={onOpenRaw}
        />
      )}
    </div>
  );
}
