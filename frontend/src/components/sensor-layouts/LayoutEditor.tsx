import { RotateCcw } from "lucide-react";
import LayoutDiagram from "./LayoutDiagram";
import { mountChanges, sensorKeys, sensorLabels } from "./layout-contract";
import type { LayoutRig, LayoutSensorKey } from "./layout-contract";

export default function LayoutEditor({ baseline, rig, selected, onSelect, onChange, disabled }: { baseline: LayoutRig; rig: LayoutRig; selected: LayoutSensorKey; onSelect: (key: LayoutSensorKey) => void; onChange: (rig: LayoutRig) => void; disabled: boolean }) {
  const sensor = rig.sensors.find(item => item.key === selected)!;
  function change(field: "translation_m" | "rotation_deg", index: number, value: number) {
    const next = structuredClone(rig); next.sensors.find(item => item.key === selected)![field][index] = value; onChange(next);
  }
  function reset(all: boolean) { const next = structuredClone(rig); for (const item of next.sensors) if (all || item.key === selected) { const original = baseline.sensors.find(s => s.key === item.key)!; item.translation_m = [...original.translation_m]; item.rotation_deg = [...original.rotation_deg]; } onChange(next); }
  const changes = mountChanges(baseline, rig);
  return <section className="layout-editor" aria-label="센서 B 배치 편집">
    <div className="layout-sensor-picker" role="group" aria-label="편집할 센서">{sensorKeys.map(key => <button key={key} aria-pressed={selected === key} onClick={() => onSelect(key)}>{sensorLabels[key]}</button>)}</div>
    <LayoutDiagram rig={rig} selected={selected} onSelect={onSelect}/>
    <div className="layout-editor-label"><strong>B · {sensorLabels[selected]} 장착 위치</strong><span>다음 실행에 적용할 편집값</span></div>
    <fieldset disabled={disabled}><legend>위치 · 미터</legend><div className="layout-numbers">{["X · 전방", "Y · 왼쪽", "Z · 위"].map((label, index) => <label key={label}>{label}<input aria-label={`${sensorLabels[selected]} ${label} (m)`} type="number" min={-10} max={10} step={.05} value={Number.isFinite(sensor.translation_m[index]) ? sensor.translation_m[index] : ""} onChange={event => change("translation_m", index, event.target.valueAsNumber)}/></label>)}</div></fieldset>
    <fieldset disabled={disabled}><legend>회전 · 도</legend><div className="layout-numbers">{["Pitch", "Yaw", "Roll"].map((label, index) => <label key={label}>{label}<input aria-label={`${sensorLabels[selected]} ${label} (deg)`} type="number" min={-180} max={180} step={1} value={Number.isFinite(sensor.rotation_deg[index]) ? sensor.rotation_deg[index] : ""} onChange={event => change("rotation_deg", index, event.target.valueAsNumber)}/></label>)}</div></fieldset>
    <p className="layout-hint">양의 Pitch: 전방이 아래로 · 양의 Yaw: 왼쪽으로 · 양의 Roll: 센서 왼쪽이 위로</p>
    <div className="layout-actions"><button disabled={disabled} onClick={() => reset(false)}><RotateCcw size={13}/>센서 초기화</button><button disabled={disabled} onClick={() => reset(true)}>전체 초기화</button></div>
    <div className="layout-editor-delta"><strong>편집값의 변경 {changes.length}개</strong>{changes.length ? <ul>{changes.map(change => <li key={`${change.sensor_key}:${change.field}:${change.axis}`}>{sensorLabels[change.sensor_key]} · {(change.field === "translation_m" ? ["X", "Y", "Z"] : ["Pitch", "Yaw", "Roll"])[change.axis]} <b>{change.baseline} → {Number.isFinite(change.value) ? change.value : "입력 필요"}{change.field === "translation_m" ? " m" : "°"}</b></li>)}</ul> : <p>A 기준과 같은 배치입니다.</p>}</div>
  </section>;
}
