import { useRef, useState } from "react";
import { api, authHeaders, useResource } from "../../api";
import ObjectMapStage from "./ObjectMapStage";
import {
  appendJunction,
  deleteRoad,
  emptyObjectMap,
  moveHistory,
  newRoad,
  roadId,
} from "./object-map-contract";
import type {
  ObjectMapRecord,
  ObjectMapSource,
  ObjectRoad,
  ObjectMapPreview,
  RoadGeometry,
} from "./object-map-contract";
import type { CatalogRef, CatalogItem, CityMap } from "./city-contract";
import "./city-sim.css";
function NumberField({
  label,
  value,
  min,
  max,
  step = 0.1,
  onChange,
  disabled = false,
}: {
  label: string;
  value: number;
  min?: number;
  max?: number;
  step?: number;
  onChange: (n: number) => void;
  disabled?: boolean;
}) {
  return (
    <label>
      {label}
      <input
        type="number"
        aria-label={label}
        value={value}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        onChange={(e) => {
          const n = e.target.valueAsNumber;
          if (Number.isFinite(n)) onChange(n);
        }}
      />
    </label>
  );
}
export default function ObjectMapEditor({
  project,
  onBuilt,
}: {
  project: string;
  onBuilt: (ref: CatalogRef) => void;
}) {
  const records = useResource<{ items: ObjectMapRecord[] }>(
      `/object-maps?project_id=${encodeURIComponent(project)}`,
    ),
    identity = useResource<{ role: string; mode: string }>("/team/whoami"),
    maps = useResource<{ items: CatalogItem<CityMap>[] }>(
      `/maps?project_id=${encodeURIComponent(project)}&limit=1000`,
    );
  const [history, setHistory] = useState<{
      past: ObjectMapSource[];
      present: ObjectMapSource;
      future: ObjectMapSource[];
    }>({ past: [], present: emptyObjectMap(), future: [] }),
    [saved, setSaved] = useState<ObjectMapRecord | null>(null),
    [selected, setSelected] = useState(""),
    [view, setView] = useState<"2d" | "3d">("2d"),
    [placing, setPlacing] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [preview, setPreview] = useState<ObjectMapPreview | null>(null),
    [built, setBuilt] = useState<CatalogRef | null>(null),
    [serverVersion, setServerVersion] = useState(""),
    [connection, setConnection] = useState({
      from_road: "",
      from_lane: -1,
      to_road: "",
      to_lane: -1,
    });
  const file = useRef<HTMLInputElement>(null),
    pending = useRef<{ body: string; key: string } | null>(null),
    draft = history.present,
    road = draft.roads.find((r) => r.id === selected),
    writable =
      !!identity.data &&
      (identity.data.mode !== "project" ||
        ["Engineer", "Admin"].includes(identity.data.role)),
    readOnly = draft.read_only === true,
    disabled = busy || !writable || readOnly;
  const dirty = JSON.stringify(draft) !== JSON.stringify(saved?.definition);
  function edit(fn: (d: ObjectMapSource) => void) {
    if (disabled) return;
    setHistory((h) => {
      const next = structuredClone(h.present);
      fn(next);
      return {
        past: [...h.past, h.present].slice(-80),
        present: next,
        future: [],
      };
    });
    setPreview(null);
    setBuilt(null);
    setNotice("");
  }
  function editRoad(fn: (r: ObjectRoad) => void) {
    edit((d) => {
      const r = d.roads.find((r) => r.id === selected);
      if (r) fn(r);
    });
  }
  function reset(definition: ObjectMapSource, record: ObjectMapRecord | null) {
    setHistory({ past: [], present: definition, future: [] });
    setSaved(record);
    setSelected(definition.roads[0]?.id ?? "");
    setPreview(null);
    setBuilt(null);
    setPlacing(false);
  }
  async function perform(work: () => Promise<void>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await work();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  const post = <T,>(path: string, body: unknown) => {
    const text = JSON.stringify(body);
    if (pending.current?.body !== text)
      pending.current = { body: text, key: crypto.randomUUID() };
    return api<T>(path, {
      method: "POST",
      body: text,
      headers: { "Idempotency-Key": pending.current.key },
    });
  };
  function addRoad() {
    const id = roadId(draft);
    edit((d) => {
      const last = d.roads.at(-1);
      d.roads.push(
        newRoad(id, {
          x: last ? last.origin.x : 0,
          y: last ? last.origin.y + 15 : 0,
          z: 0,
          heading_rad: 0,
        }),
      );
    });
    setSelected(id);
  }
  function addJunction(kind: "tee" | "cross") {
    const next = appendJunction(draft, kind);
    edit((d) => Object.assign(d, next));
    setSelected(next.roads[draft.roads.length].id);
  }
  async function save() {
    await perform(async () => {
      const body = {
        project_id: project,
        id: saved?.id ?? `map-${crypto.randomUUID().slice(0, 12)}`,
        expected_revision: saved?.revision ?? 0,
        definition: draft,
      };
      const record = await post<ObjectMapRecord>("/object-maps", body);
      reset(record.definition, record);
      records.reload();
      setNotice(
        `지도 원본 r${record.revision} 저장됨. 빌드하면 실행 지도 카탈로그에 발행됩니다.`,
      );
    });
  }
  function download(content: string, name: string, type: string) {
    const url = URL.createObjectURL(new Blob([content], { type })),
      a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function build() {
    await perform(async () => {
      if (!saved || dirty) throw new Error("먼저 현재 지도 원본을 저장하세요.");
      const result = await post<
        {
          map?: CatalogRef;
          map_record?: CatalogRef;
          catalog?: CatalogRef;
        } & Partial<CatalogRef>
      >(`/object-maps/${encodeURIComponent(saved.id)}/build`, {
        project_id: project,
        revision: saved.revision,
        sha256: saved.sha256,
        expected_map_revision:
          maps.data?.items.find(
            (m) =>
              (m.definition.generated as { source?: CatalogRef } | undefined)
                ?.source?.id === saved.id,
          )?.revision ?? 0,
        server_version:
          serverVersion ||
          String(
            maps.data?.items.find((m) => m.definition.server_version)
              ?.definition.server_version ?? "",
          ),
      });
      const ref = result.map ?? result.map_record ?? result.catalog ?? result;
      if (!ref.id || !ref.sha256 || !ref.revision)
        throw new Error(
          "빌드 응답에 실행 지도 개정이 없습니다. 원본 결과를 확인하세요.",
        );
      setBuilt({ id: ref.id, revision: ref.revision, sha256: ref.sha256 });
      maps.reload();
      setNotice(
        `실행 지도 ${ref.id} r${ref.revision} 발행됨. 실제 CARLA 주행은 시나리오 실행에서 확인합니다.`,
      );
    });
  }
  return (
    <section className="city-workspace" aria-label="Object Sim road map editor">
      <div className="city-intro">
        <div>
          <span className="city-eyebrow">OBJECT SIM · ROAD NETWORK</span>
          <h2>도로를 작성하고 실행 가능한 지도로 발행하세요.</h2>
          <p>
            차로·연결·신호·장애물을 편집합니다. 기본 도로 3D와 실제 CARLA
            렌더링은 별도입니다. 기존 Town의 주변 건물·지형은 생성 지도에
            복제되지 않습니다.
          </p>
        </div>
        <span className="city-engine-status">
          {saved
            ? `원본 r${saved.revision}${dirty ? " · 미저장 수정" : ""}`
            : "새 지도"}
        </span>
      </div>
      <div className="city-toolbar">
        <label>
          저장 지도
          <select
            aria-label="Saved editable map"
            value={saved?.id ?? ""}
            disabled={busy}
            onChange={(e) => {
              const r = records.data?.items.find(
                (r) => r.id === e.target.value,
              );
              if (r) reset(r.definition, r);
            }}
          >
            <option value="">새 지도</option>
            {records.data?.items.map((r) => (
              <option key={r.id} value={r.id}>
                {r.definition.name} · r{r.revision}
              </option>
            ))}
          </select>
        </label>
        <button
          className="button secondary"
          disabled={busy}
          onClick={() => {
            reset(emptyObjectMap(), null);
            setError("");
            setNotice("");
          }}
        >
          빈 지도
        </button>
        <button
          className="button secondary"
          disabled={busy || !writable}
          onClick={() => file.current?.click()}
        >
          OpenDRIVE 가져오기
        </button>
        <input
          ref={file}
          hidden
          type="file"
          accept=".xodr,.xml"
          onChange={(e) => {
            const picked = e.target.files?.[0];
            e.target.value = "";
            if (!picked) return;
            void perform(async () => {
              if (picked.size > 20 * 1024 * 1024)
                throw new Error("OpenDRIVE는 20MB 이하로 선택하세요.");
              const response = await post<{
                definition: ObjectMapSource;
                preview?: ObjectMapPreview;
              }>("/object-maps/import", {
                project_id: project,
                content: await picked.text(),
                name: picked.name.replace(/\.xodr$/i, ""),
                rights: draft.rights,
              });
              reset(response.definition, null);
              if (response.preview) setPreview(response.preview);
              setNotice("가져온 원본의 지원 범위를 확인한 후 저장하세요.");
            });
          }}
        />
        <button
          className="button secondary"
          disabled={!saved || busy}
          onClick={() =>
            void perform(async () => {
              if (!saved) return;
              const response = await fetch(
                `/api/v1/object-maps/${encodeURIComponent(saved.id)}/export?project_id=${encodeURIComponent(project)}&revision=${saved.revision}`,
                { headers: authHeaders() },
              );
              if (!response.ok)
                throw new Error(`OpenDRIVE 내보내기 실패 (${response.status})`);
              download(
                await response.text(),
                `${saved.id}-r${saved.revision}.xodr`,
                "application/xml",
              );
            })
          }
        >
          저장 개정 OpenDRIVE
        </button>
        <button
          className="button secondary"
          onClick={() =>
            download(
              JSON.stringify(draft, null, 2),
              "object-map-source.json",
              "application/json",
            )
          }
        >
          편집 원본 내보내기
        </button>
      </div>
      {(error || records.error) && (
        <p className="expansion-error" role="alert">
          {error || records.error}
        </p>
      )}
      {notice && (
        <p className="expansion-success" role="status">
          {notice}
        </p>
      )}
      {readOnly && (
        <p className="expansion-error">
          이 원본은 읽기 전용입니다. 미지원 도로 요소를 삭제하거나 실행 가능한
          지도로 가장하지 않습니다. {JSON.stringify(draft.unsupported ?? "")}
        </p>
      )}
      <div className="city-toolbar">
        <button
          className="button secondary"
          disabled={disabled || !history.past.length}
          onClick={() => {
            setHistory((h) => moveHistory(h, "undo"));
            setPreview(null);
            setBuilt(null);
          }}
        >
          실행 취소
        </button>
        <button
          className="button secondary"
          disabled={disabled || !history.future.length}
          onClick={() => {
            setHistory((h) => moveHistory(h, "redo"));
            setPreview(null);
            setBuilt(null);
          }}
        >
          다시 실행
        </button>
        <button className="button" disabled={disabled} onClick={addRoad}>
          도로 추가
        </button>
        <button
          className="button secondary"
          disabled={disabled}
          onClick={() => addJunction("tee")}
        >
          T자 교차로 추가
        </button>
        <button
          className="button secondary"
          disabled={disabled}
          onClick={() => addJunction("cross")}
        >
          십자 교차로 추가
        </button>
        <div role="group" aria-label="Map editor view">
          {(["2d", "3d"] as const).map((m) => (
            <button
              key={m}
              className={`button ${view === m ? "" : "secondary"}`}
              aria-pressed={view === m}
              onClick={() => {
                setView(m);
                setPlacing(false);
              }}
            >
              {m.toUpperCase()}
            </button>
          ))}
        </div>
        <button
          className="button secondary"
          disabled={busy || !draft.roads.length}
          onClick={() =>
            void perform(async () => {
              const result = await post<ObjectMapPreview>(
                "/object-maps/preview",
                { project_id: project, definition: draft },
              );
              setPreview(result);
              setNotice(
                "서버 지도 검사를 완료했습니다. 빌드 및 실제 실행은 별도입니다.",
              );
            })
          }
        >
          연결·형상 검사
        </button>
        <button
          className="button"
          disabled={
            busy || !writable || !dirty || (!draft.roads.length && !readOnly)
          }
          onClick={() => void save()}
        >
          지도 개정 저장
        </button>
        <button
          className="button"
          disabled={
            disabled ||
            !saved ||
            dirty ||
            !(
              serverVersion ||
              maps.data?.items.some((m) => m.definition.server_version)
            )
          }
          onClick={() => void build()}
        >
          실행 지도 빌드
        </button>
        {built && (
          <button className="button" onClick={() => onBuilt(built)}>
            이 지도로 시나리오 작성 →
          </button>
        )}
      </div>
      <label className="object-map-server-version">
        빌드 대상 CARLA 버전 · 실시간 연결 검사가 아님
        <input
          aria-label="Map build CARLA version"
          value={
            serverVersion ||
            String(
              maps.data?.items.find((m) => m.definition.server_version)
                ?.definition.server_version ?? "",
            )
          }
          onChange={(e) => setServerVersion(e.target.value)}
          disabled={busy}
        />
      </label>
      <div className="object-map-editor-layout">
        <aside className="city-panel object-map-inspector">
          <label>
            지도 이름
            <input
              aria-label="Editable map name"
              disabled={disabled}
              value={draft.name}
              onChange={(e) =>
                edit((d) => {
                  d.name = e.target.value;
                })
              }
            />
          </label>
          <div className="city-actor-list">
            {draft.roads.map((r) => (
              <button
                key={r.id}
                className={selected === r.id ? "selected" : ""}
                onClick={() => setSelected(r.id)}
              >
                <strong>{r.name}</strong>
                <span>
                  {r.lanes.length} 차로 ·{" "}
                  {r.geometries.reduce((s, g) => s + g.length_m, 0).toFixed(0)}m
                </span>
              </button>
            ))}
          </div>
          {road ? (
            <>
              <h3>선택 도로 · {road.id}</h3>
              <label>
                도로 이름
                <input
                  disabled={disabled}
                  value={road.name}
                  onChange={(e) =>
                    editRoad((r) => {
                      r.name = e.target.value;
                    })
                  }
                />
              </label>
              <div className="city-field-grid">
                <NumberField
                  label="도로 원점 X · m"
                  value={road.origin.x}
                  disabled={disabled}
                  onChange={(n) =>
                    editRoad((r) => {
                      r.origin.x = n;
                    })
                  }
                />
                <NumberField
                  label="도로 원점 Y · m"
                  value={road.origin.y}
                  disabled={disabled}
                  onChange={(n) =>
                    editRoad((r) => {
                      r.origin.y = n;
                    })
                  }
                />
                <NumberField
                  label="고도 Z · m"
                  value={road.origin.z}
                  disabled={disabled}
                  onChange={(n) =>
                    editRoad((r) => {
                      r.origin.z = n;
                    })
                  }
                />
                <NumberField
                  label="도로 방향 · deg"
                  value={
                    Math.round(
                      ((road.origin.heading_rad * 180) / Math.PI) * 100,
                    ) / 100
                  }
                  step={1}
                  disabled={disabled}
                  onChange={(n) =>
                    editRoad((r) => {
                      r.origin.heading_rad = (n * Math.PI) / 180;
                    })
                  }
                />
              </div>
              <div className="city-button-row">
                <button
                  className="button secondary"
                  disabled={disabled || view !== "2d"}
                  onClick={() => setPlacing(!placing)}
                  aria-pressed={placing}
                >
                  원점 지도에 배치
                </button>
                <button
                  className="button secondary"
                  disabled={disabled}
                  onClick={() => {
                    const id = roadId(draft);
                    edit((d) => {
                      const copy = structuredClone(road);
                      copy.id = id;
                      copy.name = `${road.name} 복사`;
                      copy.origin.y += 15;
                      d.roads.push(copy);
                    });
                    setSelected(id);
                  }}
                >
                  복제
                </button>
                <button
                  className="button secondary"
                  disabled={disabled}
                  onClick={() => {
                    const next = deleteRoad(draft, road.id);
                    edit((d) => Object.assign(d, next));
                    setSelected(next.roads[0]?.id ?? "");
                  }}
                >
                  도로 삭제
                </button>
              </div>
              <h4>도로 형상</h4>
              {road.geometries.map((g, i) => (
                <div className="object-map-subcard" key={i}>
                  <label>
                    구간 {i + 1}
                    <select
                      aria-label={`Road geometry ${i + 1}`}
                      disabled={disabled}
                      value={g.kind}
                      onChange={(e) =>
                        editRoad((r) => {
                          r.geometries[i].kind = e.target
                            .value as RoadGeometry["kind"];
                          if (e.target.value === "line") {
                            r.geometries[i].curvature_start = 0;
                            r.geometries[i].curvature_end = 0;
                          } else if (e.target.value === "arc") {
                            r.geometries[i].curvature_start ||= 0.02;
                            r.geometries[i].curvature_end =
                              r.geometries[i].curvature_start;
                          }
                        })
                      }
                    >
                      <option value="line">직선</option>
                      <option value="arc">원호</option>
                      <option value="spiral">완화곡선</option>
                    </select>
                  </label>
                  <NumberField
                    label={`구간 ${i + 1} 길이 · m`}
                    value={g.length_m}
                    min={1}
                    max={1000}
                    step={1}
                    disabled={disabled}
                    onChange={(n) =>
                      editRoad((r) => {
                        r.geometries[i].length_m = n;
                      })
                    }
                  />
                  {g.kind !== "line" && (
                    <NumberField
                      label={`구간 ${i + 1} 시작 곡률 · 1/m`}
                      value={g.curvature_start}
                      step={0.001}
                      disabled={disabled}
                      onChange={(n) =>
                        editRoad((r) => {
                          r.geometries[i].curvature_start = n;
                          if (g.kind === "arc")
                            r.geometries[i].curvature_end = n;
                        })
                      }
                    />
                  )}
                  {g.kind === "spiral" && (
                    <NumberField
                      label={`구간 ${i + 1} 끝 곡률 · 1/m`}
                      value={g.curvature_end}
                      step={0.001}
                      disabled={disabled}
                      onChange={(n) =>
                        editRoad((r) => {
                          r.geometries[i].curvature_end = n;
                        })
                      }
                    />
                  )}
                  <button
                    className="button secondary"
                    disabled={disabled || road.geometries.length === 1}
                    onClick={() =>
                      editRoad((r) => {
                        r.geometries.splice(i, 1);
                      })
                    }
                  >
                    구간 삭제
                  </button>
                </div>
              ))}
              <button
                className="button secondary"
                disabled={disabled}
                onClick={() =>
                  editRoad((r) => {
                    r.geometries.push({
                      kind: "line",
                      length_m: 30,
                      curvature_start: 0,
                      curvature_end: 0,
                    });
                  })
                }
              >
                형상 구간 추가
              </button>
              <h4>차로 · 음수는 도로 방향, 양수는 반대 방향</h4>
              {road.lanes.map((lane, i) => (
                <div key={i} className="object-map-subcard">
                  <strong>차로 {lane.id}</strong>
                  <div className="city-field-grid">
                    <NumberField
                      label={`차로 ${lane.id} 폭 · m`}
                      value={lane.width_m}
                      min={2}
                      max={6}
                      disabled={disabled}
                      onChange={(n) =>
                        editRoad((r) => {
                          r.lanes[i].width_m = n;
                        })
                      }
                    />
                    <NumberField
                      label={`차로 ${lane.id} 제한 속도 · m/s`}
                      value={lane.speed_limit_mps}
                      min={0.1}
                      max={12}
                      disabled={disabled}
                      onChange={(n) =>
                        editRoad((r) => {
                          r.lanes[i].speed_limit_mps = n;
                        })
                      }
                    />
                  </div>
                  <NumberField
                    label={`차로 ${lane.id} 우선권 · 높을수록 우선`}
                    value={lane.priority ?? 0}
                    min={0}
                    max={100}
                    step={1}
                    disabled={disabled}
                    onChange={(n) =>
                      editRoad((r) => {
                        r.lanes[i].priority = n;
                      })
                    }
                  />
                  <label>
                    차선 변경
                    <select
                      disabled={disabled}
                      value={lane.lane_change}
                      onChange={(e) =>
                        editRoad((r) => {
                          r.lanes[i].lane_change = e.target
                            .value as typeof lane.lane_change;
                        })
                      }
                    >
                      <option value="both">양쪽 허용</option>
                      <option value="none">금지</option>
                      <option value="left">왼쪽</option>
                      <option value="right">오른쪽</option>
                    </select>
                  </label>
                  <label className="city-checkbox">
                    <input
                      type="checkbox"
                      disabled={disabled}
                      checked={lane.closed}
                      onChange={(e) =>
                        editRoad((r) => {
                          r.lanes[i].closed = e.target.checked;
                        })
                      }
                    />
                    통행 폐쇄
                  </label>
                  <button
                    className="button secondary"
                    disabled={
                      disabled ||
                      road.lanes.filter(
                        (l) =>
                          Math.sign(l.id) === Math.sign(lane.id) &&
                          Math.abs(l.id) > Math.abs(lane.id),
                      ).length > 0 ||
                      road.lanes.length === 1
                    }
                    onClick={() =>
                      edit((d) => {
                        const r = d.roads.find((r) => r.id === selected)!;
                        r.lanes.splice(i, 1);
                        d.connections = d.connections.filter(
                          (c) =>
                            !(
                              c.from_road === r.id && c.from_lane === lane.id
                            ) && !(c.to_road === r.id && c.to_lane === lane.id),
                        );
                        d.signals = d.signals.map((s) =>
                          s.road_id === r.id
                            ? {
                                ...s,
                                lane_ids: s.lane_ids.filter(
                                  (id) => id !== lane.id,
                                ),
                              }
                            : s,
                        );
                      })
                    }
                  >
                    바깥 차로 삭제
                  </button>
                </div>
              ))}
              <div className="city-button-row">
                {[-1, 1].map((sign) => (
                  <button
                    key={sign}
                    className="button secondary"
                    disabled={
                      disabled ||
                      road.lanes.filter((l) => Math.sign(l.id) === sign)
                        .length >= 6
                    }
                    onClick={() =>
                      editRoad((r) => {
                        const id =
                          sign *
                          (1 +
                            Math.max(
                              0,
                              ...r.lanes
                                .filter((l) => Math.sign(l.id) === sign)
                                .map((l) => Math.abs(l.id)),
                            ));
                        r.lanes.push({
                          id,
                          width_m: 3.5,
                          speed_limit_mps: 12,
                          lane_change: "both",
                          closed: false,
                        });
                      })
                    }
                  >
                    {sign < 0 ? "정방향" : "역방향"} 차로 추가
                  </button>
                ))}
              </div>
              <h4>교차로 소속</h4>
              <select
                aria-label="Road junction"
                disabled={disabled}
                value={road.junction_id ?? ""}
                onChange={(e) =>
                  editRoad((r) => {
                    r.junction_id = e.target.value || null;
                  })
                }
              >
                <option value="">일반 도로</option>
                {draft.junctions.map((j) => (
                  <option value={j.id} key={j.id}>
                    {j.name}
                  </option>
                ))}
              </select>
              <button
                className="button secondary"
                disabled={disabled}
                onClick={() =>
                  edit((d) => {
                    let n = 1;
                    while (d.junctions.some((j) => j.id === `j${n}`)) n++;
                    const id = `j${n}`;
                    d.junctions.push({ id, name: `교차로 ${n}` });
                    d.roads.find((r) => r.id === selected)!.junction_id = id;
                  })
                }
              >
                교차로 만들기 · 이 도로 포함
              </button>
            </>
          ) : (
            <div className="city-empty">
              <h3>도로를 추가하세요</h3>
              <p>원점과 형상을 편집한 뒤 차로를 연결합니다.</p>
            </div>
          )}
        </aside>
        <div>
          <ObjectMapStage
            source={draft}
            selected={selected}
            onSelect={setSelected}
            mode={view}
            onPlace={
              placing && !disabled
                ? (point) => {
                    editRoad((r) => {
                      r.origin.x = Math.round(point.x);
                      r.origin.y = Math.round(point.y);
                    });
                    setPlacing(false);
                  }
                : undefined
            }
          />
          <p className="city-note">
            표시 선은 로컬 편집 미리보기입니다. 실행 지도 빌드에서 지원 형상과
            연결을 다시 검사합니다.
          </p>
          {preview && (
            <section className="city-panel" aria-label="Map validation results">
              <h3>지도 검사</h3>
              {preview.issues.length ? (
                <ul>
                  {preview.issues.map((issue, i) => (
                    <li key={i}>
                      {typeof issue === "string" ? (
                        issue
                      ) : (
                        <>
                          <b>{issue.severity ?? "확인"}</b> ·{" "}
                          {issue.road_id && (
                            <button onClick={() => setSelected(issue.road_id!)}>
                              도로 {issue.road_id}
                            </button>
                          )}{" "}
                          {issue.message}
                        </>
                      )}
                    </li>
                  ))}
                </ul>
              ) : (
                <p>
                  보고된 연결·형상 문제 없음 · 실제 엔진 로딩 및 주행은 별도
                </p>
              )}
            </section>
          )}
          <section className="city-panel">
            <h3>차로 연결 · 합류와 분기</h3>
            <p>
              출발 차로 끝에서 다음 차로 시작으로 연결합니다. 물리적으로 떨어진
              끝점은 자동으로 도로를 만들어 붙이지 않습니다.
            </p>
            <div className="object-map-connection-fields">
              {(["from", "to"] as const).map((side) => (
                <div key={side}>
                  <label>
                    {side === "from" ? "출발 도로" : "도착 도로"}
                    <select
                      aria-label={`${side} connection road`}
                      value={connection[`${side}_road`]}
                      disabled={disabled}
                      onChange={(e) =>
                        setConnection((c) => ({
                          ...c,
                          [`${side}_road`]: e.target.value,
                          [`${side}_lane`]:
                            draft.roads.find((r) => r.id === e.target.value)
                              ?.lanes[0]?.id ?? -1,
                        }))
                      }
                    >
                      <option value="">선택</option>
                      {draft.roads.map((r) => (
                        <option key={r.id} value={r.id}>
                          {r.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    차로
                    <select
                      aria-label={`${side} connection lane`}
                      value={connection[`${side}_lane`]}
                      disabled={disabled}
                      onChange={(e) =>
                        setConnection((c) => ({
                          ...c,
                          [`${side}_lane`]: Number(e.target.value),
                        }))
                      }
                    >
                      {draft.roads
                        .find((r) => r.id === connection[`${side}_road`])
                        ?.lanes.map((l) => (
                          <option value={l.id} key={l.id}>
                            {l.id}
                          </option>
                        ))}
                    </select>
                  </label>
                </div>
              ))}
            </div>
            <button
              className="button secondary"
              disabled={
                disabled ||
                !connection.from_road ||
                !connection.to_road ||
                draft.connections.some(
                  (c) => JSON.stringify(c) === JSON.stringify(connection),
                )
              }
              onClick={() =>
                edit((d) => {
                  d.connections.push({ ...connection });
                })
              }
            >
              차로 연결 추가
            </button>
            <div className="object-map-row-list">
              {draft.connections.map((c, i) => (
                <div key={i}>
                  <span>
                    {c.from_road} / {c.from_lane} → {c.to_road} / {c.to_lane}
                  </span>
                  <button
                    disabled={disabled}
                    onClick={() =>
                      edit((d) => {
                        d.connections.splice(i, 1);
                      })
                    }
                  >
                    연결 삭제
                  </button>
                </div>
              ))}
            </div>
          </section>
          <section className="city-panel">
            <h3>교통시설 · OpenDRIVE 객체 메타데이터</h3>
            <p className="city-note">
              현재 지도 객체는 기본 3D 편집 미리보기용 메타데이터입니다. 객체가
              있는 지도 빌드는 거절됩니다. 실제 장애물은 시나리오에서 static
              asset을 배치하세요.
            </p>
            <p>
              선택 도로: {road?.name ?? "선택 필요"} · s는 도로 시작부터 거리,
              t는 횡방향 위치입니다.
            </p>
            <div className="city-button-row">
              {(["traffic_light", "stop", "yield"] as const).map((kind) => (
                <button
                  key={kind}
                  className="button secondary"
                  disabled={disabled || !road}
                  onClick={() =>
                    edit((d) => {
                      let n = 1;
                      while (d.signals.some((s) => s.id === `signal-${n}`)) n++;
                      d.signals.push({
                        id: `signal-${n}`,
                        road_id: road!.id,
                        s_m: 10,
                        t_m: 4,
                        kind,
                        orientation: "+",
                        lane_ids: road!.lanes
                          .filter((l) => l.id < 0)
                          .map((l) => l.id),
                      });
                    })
                  }
                >
                  {
                    {
                      traffic_light: "신호등",
                      stop: "정지 표지",
                      yield: "양보 표지",
                    }[kind]
                  }{" "}
                  추가
                </button>
              ))}
              <button
                className="button secondary"
                disabled={disabled || !road}
                onClick={() =>
                  edit((d) => {
                    let n = 1;
                    while (d.objects.some((o) => o.id === `object-${n}`)) n++;
                    d.objects.push({
                      id: `object-${n}`,
                      road_id: road!.id,
                      s_m: 20,
                      t_m: 2,
                      length_m: 4,
                      width_m: 1.5,
                      height_m: 1,
                    });
                  })
                }
              >
                객체 메타데이터 추가
              </button>
            </div>
            {draft.signals
              .filter((s) => s.road_id === selected)
              .map((signal) => {
                const i = draft.signals.indexOf(signal);
                return (
                  <div className="object-map-subcard" key={signal.id}>
                    <strong>
                      {signal.id} · {signal.kind}
                    </strong>
                    <div className="city-field-grid">
                      <NumberField
                        label={`${signal.id} s · m`}
                        value={signal.s_m}
                        min={0}
                        disabled={disabled}
                        onChange={(n) =>
                          edit((d) => {
                            d.signals[i].s_m = n;
                          })
                        }
                      />
                      <NumberField
                        label={`${signal.id} t · m`}
                        value={signal.t_m}
                        disabled={disabled}
                        onChange={(n) =>
                          edit((d) => {
                            d.signals[i].t_m = n;
                          })
                        }
                      />
                    </div>
                    <label>
                      적용 방향
                      <select
                        disabled={disabled}
                        value={signal.orientation}
                        onChange={(e) =>
                          edit((d) => {
                            d.signals[i].orientation = e.target.value as
                              "+" | "-";
                          })
                        }
                      >
                        <option value="+">도로 정방향</option>
                        <option value="-">도로 역방향</option>
                      </select>
                    </label>
                    <div className="city-button-row">
                      {road?.lanes.map((l) => (
                        <label className="city-checkbox" key={l.id}>
                          <input
                            type="checkbox"
                            disabled={disabled}
                            checked={signal.lane_ids.includes(l.id)}
                            onChange={(e) =>
                              edit((d) => {
                                d.signals[i].lane_ids = e.target.checked
                                  ? [...signal.lane_ids, l.id]
                                  : signal.lane_ids.filter((id) => id !== l.id);
                              })
                            }
                          />
                          차로 {l.id}
                        </label>
                      ))}
                    </div>
                    <button
                      disabled={disabled}
                      onClick={() =>
                        edit((d) => {
                          d.signals.splice(i, 1);
                        })
                      }
                    >
                      시설 삭제
                    </button>
                  </div>
                );
              })}
            {draft.objects
              .filter((o) => o.road_id === selected)
              .map((object) => {
                const i = draft.objects.indexOf(object);
                return (
                  <div key={object.id} className="object-map-subcard">
                    <strong>{object.id}</strong>
                    <div className="city-field-grid">
                      {(
                        [
                          "s_m",
                          "t_m",
                          "length_m",
                          "width_m",
                          "height_m",
                        ] as const
                      ).map((key) => (
                        <NumberField
                          key={key}
                          label={`${object.id} ${key}`}
                          value={object[key]}
                          min={key === "t_m" ? undefined : 0}
                          disabled={disabled}
                          onChange={(n) =>
                            edit((d) => {
                              d.objects[i][key] = n;
                            })
                          }
                        />
                      ))}
                    </div>
                    <button
                      disabled={disabled}
                      onClick={() =>
                        edit((d) => {
                          d.objects.splice(i, 1);
                        })
                      }
                    >
                      객체 메타데이터 삭제
                    </button>
                  </div>
                );
              })}
          </section>
          <details className="city-panel">
            <summary>지도 권리와 출처</summary>
            {(["license", "source_url", "notes"] as const).map((key) => (
              <label key={key}>
                {key}
                <input
                  disabled={disabled}
                  value={draft.rights[key]}
                  onChange={(e) =>
                    edit((d) => {
                      d.rights[key] = e.target.value;
                    })
                  }
                />
              </label>
            ))}
            <label>
              재배포 권한
              <select
                value={draft.rights.redistribution}
                disabled={disabled}
                onChange={(e) =>
                  edit((d) => {
                    d.rights.redistribution = e.target.value;
                  })
                }
              >
                <option value="unknown">미확인</option>
                <option value="permitted">허용</option>
                <option value="restricted">제한</option>
              </select>
            </label>
          </details>
        </div>
      </div>
    </section>
  );
}
