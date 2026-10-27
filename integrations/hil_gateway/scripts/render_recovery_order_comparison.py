"""Both columns are faulted sides from different sealed runs, never normal/fault mixing."""
import hashlib,json,sqlite3,zipfile
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont

root=Path.cwd();out=root/'reports/hil-model-recovery-order-20261018';out.mkdir(parents=True,exist_ok=True)
folders=[root/'docs/validation/hil-model-overlap-20261018',root/'docs/validation/hil-model-recovery-order-20261018']
data=root/'e2e/.runtime/hil-bridge-20261017';db=sqlite3.connect('file:'+str(data/'platform.db')+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
subjects=[]
def read_artifact(job,name):
    r=db.execute('select * from experiment_artifacts where job_id=? and name=?',(job,name)).fetchone();assert r is not None
    path=data/'experiments/artifacts'/r['storage_key'];assert path.stat().st_size==r['size_bytes'] and hashlib.sha256(path.read_bytes()).hexdigest()==r['sha256']
    return path,dict(r)
for folder in folders:
    run=json.loads((folder/'result.json').read_text());assert run['summary']['verdict']=='passed'
    assert hashlib.sha256((folder/'export.json').read_bytes()).hexdigest()==run['manifest_hash']
    path,trace_meta=read_artifact(run['id'],'vecu-trace.json');rows=json.loads(path.read_text())['sides']['faulted']
    path,scene_meta=read_artifact(run['id'],'faulted-city-scene.json');scene=json.loads(path.read_text())
    archives={};hashes={}
    for name in {v['archive'] for v in scene['camera_records'] if v['sensor_id']=='chase' and 50<=v['index']<=102}:
        path,meta=read_artifact(run['id'],'faulted-'+name);archives[name]=zipfile.ZipFile(path);hashes[name]=meta['sha256']
    subjects.append(dict(run=run,rows=rows,scene=scene,archives=archives,provenance=dict(execution=run['id'],manifest_sha256=run['manifest_hash'],trace_sha256=trace_meta['sha256'],scene_sha256=scene_meta['sha256'],archives=hashes,members=[])))
font='/System/Library/Fonts/AppleSDGothicNeo.ttc';title=ImageFont.truetype(font,25);normal=ImageFont.truetype(font,18);small=ImageFont.truetype(font,16)
frames=[]
for step in range(50,103,2):
    canvas=Image.new('RGB',(960,500),'#101c2b');d=ImageDraw.Draw(canvas)
    d.text((12,7),'회복 순서 비교 · 두 영상 모두 복합 고장 조건',font=title,fill='white')
    d.text((12,43),f'동일 입력 {step*.05:.2f}s → CARLA 영상 {(step+1)*.05:.2f}s · 0.5배속',font=normal,fill='#cadbf0')
    for col,subject in enumerate(subjects):
        x=col*480;row=subject['rows'][step];m=row['reply']['bridge']['model_monitor']
        r=next(v for v in subject['scene']['camera_records'] if v['sensor_id']=='chase' and v['index']==step)
        assert abs(r['time_s']-(step+1)*.05)<1e-6
        raw=subject['archives'][r['archive']].read(r['member']);assert hashlib.sha256(raw).hexdigest()==r['sha256']
        img=Image.frombytes('RGBA',(r['width'],r['height']),raw,'raw','BGRA').convert('RGB');canvas.paste(img.resize((480,270),Image.Resampling.LANCZOS),(x,96))
        d.text((x+12,72),'A · 기존: 전원 먼저 회복' if col==0 else 'B · 이번: FPGA 먼저 회복',font=normal,fill='#c4d9f4')
        active=' + '.join({'power':'전원','fpga':'FPGA'}[n] for n in m['active_boundaries']) or '없음'
        d.text((x+12,369),f"Brake {row['reply']['control']['brake']*100:.0f}% · {row['next_ego']['speed_mps']*3.6:.2f}km/h · 보호: {active}",font=normal,fill='#ffbe86' if m['protect'] else '#7fdec3')
        for y,n,label in ((398,'power','전원'),(427,'fpga','FPGA')):
            v=m['members'][n];phase=v['sample']['phase'];state='감시 전' if phase=='before' else '이상 검출' if v['state']=='degraded' else '복귀 확인 중' if v['protect'] else '보호 해제'
            d.text((x+12,y),f"{label}: {state} · 정상 {v['fresh_samples']}회 · 잔여 {v['hold_remaining_ms']:.0f}ms",font=small,fill='#ffbe86' if v['protect'] else '#7fdec3')
        subject['provenance']['members'].append(dict(member=r['member'],sha256=r['sha256'],input_step=step,camera_time_s=r['time_s']))
    d.text((12,470),'남은 보호 원인은 바뀌어도, 하나라도 보호 중이면 제동 유지 · 최종 해제 뒤 재출발',font=small,fill='#bdcfe3')
    if step==74:canvas.save(out/'order-comparison-partial.png')
    frames.append(canvas.resize((768,400),Image.Resampling.LANCZOS).quantize(colors=128,dither=Image.Dither.NONE))
for s in subjects:
    for z in s['archives'].values():z.close()
gif=out/'recovery-order-ab.gif';frames[0].save(gif,save_all=True,append_images=frames[1:],duration=200,loop=0,optimize=True,disposal=2)
provenance=dict(schema_version='hil-recovery-order-video/1',comparison_axis='previous_faulted_vs_current_faulted',playback_rate=.5,sources=[s['provenance'] for s in subjects],gif=dict(path=gif.name,sha256=hashlib.sha256(gif.read_bytes()).hexdigest(),frames=len(frames)),browser_verified=False,physical_validation=False)
(out/'comparison-provenance.json').write_text(json.dumps(provenance,ensure_ascii=False,indent=2)+'\n');print(json.dumps(provenance['gif']))
