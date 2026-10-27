"""Decoded CAN and reference-process state from the two actual sealed executions."""
import hashlib,json,struct
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
root=Path('docs/validation/hil-pending-power-20261018');old=Path('docs/validation/hil-pending-fpga-20261018/vecu-trace.json');new=root/'vecu-trace.json'
out=Path('reports/hil-pending-power-20261018');out.mkdir(exist_ok=True)
font='/System/Library/Fonts/AppleSDGothicNeo.ttc';title=ImageFont.truetype(font,26);label=ImageFont.truetype(font,19);small=ImageFont.truetype(font,16)
canvas=Image.new('RGB',(1080,680),'#101c2b');d=ImageDraw.Draw(canvas)
d.text((22,12),'5.85초 전원 재발 · CAN 제동과 전체 보호 A/B',font=title,fill='white')
d.text((22,53),'A: FPGA 5.25초 재발까지 / B: 전원6V·300ms 추가 · 실제 vCAN 관측',font=label,fill='#c4d9f4')
def wave(points,y,color,text):
 start,end=5700,7100;x0,x1=235,1040;scale=lambda t:x0+(t-start)/(end-start)*(x1-x0)
 d.text((22,y+10),text,font=label,fill=color)
 d.text((182,y-3),'100%',font=small,fill='#c4d9f4');d.text((194,y+49),'0%',font=small,fill='#c4d9f4')
 for t in (5700,5900,6100,6300,6500,6800,7100):
  x=scale(t);d.line((x,y,x,y+60),fill='#32465d');d.text((x-22,y+63),f'{t/1000:.2f}',font=small,fill='#a5b7cb')
 d.line((x0,y+55,x1,y+55),fill='#597086')
 prior=next((v for t,v in reversed(points) if t<=start),points[0][1]);px=x0;py=y+55-prior*50
 for t,v in points:
  if t<=start:continue
  if t>end:break
  x=scale(t);ny=y+55-v*50;d.line((px,py,x,py,x,ny),fill=color,width=3);px,py=x,ny
 d.line((px,py,x1,py),fill=color,width=3)
def events(path,kind):
 rows=json.loads(path.read_text())['sides']['faulted']
 return [(e['time_ms'],struct.unpack('<HHhBB',bytes.fromhex(e['data_hex']))[1]/10000) for r in rows for e in r['reply']['bridge']['events'] if e['kind']==kind and (kind!='can_tx' or e.get('ecu')=='adas')]
wave(events(old,'can_tx'),100,'#f29979','A · CAN 0x100')
wave(events(new,'can_tx'),210,'#7fdec3','B · CAN 0x100')
wave(events(old,'brake_output_observed'),320,'#f29979','A · Brake 0x101')
wave(events(new,'brake_output_observed'),430,'#7fdec3','B · Brake 0x101')
checks=json.loads((root/'checks.json').read_text())
d.text((22,555),f"B: 입력5.85s 후보 대기 → 입력5.90s 전원 이상 / 후보 재취소 관측 → 제동100% 유지",font=small,fill='#ffd79b')
d.text((22,584),f"B: 전체 보호 해제 {checks['group_recovery_ms']/1000:.6f}s · CARLA 재이동 {checks['motion_carla_ms']/1000:.2f}s",font=label,fill='#7fdec3')
d.text((22,624),'실행별 Linux monotonic 경과 시각(초) · 원본 CAN bytes 해석 · 차량 영상 시간은 별도',font=small,fill='#a5b7cb')
d.text((22,650),'참조 ECU 프로세스·가상 CAN·CARLA 검증이며 실물 ECU/안전 인증 결과가 아님',font=small,fill='#a5b7cb')
path=out/'pending-power-can.png';canvas.save(path)
metadata=dict(schema_version='hil-pending-power-waveform/1',artifact=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),native_sources={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (old,new)},physical_validation=False,browser_workflow_verified=False)
(out/'waveform-provenance.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n');print(str(path))
