import { useState } from "react";
import { ChevronDown, Trash2 } from "lucide-react";
import type { CameraIntrinsics, RigSensor, SensorKind } from "./SensorTypes";

export interface RigSensorDraft {
  localId: string;
  key: string;
  name: string;
  kind: SensorKind;
  frameId: string;
  transformDeclared: boolean;
  translation: [string, string, string];
  rotation: [string, string, string, string];
  intrinsicsText: string;
}
export function sensorDraft(sensor?: RigSensor, index = 1): RigSensorDraft {
  return {
    localId: crypto.randomUUID(),
    key: sensor?.key ?? `sensor-${index}`,
    name: sensor?.name ?? "",
    kind: sensor?.kind ?? "camera",
    frameId: sensor?.frame_id ?? "",
    transformDeclared: Boolean(sensor?.transform),
    translation: (sensor?.transform?.translation_m.map(String) as [
      string,
      string,
      string,
    ]) ?? ["0", "0", "0"],
    rotation: (sensor?.transform?.rotation_xyzw.map(String) as [
      string,
      string,
      string,
      string,
    ]) ?? ["0", "0", "0", "1"],
    intrinsicsText: sensor?.intrinsics
      ? JSON.stringify(sensor.intrinsics, null, 2)
      : "",
  };
}
export function authoredSensor(draft: RigSensorDraft): RigSensor {
  const translation = draft.translation.map(Number) as [number, number, number];
  const rotation = draft.rotation.map(Number) as [
    number,
    number,
    number,
    number,
  ];
  if (
    draft.transformDeclared &&
    [...draft.translation, ...draft.rotation].some(
      (value) => !value.trim() || !Number.isFinite(Number(value)),
    )
  )
    throw new Error(
      `Sensor ${draft.key}: all declared transform values must be finite numbers.`,
    );
  if (
    draft.transformDeclared &&
    Math.abs(rotation.reduce((sum, value) => sum + value * value, 0) - 1) > 1e-6
  )
    throw new Error(
      `Sensor ${draft.key}: quaternion must have unit norm. Values are not automatically normalized.`,
    );
  let intrinsics: CameraIntrinsics | null = null;
  if (draft.kind === "camera" && draft.intrinsicsText.trim()) {
    try {
      intrinsics = JSON.parse(draft.intrinsicsText) as CameraIntrinsics;
    } catch {
      throw new Error(
        `Sensor ${draft.key}: camera intrinsics must be valid JSON.`,
      );
    }
    if (
      !intrinsics ||
      typeof intrinsics !== "object" ||
      Array.isArray(intrinsics)
    )
      throw new Error(
        `Sensor ${draft.key}: camera intrinsics must be an object.`,
      );
  }
  return {
    key: draft.key.trim(),
    name: draft.name.trim(),
    kind: draft.kind,
    frame_id: draft.frameId.trim(),
    transform: draft.transformDeclared
      ? { translation_m: translation, rotation_xyzw: rotation }
      : null,
    intrinsics,
  };
}

export default function SensorRigRow({
  value,
  index,
  onChange,
  onRemove,
  removable,
}: {
  value: RigSensorDraft;
  index: number;
  onChange: (value: RigSensorDraft) => void;
  onRemove: () => void;
  removable: boolean;
}) {
  const [intrinsicsOpen, setIntrinsicsOpen] = useState(
    Boolean(value.intrinsicsText),
  );
  const update = (patch: Partial<RigSensorDraft>) =>
    onChange({ ...value, ...patch });
  return (
    <article className="sensor-rig-row" aria-label={`Rig sensor ${index + 1}`}>
      <header>
        <strong>Sensor {index + 1}</strong>
        <span>{value.kind}</span>
        <button
          type="button"
          className="icon-button"
          aria-label={`Remove rig sensor ${index + 1}`}
          disabled={!removable}
          onClick={onRemove}
        >
          <Trash2 size={15} />
        </button>
      </header>
      <div className="sensor-form-grid four">
        <label>
          Sensor key
          <input
            value={value.key}
            required
            maxLength={64}
            pattern="[A-Za-z0-9][A-Za-z0-9_.\-]*"
            onChange={(event) => update({ key: event.target.value })}
          />
        </label>
        <label>
          Sensor name
          <input
            value={value.name}
            required
            maxLength={180}
            onChange={(event) => update({ name: event.target.value })}
          />
        </label>
        <label>
          Sensor kind
          <select
            value={value.kind}
            onChange={(event) =>
              update({ kind: event.target.value as SensorKind })
            }
          >
            <option value="camera">Camera</option>
            <option value="lidar">LiDAR</option>
          </select>
        </label>
        <label>
          Sensor frame ID
          <input
            value={value.frameId}
            required
            maxLength={180}
            onChange={(event) => update({ frameId: event.target.value })}
          />
        </label>
      </div>
      <label className="sensor-checkbox">
        <input
          type="checkbox"
          aria-label="Declare static transform"
          checked={value.transformDeclared}
          onChange={(event) =>
            update({ transformDeclared: event.target.checked })
          }
        />
        Declare static transform<span>sensor → rig</span>
      </label>
      {value.transformDeclared ? (
        <>
          <div className="sensor-form-grid three">
            {(["X", "Y", "Z"] as const).map((axis, index) => (
              <label key={axis}>
                Translation {axis}
                <input
                  type="number"
                  min={-1000000}
                  max={1000000}
                  aria-label={`Translation ${axis}`}
                  step="any"
                  required
                  value={value.translation[index]}
                  onChange={(event) => {
                    const next = [
                      ...value.translation,
                    ] as RigSensorDraft["translation"];
                    next[index] = event.target.value;
                    update({ translation: next });
                  }}
                />
                <small>metres</small>
              </label>
            ))}
          </div>
          <div className="sensor-form-grid four">
            {(["X", "Y", "Z", "W"] as const).map((axis, index) => (
              <label key={axis}>
                Quaternion {axis}
                <input
                  type="number"
                  aria-label={`Quaternion ${axis}`}
                  step="any"
                  required
                  value={value.rotation[index]}
                  onChange={(event) => {
                    const next = [
                      ...value.rotation,
                    ] as RigSensorDraft["rotation"];
                    next[index] = event.target.value;
                    update({ rotation: next });
                  }}
                />
              </label>
            ))}
          </div>
          <p className="sensor-form-help">
            Declared translation and unit quaternion apply sensor coordinates to
            the rig frame. Registration does not establish calibration accuracy.
          </p>
        </>
      ) : (
        <p className="sensor-form-help">
          Transform unknown. Native sensor preview remains available; rig-frame
          rendering is withheld.
        </p>
      )}
      {value.kind === "camera" && (
        <div className="sensor-intrinsics-editor">
          <button
            type="button"
            className="sensor-text-button"
            aria-expanded={intrinsicsOpen}
            onClick={() => setIntrinsicsOpen((open) => !open)}
          >
            <ChevronDown size={14} /> Optional camera intrinsics
          </button>
          {intrinsicsOpen && (
            <label>
              Camera intrinsics JSON
              <textarea
                value={value.intrinsicsText}
                rows={5}
                onChange={(event) =>
                  update({ intrinsicsText: event.target.value })
                }
                placeholder={
                  '{"width": 640, "height": 480, "fx": 500, "fy": 500, "cx": 320, "cy": 240, "distortion_model": "none", "distortion_coefficients": []}'
                }
              />
              <small>
                Leave blank when unknown. These declarations are retained for
                inspection; camera projection is not implemented.
              </small>
            </label>
          )}
        </div>
      )}
    </article>
  );
}
