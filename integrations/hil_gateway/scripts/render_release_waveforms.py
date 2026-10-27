"""Digital timing diagram from sealed unit states and actual CAN payloads, kept distinct."""
import hashlib,json,struct
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
root=Path('docs/validation/hil-release-qualification-20261018');out=Path('reports/hil-release-qualification-20261018');out.mkdir(exist_ok=True)
font='/System/Library/Fonts/AppleSDGothicNeo.ttc';title=ImageFont.truetype(font,26);label=ImageFont.truetype(font,19);small=ImageFont.truetype(font,16)
canvas=Image.new('RGB',(1080,670),'#101c2b');d=ImageDraw.Draw(canvas)
old=Path('docs/validation/hil-power-recurrence-20261018/vecu-trace.json');new=root/'4350/vecu-trace.json';matrix=json.loads((root/'phase-matrix.json').read_text())
d.text((22,12),'50ms 경계 · 보호 판정 재현과 실제 CAN 출력 비교',font=title,fill='white')
d.text((22,55),'① 단위 재현: 보존된 기존 코드 A / 개선 코드 B · 실제 CAN 주행 결과와 구분',font=label,fill='#c4d9f4')
def wave(points,start,end,y,color,text,ticks):
 x0=235;x1=1040;scale=lambda t:x0+(t-start)/(end-start)*(x1-x0)
 d.text((22,y+10),text,font=label,fill=color)
 d.text((182,y-3),'100%',font=small,fill='#c4d9f4');d.text((194,y+49),'0%',font=small,fill='#c4d9f4')
 for t in ticks:
  x=scale(t);d.line((x,y,x,y+60),fill='#32465d');d.text((x-22,y+63),f'{t/1000:.2f}',font=small,fill='#a5b7cb')
 d.line((x0,y+55,x1,y+55),fill='#597086')
 prior=next((v for t,v in reversed(points) if t<=start),points[0][1]);px=x0;py=y+55-prior*50
 for t,v in points:
  if t<=start:continue
  if t>end:break
  x=scale(t);ny=y+55-v*50;d.line((px,py,x,py,x,ny),fill=color,width=3);px,py=x,ny
 d.line((px,py,x1,py),fill=color,width=3)
rep=matrix['representative']
wave([(r['input_ms'],int(r['protect'])) for r in rep['legacy']],4300,4550,95,'#f29979','A · 보호 판정',[4300,4350,4400,4450,4500,4550])
wave([(r['input_ms'],int(r['protect'])) for r in rep['qualified']],4300,4550,205,'#7fdec3','B · 보호 판정',[4300,4350,4400,4450,4500,4550])
d.text((235,298),'A: 4.35–4.40초 50ms 공백 재현 / B: 해제 검증 대기 중 새 전원 이상 → 제동 유지',font=small,fill='#ffd79b')
d.text((22,337),'② 실제 vCAN: 동일 4.35초 주입 · 두 주행 모두 공백 없이 최종 해제 시점이 달라짐',font=label,fill='#c4d9f4')
def can(path):
 rows=json.loads(path.read_text())['sides']['faulted'];events=[e for r in rows for e in r['reply']['bridge']['events'] if e['kind']=='can_tx' and e.get('ecu')=='adas']
 return [(e['time_ms'],struct.unpack('<HHhBB',bytes.fromhex(e['data_hex']))[1]/10000) for e in events]
wave(can(old),5100,5500,380,'#f29979','A · 기존 CAN',[5100,5200,5300,5400,5500])
wave(can(new),5100,5500,490,'#7fdec3','B · 개선 CAN',[5100,5200,5300,5400,5500])
d.text((235,584),'실행별 Linux monotonic 경과 시각(초) · 원본 CAN payload를 직접 해석 · 물리 ECU 아님',font=small,fill='#a5b7cb')
d.text((22,626),f"단위 검증 500조합: 기존 공백 {matrix['legacy_cases_with_gap']} / 개선 공백 0 · 임의 미래 고장에 대한 무지연 보장 아님",font=small,fill='#a5b7cb')
path=out/'release-can-waveforms.png';canvas.save(path)
metadata=dict(schema_version='hil-release-waveforms/1',artifact=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
 unit_matrix_sha256=hashlib.sha256((root/'phase-matrix.json').read_bytes()).hexdigest(),native_sources={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (old,new)},
 unit_is_native_can=False,physical_validation=False,browser_workflow_verified=False)
(out/'waveform-provenance.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n');print(str(path))
