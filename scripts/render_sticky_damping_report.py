"""Render the verified A/B, retaining full samples regardless of chart zoom."""
from pathlib import Path
import argparse
import csv
import json

HTML = r'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AXIOM · 저속 타이어 정지 전이 비교</title><style>
*{box-sizing:border-box}body{margin:0;font:15px/1.6 system-ui,sans-serif;background:#f1f4f7;color:#172c3a}main{max-width:1220px;margin:auto;padding:26px}h1{font-size:28px}h2{font-size:20px}section{background:white;border:1px solid #d4dee6;border-radius:10px;padding:20px;margin:20px 0;min-width:0}p,pre,td,code{overflow-wrap:anywhere}pre{white-space:pre-wrap;font-size:12px}.notice{padding:14px;border-left:4px solid #ad7922;background:#fff4d9}.scroll{overflow:auto;max-height:480px}table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:9px;border-bottom:1px solid #d9e2e8}th{background:#edf2f6;position:sticky;top:0}select{font:inherit;padding:6px;max-width:100%}svg{width:100%;height:230px;display:block}.pass{color:#147052}.fail{color:#a43c32}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.card{background:#edf2f6;border-radius:8px;padding:14px}.big{font-size:24px;font-weight:700}.sub{color:#536779}a{color:#156c9a}@media(max-width:600px){main{padding:12px}section{padding:12px}.cards{grid-template-columns:1fr}h1{font-size:22px}.big{font-size:21px}}</style>
<main><h1>저속 타이어 정지 전이 · 감쇠 변경 비교</h1>
<p>실제 CARLA / 별도 엔진 / 동일 LiDAR·제어기·50ms 평가 · 2026-09-27</p>
<p class="notice">후보는 제어기 개선이 아니라 가상 차량의 물리 제약 변경입니다. 종방향 sticky 제약은 유지하고 감쇠만 바꿉니다. 실차 보정·안전 인증·운영 배포 완료가 아닙니다.</p>
<section><h2>전체 4초 결과</h2><div class="cards" id="cards"></div><div class="scroll"><table id="metrics"></table></div><p id="counts"></p></section>
<section><h2>기준군 1000 / 후보 4</h2><p>회색: 기준군 · 청록: 후보. 확대는 그래프만 바꾸며 판정과 전체 표본 표는 그대로입니다.</p>
<label>시간창 <select id="window"><option value="all">전체 0–4초</option><option value="stop">정지 전이 1.5–2.6초</option></select></label>
<h3>실제 속도 m/s</h3><svg id="speed"></svg><h3>원본 속도 차분 가속도 m/s²</h3><svg id="acceleration"></svg>
<h3>Jerk m/s³ · 기존 기준 ±10</h3><svg id="jerk"></svg><h3>활성 종방향 sticky 제약 수 · 0–4바퀴</h3><svg id="sticky"></svg></section>
<section><h2>정책·원본 검증</h2><pre id="verification"></pre></section>
<section><h2>전체 162개 관측</h2><div class="scroll"><table id="samples"></table></div></section>
<section><h2>해석 범위</h2><p>후보는 별도 프로세스의 제약 생성 결과에만 적용됩니다. 속도·가속도·jerk·제어 요청·활성 플래그·목표 속도·impulse 제한은 수정하지 않았습니다. 이 시험에서는 두 차량의 종방향 sticky 행 모두가 적용 대상입니다.</p><p>초기 정착과 속도 주입을 포함한 입력 구성은 같지만 숨은 엔진 상태가 동일하다고 주장하지 않습니다. 계측용 breakpoint가 실행을 지연시키므로 wall time은 성능 비교에 사용하지 않습니다.</p><pre id="provenance"></pre><a href="analysis.json">검증 JSON</a> · <a href="samples.csv">전체 표본 CSV</a></section></main>
<script id="payload" type="application/json">__DATA__</script><script>
'use strict';const D=JSON.parse(document.getElementById('payload').textContent),E=id=>document.getElementById(id),names=['sham','candidate'],label={sham:'기준 1000',candidate:'후보 4'},fmt=x=>x===null||x===undefined?'—':typeof x==='number'?x.toFixed(5):String(x),esc=x=>String(x).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const metric=(n,k)=>D.trials[n].native.metrics.find(m=>m.name===k),data=n=>D.trials[n].native.rows.map(r=>({...r,sticky_count:r.native.sticky_forward_active.filter(Boolean).length}));
E('cards').innerHTML=[['最大 감속','max_deceleration_mps2','m/s²'],['최대 jerk','max_jerk_mps3','m/s³'],['이동 거리','distance_m','m']].map(([title,key,unit])=>'<div class="card"><div>'+title+'</div><div class="big">'+fmt(metric('sham',key).value)+' → '+fmt(metric('candidate',key).value)+'</div><span class="sub">'+unit+'</span></div>').join('');
E('metrics').innerHTML='<thead><tr><th>실행</th><th>최대 감속 ≤6</th><th>최대 jerk ≤10</th><th>접촉 관측</th><th>최소 TTC ≥1.5</th><th>종합</th></tr></thead><tbody>'+names.map(n=>'<tr><td>'+label[n]+'</td>'+['max_deceleration_mps2','max_jerk_mps3','collision_count','min_ttc_s'].map(k=>{let m=metric(n,k);return '<td class="'+(m.passed?'pass':'fail')+'">'+fmt(m.value)+' / '+(m.passed?'통과':'실패')+'</td>'}).join('')+'<td>'+D.trials[n].native.quality_verdict+'</td></tr>').join('')+'</tbody>';
E('counts').textContent='실제 완료 2실행 · 160명령 · 162관측 · 후보 초기 속도 차이 '+D.initial_speed_delta_mps+'m/s';
function chart(id,key){const s=E(id),W=1000,H=230,L=75,R=25,T=15,B=32;const zoom=E('window').value==='stop',x0=zoom?1.5:0,x1=zoom?2.6:4;let seq=names.map(n=>({name:n,rows:data(n).filter(r=>r.time_s>=x0&&r.time_s<=x1)}));let vals=seq.flatMap(t=>t.rows.map(r=>r[key])).filter(Number.isFinite);if(key==='jerk_mps3')vals.push(-10,10);let lo=Math.min(0,...vals),hi=Math.max(0,...vals);if(hi===lo)hi=lo+1;let pad=(hi-lo)*.08;lo-=pad;hi+=pad;const X=x=>L+(x-x0)/(x1-x0)*(W-L-R),Y=y=>H-B-(y-lo)/(hi-lo)*(H-T-B);let html='';for(let i=0;i<5;i++){let v=lo+(hi-lo)*i/4;html+='<line x1="'+L+'" x2="'+(W-R)+'" y1="'+Y(v)+'" y2="'+Y(v)+'" stroke="#dce4e9"/><text x="'+(L-9)+'" y="'+(Y(v)+4)+'" text-anchor="end" font-size="12">'+v.toFixed(2)+'</text>'}for(let i=0;i<5;i++){let x=x0+(x1-x0)*i/4;html+='<text x="'+X(x)+'" y="'+(H-6)+'" text-anchor="middle" font-size="12">'+x.toFixed(2)+'s</text>'}if(key==='jerk_mps3')for(const v of [-10,10])html+='<line x1="'+L+'" x2="'+(W-R)+'" y1="'+Y(v)+'" y2="'+Y(v)+'" stroke="#ac792b" stroke-dasharray="6 4"/>';seq.forEach((t,j)=>{let p='',prior=false;t.rows.forEach(r=>{if(!Number.isFinite(r[key])){prior=false;return}p+=(prior?'L':'M')+X(r.time_s)+','+Y(r[key])+' ';prior=true});html+='<path d="'+p+'" fill="none" stroke="'+(j?'#087f85':'#657683')+'" stroke-width="2"/>'});s.setAttribute('viewBox','0 0 '+W+' '+H);s.innerHTML=html}
function render(){chart('speed','speed_mps');chart('acceleration','acceleration_mps2');chart('jerk','jerk_mps3');chart('sticky','sticky_count')}E('window').onchange=render;render();
E('verification').textContent=JSON.stringify(Object.fromEntries(names.map(n=>{let t=D.trials[n];return [label[n],{modes:t.mode_counts,constraint_callbacks:t.intervention.solver_callbacks,forward_rows:t.intervention.forward_rows,rewritten_damping_fields:t.intervention.rewritten_damping_fields,outside_damping_changes:t.intervention.outside_damping_changes,cleanup:t.cleanup_verified,flag_transitions:t.native.flag_transitions}]})),null,2);
E('samples').innerHTML='<thead><tr><th>실행/#</th><th>t / frame</th><th>속도</th><th>가속도</th><th>jerk</th><th>brake 요청</th><th>sticky 수</th><th>sleep</th><th>정책</th></tr></thead><tbody>'+names.flatMap(n=>data(n).map(r=>'<tr><td>'+label[n]+' / '+r.index+'</td><td>'+r.time_s+' / '+r.world_frame+'</td><td>'+fmt(r.speed_mps)+'</td><td>'+fmt(r.acceleration_mps2)+'</td><td>'+fmt(r.jerk_mps3)+'</td><td>'+fmt(r.requested_brake)+'</td><td>'+r.sticky_count+'</td><td>'+r.native.rigid_sleeping+'</td><td>'+r.mode+'</td></tr>')).join('')+'</tbody>';
E('provenance').textContent=JSON.stringify({limitations:D.limitations,executions:Object.fromEntries(names.map(n=>[n,{execution_id:D.trials[n].native.execution_id,candidate_hash:D.trials[n].candidate_hash,source_manifest_sha256:D.trials[n].source_manifest_sha256,report_sha256:D.trials[n].report_sha256,intervention_sha256:D.trials[n].intervention.file_sha256}]))},null,2);
</script></html>'''


def render(source, output):
    raw = source.read_bytes(); data = json.loads(raw)
    if data.get('schema_version') != 'carla-private-sticky-damping-comparison/1':
        raise ValueError('Expected the verified A/B schema')
    output.mkdir(parents=True, exist_ok=False)
    (output/'analysis.json').write_bytes(raw)
    text = raw.decode().replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
    (output/'index.html').write_text(HTML.replace('__DATA__', text).replace('最大 감속','최대 감속'))
    with (output/'samples.csv').open('w', newline='') as stream:
        fields = ['trial','index','time_s','world_frame','speed_mps','acceleration_mps2','jerk_mps3','requested_brake','mode','sticky_count','sleep']
        writer=csv.DictWriter(stream, fieldnames=fields);writer.writeheader()
        for name, trial in data['trials'].items():
            for row in trial['native']['rows']:
                writer.writerow({'trial':name, **{k:row[k] for k in fields[1:-2]},
                    'sticky_count':sum(row['native']['sticky_forward_active']), 'sleep':row['native']['rigid_sleeping']})
    print(json.dumps({'report':str(output/'index.html'),'rows':sum(len(t['native']['rows']) for t in data['trials'].values())}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();render(a.source,a.output)
