#!/usr/bin/env python3
"""Create an offline viewer from verified native/API observations only."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

HTML = r'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AXIOM · Native 정지 상태 관측</title><style>
*{box-sizing:border-box}body{margin:0;background:#f1f4f7;color:#1a2c3e;font:15px/1.65 system-ui,sans-serif}main{max-width:1240px;margin:auto;padding:28px}h1{font-size:28px}h2{font-size:20px}h3{font-size:16px;margin-top:24px}section{background:white;border:1px solid #d6e0e8;border-radius:10px;padding:22px;margin:18px 0;min-width:0}p,pre,td,code{overflow-wrap:anywhere}pre{white-space:pre-wrap;font-size:12px}.notice{background:#fff2d6;border-left:4px solid #ae7920;padding:14px}.kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.kpis div{padding:16px;background:#eaf1f6;border-radius:6px}.kpis strong{display:block;font-size:26px}.muted{color:#586d7e}.scroll{overflow:auto;max-height:510px}table{border-collapse:collapse;width:100%;font-size:12px}td,th{padding:8px;border-bottom:1px solid #d7e1e8;vertical-align:top;text-align:left}th{position:sticky;top:0;background:#eaf1f6}svg{width:100%;height:215px;display:block}select{font:inherit;padding:8px;max-width:100%}a{color:#17568b}code{font-size:12px}@media(max-width:600px){main{padding:12px}section{padding:12px}.kpis{grid-template-columns:1fr}h1{font-size:22px}}
</style><main><h1>저속 정지 · 엔진 내부 상태를 같은 프레임에 연결</h1>
<p class="muted">별도 CARLA2300 · 설치 바이너리 고정 · 기존 LiDAR/kernel2/제동 후보 유지</p>
<p class="notice">주행 품질은 여전히 failed입니다. GDB는 별도 자식 프로세스만 계측했습니다. 아래 flag는 API 관측 시점의 native 상태이며 모든 내부 solver 시점의 기록이나 단일 원인 증명은 아닙니다. 적용 brake torque는 미관측입니다.</p>
<section><div class="kpis"><div>종방향 정지 제약 활성<strong id="stickyOn"></strong>네 바퀴 flag가 관측된 시각</div><div>차체 sleep 활성<strong id="sleepOn"></strong>급정지와 구분되는 후속 상태</div><div>실제 차량 업데이트<strong id="clockKpi"></strong>기록된 함수 호출 / 외부 프레임</div></div><p id="checks"></p></section>
<section><h2>급정지와 내부 상태의 시간 관계</h2><label>그래프 구간 <select id="window"><option value="all">전체 0–4초</option><option value="tail">정지 말단 1.6–2.5초</option></select></label>
<h3>실제 속도 · m/s</h3><svg id="speed"></svg>
<h3>구간 가속도 · m/s²</h3><svg id="acceleration"></svg>
<h3>Jerk · m/s³ · 기존 50ms 차분</h3><svg id="jerk"></svg>
<h3>Brake · 실선 내부 처리값 / 점선 다음 명령 요청</h3><svg id="brake"></svg>
<h3>활성 바퀴 수 · 실선 종방향 / 점선 횡방향 타이어 정지 제약</h3><svg id="sticky"></svg>
<h3>차체 sleep · 0=false / 1=true</h3><svg id="sleep"></svg>
<p class="muted">세로선은 처음 관측된 종방향 활성과 sleep 시점입니다. 구간 내 정확한 최초 발동 시각을 보간하지 않습니다. 표는 항상 전체 81개 원본 관측을 유지합니다.</p></section>
<section><h2>Native flag 변화</h2><pre id="transitions"></pre><h2>실행 시간 간격 확인</h2><p>설정 readback과 실제 함수 호출을 구분합니다. CARLA는 max_substeps=50에 범위 경고를 냈지만, 이번 설치 바이너리의 아래 호출 기록은 실제 관측값입니다. 다른 빌드의 동작까지 보장하지 않습니다.</p><pre id="clock"></pre></section>
<section><h2>전체 관측 · frame/actor ID 정확히 연결</h2><div class="scroll"><table id="samples"></table></div></section>
<section><h2>실험 출처와 한계</h2><pre id="provenance"></pre><p>계측 기준 주행은 같은 설정의 별도 엔진 실행입니다. 내부 pause가 wall time을 바꾸므로 성능 벤치마크로 사용하지 않습니다. API wheel omega는 native corrected wheel speeds와 대조하며 raw integration speeds도 별도로 보존합니다.</p><a href="analysis.json">분석 JSON</a> · <a href="samples.csv">전체 표본 CSV</a></section></main>
<script type="application/json" id="payload">__DATA__</script><script>
'use strict';const D=JSON.parse(document.getElementById('payload').textContent),E=id=>document.getElementById(id),fmt=(x,n=6)=>x===null||x===undefined?'—':Number(x).toFixed(n),esc=x=>String(x).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const rows=D.rows.map(r=>({...r,internal_brake:r.native.movement.BrakeInput,forward_count:r.native.sticky_forward_active.filter(Boolean).length,side_count:r.native.sticky_side_active.filter(Boolean).length,sleep_value:Number(r.native.rigid_sleeping)}));const firstSticky=rows.find(r=>r.forward_count>0),firstSleep=rows.find(r=>r.sleep_value>0);
E('stickyOn').textContent=firstSticky?fmt(firstSticky.time_s,2)+'초':'미관측';E('sleepOn').textContent=firstSleep?fmt(firstSleep.time_s,2)+'초':'미관측';E('clockKpi').textContent=Object.keys(D.native_clock.updates_per_frame_histogram).join('/')+'회';E('checks').textContent='81/81 ego 관측의 frame/actor 연결 · native corrected wheel speeds와 API 값 일치 · 기준 주행과 최대 속도 차이 '+D.private_baseline_max_speed_difference_mps.toExponential(3)+'m/s';
function chart(id,key,dashed=null,steps=false){const all=rows.filter(r=>E('window').value==='all'||r.time_s>=1.6&&r.time_s<=2.5),s=E(id),W=1100,H=215,L=78,R=25,T=12,B=30,xmin=all[0].time_s,xmax=all[all.length-1].time_s,values=all.flatMap(r=>[r[key],dashed?r[dashed]:null]).filter(Number.isFinite);let lo=Math.min(0,...values),hi=Math.max(0,...values);if(hi===lo)hi=lo+1;const p=(hi-lo)*.08;lo-=p;hi+=p;const X=x=>L+(x-xmin)/(xmax-xmin)*(W-L-R),Y=y=>H-B-(y-lo)/(hi-lo)*(H-T-B);let h='';for(let i=0;i<=4;i++){let y=lo+(hi-lo)*i/4;h+='<line x1="'+L+'" x2="'+(W-R)+'" y1="'+Y(y)+'" y2="'+Y(y)+'" stroke="#e2e8ee"/><text x="'+(L-9)+'" y="'+(Y(y)+4)+'" text-anchor="end" font-size="12">'+y.toFixed(2)+'</text>';let x=xmin+(xmax-xmin)*i/4;h+='<text x="'+X(x)+'" y="'+(H-7)+'" text-anchor="middle" font-size="12">'+x.toFixed(2)+'s</text>'}for(const marker of [firstSticky,firstSleep])if(marker&&marker.time_s>=xmin&&marker.time_s<=xmax)h+='<line x1="'+X(marker.time_s)+'" x2="'+X(marker.time_s)+'" y1="'+T+'" y2="'+(H-B)+'" stroke="#bf8934" stroke-dasharray="4 5"/>';[key,dashed].filter(Boolean).forEach((k,j)=>{let d='',previous=null;for(const r of all){if(!Number.isFinite(r[k])){previous=null;continue}if(!previous)d+='M'+X(r.time_s)+','+Y(r[k]);else d+=steps?'H'+X(r.time_s)+'V'+Y(r[k]):'L'+X(r.time_s)+','+Y(r[k]);previous=r}h+='<path d="'+d+'" fill="none" stroke="'+(j?'#a45d2b':'#1d5b84')+'" stroke-width="2" '+(j?'stroke-dasharray="7 4"':'')+'/>'});s.setAttribute('viewBox','0 0 '+W+' '+H);s.innerHTML=h}
function render(){chart('speed','speed_mps');chart('acceleration','acceleration_mps2');chart('jerk','jerk_mps3');chart('brake','internal_brake','requested_brake');chart('sticky','forward_count','side_count',true);chart('sleep','sleep_value',null,true)}E('window').onchange=render;render();
E('transitions').textContent=JSON.stringify(D.flag_transitions,null,2);const C=D.native_clock;E('clock').textContent=JSON.stringify({frames:C.frames,vehicle_updates:C.updates,updates_per_frame:C.updates_per_frame_histogram,min_dt_seconds:C.min_delta_seconds,max_dt_seconds:C.max_delta_seconds,scope:C.scope},null,2);
E('samples').innerHTML='<thead><tr><th># / 시각 / frame</th><th>속도 / 가속도 / jerk</th><th>이전 명령 readback / 내부 brake / 다음 요청</th><th>종방향 / 횡방향 / sleep</th><th>내부 원본</th></tr></thead><tbody>'+rows.map(r=>'<tr><td>'+r.index+' / '+fmt(r.time_s,2)+' / '+r.world_frame+'</td><td>'+fmt(r.speed_mps)+' / '+fmt(r.acceleration_mps2)+' / '+fmt(r.jerk_mps3)+'</td><td>'+fmt(r.reported_brake)+' / '+fmt(r.internal_brake)+' / '+fmt(r.requested_brake)+'</td><td>'+r.forward_count+' / '+r.side_count+' / '+r.sleep_value+'</td><td><details><summary>원본 값</summary><pre>'+esc(JSON.stringify(r.native,null,2))+'</pre></details></td></tr>').join('')+'</tbody>';
E('provenance').textContent=JSON.stringify({status:D.status,execution_id:D.execution_id,quality:D.quality_verdict,native_stream_sha256:D.native_stream_sha256,observer:D.observer_header,scope:D.scope},null,2);
</script></html>'''


def render(source: Path, output: Path):
    from analyze_native_stop_observer import parse
    raw = source.read_bytes()
    data = parse(raw)
    if data.get('status') != 'exact_frame_join_verified' or len(data.get('rows', [])) != 81:
        raise ValueError('A complete verified native analysis is required')
    output.mkdir(parents=True, exist_ok=False)
    (output / 'analysis.json').write_bytes(raw)
    safe = raw.decode().replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
    (output / 'index.html').write_text(HTML.replace('__DATA__', safe), encoding='utf-8')
    names = ('index', 'time_s', 'world_frame', 'actor_id', 'speed_mps', 'acceleration_mps2',
             'jerk_mps3', 'reported_brake', 'requested_brake')
    with (output / 'samples.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=[*names, 'sticky_forward_active', 'sticky_side_active',
            'rigid_sleeping', 'internal_brake', 'raw_wheel_speeds', 'corrected_wheel_speeds', 'updates_in_frame'])
        writer.writeheader()
        for row in data['rows']:
            native = row['native']
            writer.writerow({**{k: row[k] for k in names},
                'sticky_forward_active': native['sticky_forward_active'], 'sticky_side_active': native['sticky_side_active'],
                'rigid_sleeping': native['rigid_sleeping'], 'internal_brake': native['movement']['BrakeInput'],
                'raw_wheel_speeds': native['wheel_speeds_native'], 'corrected_wheel_speeds': native['corrected_wheel_speeds_native'],
                'updates_in_frame': row['native_clock']['count']})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    render(args.input, args.output)
    print(json.dumps({'report': str(args.output / 'index.html'), 'observations': 81}))
