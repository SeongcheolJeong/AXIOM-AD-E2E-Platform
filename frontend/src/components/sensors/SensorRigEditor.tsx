import { useState, type FormEvent } from "react";
import { LoaderCircle, Plus, Save } from "lucide-react";
import { api } from "../../api";
import { Modal } from "../ui";
import type { RecordingSourceKind } from "../recordings/RecordingTypes";
import SensorRigRow, {
  authoredSensor,
  sensorDraft,
  type RigSensorDraft,
} from "./SensorRigRow";
import type {
  CoordinateConvention,
  RigCreate,
  RigDetail,
  RigSensor,
} from "./SensorTypes";

export default function SensorRigEditor({
  rig,
  initialSensors,
  expectedRevision,
  onClose,
  onSaved,
}: {
  rig?: RigDetail;
  initialSensors?: RigSensor[];
  expectedRevision?: number;
  onClose: () => void;
  onSaved: (rig: RigDetail) => void;
}) {
  const [name, setName] = useState(rig?.name ?? "");
  const [description, setDescription] = useState(rig?.description ?? "");
  const [referenceFrame, setReferenceFrame] = useState(
    rig?.reference_frame ?? "",
  );
  const [convention, setConvention] = useState<CoordinateConvention>(
    rig?.coordinate_convention ?? "unknown",
  );
  const [sourceKind, setSourceKind] = useState<RecordingSourceKind>(
    rig?.source_kind ?? "unknown",
  );
  const [source, setSource] = useState(rig?.source ?? "");
  const [note, setNote] = useState(rig?.note ?? "");
  const [guard] = useState(expectedRevision ?? rig?.revision ?? 1);
  const [sensors, setSensors] = useState<RigSensorDraft[]>(
    (rig?.sensors ?? initialSensors)?.map((sensor, index) =>
      sensorDraft(sensor, index + 1),
    ) ?? [sensorDraft()],
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      const body: RigCreate = {
        name: name.trim(),
        description: description.trim(),
        reference_frame: referenceFrame.trim(),
        coordinate_convention: convention,
        source_kind: sourceKind,
        source: source.trim(),
        note: note.trim(),
        sensors: sensors.map(authoredSensor),
      };
      if (
        new Set(body.sensors.map((sensor) => sensor.key)).size !==
        body.sensors.length
      )
        throw new Error("Each sensor key must be unique.");
      if (
        new Set(body.sensors.map((sensor) => sensor.frame_id)).size !==
        body.sensors.length
      )
        throw new Error("Each sensor frame ID must be unique.");
      const saved = await api<RigDetail>(
        rig
          ? `/sensor-rigs/${encodeURIComponent(rig.id)}/revisions`
          : "/sensor-rigs",
        {
          method: "POST",
          body: JSON.stringify(
            rig ? { ...body, expected_revision: guard } : body,
          ),
        },
      );
      onSaved(saved);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "The sensor rig could not be saved.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title={rig ? "Create sensor rig revision" : "Create sensor rig"}
      subtitle="Reusable source-declared sensor geometry. Unknown transforms remain unknown."
      onClose={() => {
        if (!busy) onClose();
      }}
      className="sensor-modal"
    >
      <form className="sensor-form" onSubmit={submit}>
        <fieldset disabled={busy}>
          <div className="sensor-form-grid two">
            <label>
              Rig name
              <input
                autoFocus
                value={name}
                required
                maxLength={180}
                onChange={(event) => setName(event.target.value)}
              />
            </label>
            <label>
              Reference frame
              <input
                value={referenceFrame}
                required
                maxLength={180}
                onChange={(event) => setReferenceFrame(event.target.value)}
                placeholder="Explicit rig frame ID"
              />
            </label>
          </div>
          <label>
            Rig description
            <textarea
              value={description}
              maxLength={4000}
              rows={2}
              onChange={(event) => setDescription(event.target.value)}
            />
          </label>
          <div className="sensor-form-grid two">
            <label>
              Coordinate convention
              <select
                value={convention}
                onChange={(event) =>
                  setConvention(event.target.value as CoordinateConvention)
                }
              >
                <option value="unknown">Unknown</option>
                <option value="right_handed_z_up">Right-handed · Z up</option>
                <option value="left_handed_z_up">Left-handed · Z up</option>
              </select>
            </label>
            <label>
              Rig source kind
              <select
                value={sourceKind}
                onChange={(event) =>
                  setSourceKind(event.target.value as RecordingSourceKind)
                }
              >
                {["unknown", "synthetic", "simulation", "measured"].map(
                  (kind) => (
                    <option key={kind} value={kind}>
                      {kind}
                    </option>
                  ),
                )}
              </select>
            </label>
          </div>
          <label>
            Rig source
            <input
              value={source}
              maxLength={1000}
              onChange={(event) => setSource(event.target.value)}
              placeholder="Source document, calibration procedure or authored fixture"
            />
          </label>
          <label>
            Rig note
            <textarea
              value={note}
              required
              maxLength={4000}
              rows={2}
              onChange={(event) => setNote(event.target.value)}
              placeholder="Basis and limits of this declaration"
            />
          </label>
          <div className="sensor-form-section-title">
            <h3>Sensors</h3>
            <span>{sensors.length} / 32</span>
            <button
              type="button"
              className="button secondary small"
              disabled={sensors.length >= 32}
              onClick={() =>
                setSensors((rows) => [
                  ...rows,
                  sensorDraft(undefined, rows.length + 1),
                ])
              }
            >
              <Plus size={14} /> Add sensor
            </button>
          </div>
          {sensors.map((sensor, index) => (
            <SensorRigRow
              key={sensor.localId}
              value={sensor}
              index={index}
              removable={sensors.length > 1}
              onRemove={() =>
                setSensors((rows) =>
                  rows.filter((row) => row.localId !== sensor.localId),
                )
              }
              onChange={(value) =>
                setSensors((rows) =>
                  rows.map((row) =>
                    row.localId === sensor.localId ? value : row,
                  ),
                )
              }
            />
          ))}
        </fieldset>
        {rig && (
          <p className="sensor-form-help">
            Creates a new immutable revision from r{rig.revision}.
            Latest-revision guard: r{guard}. Existing recording bindings keep
            their pinned revision.
          </p>
        )}
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
        <footer className="sensor-form-footer">
          <button
            type="button"
            className="button secondary"
            disabled={busy}
            onClick={onClose}
          >
            Cancel
          </button>
          <button type="submit" className="button primary" disabled={busy}>
            {busy ? (
              <LoaderCircle size={15} className="spin" />
            ) : (
              <Save size={15} />
            )}
            {rig ? "Publish rig revision" : "Save sensor rig"}
          </button>
        </footer>
      </form>
    </Modal>
  );
}
