"""Render a model validation matrix with separate metric and behavior verdicts."""
from pathlib import Path
import argparse
import csv
import json

HTML = r'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AXIOM · 차량 모델 검증 행렬</title><style>
*{box-sizing:border-box}body{margin:0;background:#f1f4f7;color:#1c3042;font:15px/1.6 system-ui,sans-serif}main{max-width:1260px;margin:auto;padding:28px}header{background:#192f44;color:white;padding:25px;border-radius:12px}h1{font-size:28px;margin:0}h2{font-size:21px}section{background:white;border:1px solid #dce4ea;border-radius:12px;padding:20px;margin:20px 0;min-width:0}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.card{background:#edf3f6;border-radius:8px;padding:16px}.big{font-size:29px;font-weight:700}.notice{border-left:4px solid #ab781f;padding:12px;background:#fff4da}.scroll{overflow:auto;max-height:460px}table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:10px;border-bottom:1px solid #dfe6ec;text-align:left}th{background:#edf3f6;position:sticky;top:0}.passed{color:#126449}.failed{color:#a33528}pre{font:12px/1.6 ui-monospace,monospace;white-space:pre-wrap}p,pre,td,code{overflow-wrap:anywhere}select{max-width:100%;font:inherit;padding:8px}svg{display:block;width:100%;height:230px}.muted{color:#627484}a{color:#126a91}@media(max-width:640px){main{padding:12px}header,section{padding:14px}h1{font-size:22px}.cards{grid-template-columns:1fr}.big{font-size:24px}}</style>
<main><header><h1>차량 모델 · 추가 조건 검증</h1><p>고정된 모델 revision × 사전 선언한 3조건 × 기준·후보</p></header>
<p class="notice">가상 차량 모델의 독립 엔진 검증입니다. 실차 보정이나 운영 배포가 아닙니다. 기존 지표 통과와 작업 수행 성공을 분리하며, 경사 시험은 미구현 상태로 남깁니다.</p>
<section><h2>후보 인수 현황</h2><div class="cards" id="cards"></div><p id="denominator"></p><div class="scroll"><table id="results"></table></div></section>
<section><h2>조건별 전체 응답</h2><label>조건 <select id="case"></select></label> <label>시간창 <select id="window"><option value="all">전체 0–4초</option><option value="stop">1.4–2.7초</option></select></label>
<p class="muted">회색: 기존 감쇠1000 · 청록: 후보 감쇠4. 확대는 그래프만 바꾸며 모든 표본과 판정은 유지됩니다.</p>
<h3>실제 속도 m/s</h3><svg id="speed"></svg><h3>원본 차분 가속도 m/s²</h3><svg id="acceleration"></svg><h3>Jerk m/s³ · 기존 ±10 기준</h3><svg id="jerk"></svg><h3>Brake 요청</h3><svg id="brake"></svg>
<h3>정책 전환 및 작업 수행 판정</h3><pre id="behavior"></pre></section>
<section><h2>선택한 조건의 전체 원본 관측</h2><div class="scroll"><table id="samples"></table></div></section>
<section><h2>모델 식별과 근거</h2><p>모델 ID·revision·실행파일·native 감쇠·기반 소스·제어기·실행 코드 hash를 구분합니다. 모델이 달라진 결과를 일반 0.8 실행으로 합치지 않습니다. 1.5/2.5는 초기 요청 속도이며 정책의 cruise 목표2m/s는 그대로입니다. 실제 첫 관측 속도는 표에 별도로 표시합니다.</p><pre id="provenance"></pre><p id="unsupported"></p><a href="analysis.json">검증 JSON</a> · <a href="samples.csv">전체 관측 CSV</a></section></main>
<script id="payload" type="application/json">__DATA__</script><script>
'use strict';const D=JSON.parse(document.getElementById('payload').textContent),E=id=>document.getElementById(id),fmt=x=>x===null||x===undefined?'—':typeof x==='number'?x.toFixed(5):String(x),esc=x=>String(x).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const label={sham:'기준 1000',candidate:'후보 4'},met=(r,n)=>r.metrics?.find(m=>m.name===n)?.value;
E('cards').innerHTML=[['기존 지표 통과',D.candidate_metric_passes],['작업 수행 통과',D.candidate_behavior_passes],['두 판정 모두 통과',D.candidate_accepted_cases]].map(([s,n])=>'<div class="card"><div>'+s+'</div><div class="big">'+n+' / '+D.candidate_denominator+'</div></div>').join('');
E('denominator').textContent='사전 계획 '+D.planned_executions+'실행 · 원본 검증 '+D.verified_executions+'실행 · '+D.commands+'명령 · '+D.observations+'관측 · 묶음 판정 '+D.suite_acceptance;
E('results').innerHTML='<thead><tr><th>조건 / 모델</th><th>첫 속도</th><th>감속 ≤6</th><th>Jerk ≤10</th><th>거리 m</th><th>기존 지표</th><th>작업 수행</th><th>추가 인수</th></tr></thead><tbody>'+D.cases.map(r=>'<tr><td>'+esc(r.case_id)+' / '+label[r.label]+'</td><td>'+fmt(r.initial_speed_mps)+'</td><td>'+fmt(met(r,'max_deceleration_mps2'))+'</td><td>'+fmt(met(r,'max_jerk_mps3'))+'</td><td>'+fmt(met(r,'distance_m'))+'</td>'+['metric_verdict','behavior_verdict','acceptance_verdict'].map(k=>'<td class="'+r[k]+'">'+esc(r[k])+'</td>').join('')+'</tr>').join('')+'</tbody>';
const ids=[...new Set(D.cases.map(c=>c.case_id))];E('case').innerHTML=ids.map(id=>'<option>'+esc(id)+'</option>').join('');
function selected(){return ['sham','candidate'].map(n=>({name:n,key:E('case').value+'-'+n,rows:D.details[E('case').value+'-'+n]?.native.rows||[]}))}
function chart(id,key){let W=1000,H=230,L=75,R=24,T=15,B=32,zoom=E('window').value==='stop',x0=zoom?1.4:0,x1=zoom?2.7:4;const seq=selected().map(s=>({...s,rows:s.rows.filter(r=>r.time_s>=x0&&r.time_s<=x1)}));let vals=seq.flatMap(s=>s.rows.map(r=>r[key])).filter(Number.isFinite);if(key==='jerk_mps3')vals.push(-10,10);let lo=Math.min(0,...vals),hi=Math.max(0,...vals);if(lo===hi)hi=lo+1;let pad=(hi-lo)*.08;lo-=pad;hi+=pad;const X=x=>L+(x-x0)/(x1-x0)*(W-L-R),Y=y=>H-B-(y-lo)/(hi-lo)*(H-T-B);let h='';for(let i=0;i<5;i++){let y=lo+(hi-lo)*i/4;h+='<line x1="'+L+'" x2="'+(W-R)+'" y1="'+Y(y)+'" y2="'+Y(y)+'" stroke="#dce4ea"/><text x="'+(L-8)+'" y="'+(Y(y)+4)+'" text-anchor="end" font-size="12">'+y.toFixed(2)+'</text>';let x=x0+(x1-x0)*i/4;h+='<text x="'+X(x)+'" y="'+(H-5)+'" text-anchor="middle" font-size="12">'+x.toFixed(2)+'s</text>'}if(key==='jerk_mps3')for(const y of [-10,10])h+='<line x1="'+L+'" x2="'+(W-R)+'" y1="'+Y(y)+'" y2="'+Y(y)+'" stroke="#b28334" stroke-dasharray="6 4"/>';seq.forEach((s,j)=>{let path='',prior=false;s.rows.forEach(r=>{if(!Number.isFinite(r[key])){prior=false;return}path+=(prior?'L':'M')+X(r.time_s)+','+Y(r[key])+' ';prior=true});h+='<path fill="none" stroke="'+(j?'#087f85':'#657683')+'" stroke-width="2" d="'+path+'"/>'});E(id).setAttribute('viewBox','0 0 '+W+' '+H);E(id).innerHTML=h}
function render(){chart('speed','speed_mps');chart('acceleration','acceleration_mps2');chart('jerk','jerk_mps3');chart('brake','requested_brake');E('samples').innerHTML='<thead><tr><th>모델/#</th><th>t</th><th>속도</th><th>가속도</th><th>jerk</th><th>brake</th><th>정책</th></tr></thead><tbody>'+selected().flatMap(s=>s.rows.map(r=>'<tr><td>'+label[s.name]+' / '+r.index+'</td><td>'+r.time_s+'</td><td>'+fmt(r.speed_mps)+'</td><td>'+fmt(r.acceleration_mps2)+'</td><td>'+fmt(r.jerk_mps3)+'</td><td>'+fmt(r.requested_brake)+'</td><td>'+esc(r.mode)+'</td></tr>')).join('')+'</tbody>';E('behavior').textContent=JSON.stringify(selected().map(s=>{let c=D.cases.find(c=>c.key===s.key),last=null,events=[];s.rows.forEach(r=>{if(r.mode!==last){events.push({time_s:r.time_s,mode:r.mode,reasons:r.decision_reasons});last=r.mode}});return {model:label[s.name],checks:c.behavior,modes:c.mode_counts,transitions:events}}),null,2);E('provenance').textContent=JSON.stringify(D.cases.filter(c=>c.case_id===E('case').value).map(c=>({model_id:c.model_id,revision:1,model_sha256:c.model_sha256,execution_id:c.execution_id,model_execution_hash:c.model_execution_hash,source_manifest_sha256:c.source_manifest_sha256,status:c.status})),null,2)}
E('case').onchange=render;E('window').onchange=render;render();E('unsupported').textContent='경사 조건: 미구현·미실행. '+D.scope;
</script></html>'''


def render(source, output):
    raw = Path(source).read_bytes(); data = json.loads(raw)
    if data.get('schema_version') != 'carla-private-model-validation-analysis/1':
        raise ValueError('Expected the verified model-suite analysis')
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    (output / 'analysis.json').write_bytes(raw)
    embedded = raw.decode().replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
    (output / 'index.html').write_text(HTML.replace('__DATA__', embedded))
    with (output / 'samples.csv').open('w', newline='') as f:
        fields = ['case_model', 'index', 'time_s', 'world_frame', 'speed_mps',
                  'acceleration_mps2', 'jerk_mps3', 'requested_brake', 'mode']
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader()
        for key, detail in data['details'].items():
            for row in detail['native']['rows']:
                writer.writerow({'case_model': key, **{name: row[name] for name in fields[1:]}})
    print(json.dumps({'report': str(output / 'index.html'), 'cases': len(data['cases']), 'observations': data['observations']}))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path); p.add_argument('output', type=Path)
    a = p.parse_args(); render(a.source, a.output)
