"""Create an annotated A/B clip from sealed local CARLA artifacts, without a browser."""
import argparse
import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont

parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=Path('docs/validation/hil-model-overlap-20261018'));parser.add_argument('--out',type=Path,default=Path('reports/hil-model-overlap-20261018'));parser.add_argument('--first-recovery',choices=['power','fpga'],default='power');parser.add_argument('--title',default='전원 강하 + FPGA 300ms 정체 · A/B');parser.add_argument('--filename',default='power-fpga-overlap-ab.gif');args=parser.parse_args()
root=Path.cwd();p=root/args.root
out=root/args.out;out.mkdir(parents=True,exist_ok=True)
last='fpga' if args.first_recovery=='power' else 'power';names={'power':'전원','fpga':'FPGA'}
data=root/'e2e/.runtime/hil-bridge-20261017'
db=sqlite3.connect('file:'+str(data/'platform.db')+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
run=json.loads((p/'result.json').read_text());checks=json.loads((p/'checks.json').read_text())
assert run['summary']['verdict']=='passed' and checks['execution']==run['id']
assert hashlib.sha256((p/'export.json').read_bytes()).hexdigest()==run['manifest_hash']
def artifact(name):
    r=db.execute('select * from experiment_artifacts where job_id=? and name=?',(run['id'],name)).fetchone()
    path=data/'experiments/artifacts'/r['storage_key']
    assert path.stat().st_size==r['size_bytes'] and hashlib.sha256(path.read_bytes()).hexdigest()==r['sha256']
    return path,dict(r)
path,meta=artifact('vecu-trace.json');trace=json.loads(path.read_text());sources=dict(trace_sha256=meta['sha256'],sides={})
scenes={};archives={}
for side in ('baseline','faulted'):
    path,meta=artifact(side+'-city-scene.json');scenes[side]=json.loads(path.read_text());archives[side]={}
    sources['sides'][side]=dict(scene_sha256=meta['sha256'],archives={},members=[])
    needed={r['archive'] for r in scenes[side]['camera_records'] if r['sensor_id']=='chase' and 50<=r['index']<=102}
    for name in needed:
        path,meta=artifact(side+'-'+name);archives[side][name]=zipfile.ZipFile(path);sources['sides'][side]['archives'][name]=meta['sha256']
font='/System/Library/Fonts/AppleSDGothicNeo.ttc';normal=ImageFont.truetype(font,18);small=ImageFont.truetype(font,16);title=ImageFont.truetype(font,27)
frames=[];partial_saved=False
for step in range(50,103,2):
    canvas=Image.new('RGB',(960,500),'#101c2b');d=ImageDraw.Draw(canvas)
    m=trace['sides']['faulted'][step]['reply']['bridge']['model_monitor']
    stage='두 모델 모두 정상' if step<60 else '두 고장 보호 중' if len(m['active_boundaries'])==2 else f'{names[args.first_recovery]} 회복 · {names[last]} 보호 남음 · 제동 유지' if m['active_boundaries']==[last] else '두 보호 조건 해제 · 차량 회복'
    d.text((14,8),args.title,font=title,fill='white')
    d.text((14,43),f'입력 {step*.05:.2f}s → 영상 {(step+1)*.05:.2f}s · 0.5배속 · {stage}',font=normal,fill='#c9ddf5')
    for col,side in enumerate(('baseline','faulted')):
        x=col*480;row=trace['sides'][side][step];mon=row['reply']['bridge']['model_monitor']
        record=next(r for r in scenes[side]['camera_records'] if r['sensor_id']=='chase' and r['index']==step)
        assert abs(record['time_s']-(step+1)*.05)<1e-6
        raw=archives[side][record['archive']].read(record['member']);assert hashlib.sha256(raw).hexdigest()==record['sha256']
        img=Image.frombytes('RGBA',(record['width'],record['height']),raw,'raw','BGRA').convert('RGB')
        canvas.paste(img.resize((480,270),Image.Resampling.LANCZOS),(x,96))
        color='#79dec1' if col==0 else '#ffbc83'
        d.text((x+12,73),'A · 정상 모델 2개' if col==0 else 'B · 전원 강하 + FPGA 정체',font=normal,fill=color)
        d.text((x+12,370),f"Brake {row['reply']['control']['brake']*100:.0f}% · {row['next_ego']['speed_mps']*3.6:.2f} km/h · {'제동 유지' if mon['protect'] else '모델 보호 없음'}",font=normal,fill=color)
        for offset,n,label in ((398,'power','전원'),(426,'fpga','FPGA')):
            v=mon['members'][n];s=v['sample']
            state='이상 검출' if v['state']=='degraded' else '복귀 확인 중' if v['protect'] else '감시 시작 전' if s['phase']=='before' else '보호 해제'
            value=f"{s['min_v']:.2f}–{s['max_v']:.2f}V" if n=='power' else f"abort {s['abort_count']} · {s['last_status']}"
            d.text((x+12,offset),f'{label}: {state} · {value}',font=small,fill='#ffbc83' if v['protect'] else '#79dec1')
        sources['sides'][side]['members'].append(dict(member=record['member'],sha256=record['sha256'],input_step=step,camera_time_s=record['time_s']))
    d.text((14,469),'제동 유지 = 전원 보호 OR FPGA 보호 · 최소 500ms 및 정상 3표본을 경계별 확인',font=small,fill='#b7c9df')
    if not partial_saved and m['active_boundaries']==[last]:
        canvas.save(out/'partial-recovery.png');partial_saved=True
    frames.append(canvas.resize((768,400),Image.Resampling.LANCZOS).quantize(colors=128,dither=Image.Dither.NONE))
assert partial_saved
for v in archives.values():
    for z in v.values():z.close()
gif=out/args.filename;frames[0].save(gif,save_all=True,append_images=frames[1:],duration=200,loop=0,optimize=True,disposal=2)
provenance=dict(schema_version='hil-overlap-video/1',execution=run['id'],manifest_sha256=run['manifest_hash'],
    playback_rate=.5,gif=dict(path=gif.name,sha256=hashlib.sha256(gif.read_bytes()).hexdigest(),frames=len(frames)),
    sources=sources,browser_verified=False,physical_validation=False)
(out/'provenance.json').write_text(json.dumps(provenance,ensure_ascii=False,indent=2)+'\n');print(json.dumps(provenance['gif']))
