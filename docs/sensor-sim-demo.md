# Sensor Sim · 발표자 화면 공유 데모

2026-10-10 후속: 주행 FoV를 포함한 Sensor 사례3개를 질문 중심 목록에서 열어 실제 원본 재생·
핵심 상대 시각·native readback·새로고침·목록 복귀를 정상 브라우저에서 확인했다. 이전 주행
브라우저 대기 항목은 완료다. 새 native 수집은 수행하지 않았다.
[사례 연결과 완료 범위](demo-case-library.md) · [확인 근거](validation/demo-case-library-20261010.json).

결정일: 2026-10-09. 사용자가 선택한 첫 범위는 **센서 구성 A/B + 발표자 화면 공유**다.
상세 물리 검증 확대보다 정상 UI/API/SDK에서 빠르게 실행하고 차이를 보여주는 기능을 우선한다.
내부 통합플랫폼의 다른 업무와 연구 결과는 그대로 유지한다.

정지 A/B 상태: **구현 및 로컬 발표 리허설 완료**. [기존 실행·브라우저 근거](validation/sensor-sim-demo-20261009.json).
주행 확장: **구현·실제 CARLA 수집·원본 parser 확인 완료**, 브라우저 최종 확인은 Mac 잠금으로 대기 중.
[주행 실행 근거](validation/sensor-sim-driving-20261009.json).

## 기존 정지 시연 범위

목표 흐름은 **프리셋 선택 → B 설정 변경 → 차이 확인 → native A/B 실행 → 원본 비교**다.
발표는 명시적으로 연결한 저장 결과로 바로 시작한다. 새 실행이 대기·진행 중이어도 저장 결과를
볼 수 있고, 완료된 새 결과는 별도로 연다. 실행 ID와 설정은 결과에 고정된다.

| 프리셋 | A 기준 | B 기본값 | 편집 범위 |
| --- | --- | --- | --- |
| 카메라 시야각 | 전방 90° | 전방 70° | 50–110°, 1° 단위 |
| LiDAR 밀도 | 32,000 points/s | 8,000 points/s | 8,000–32,000, 1,000 단위 |
| Radar 범위 | 80 m | 30 m | 10–100 m, 1 m 단위 |

A와 같은 값은 비교 실행 전에 거절한다. 하나의 프리셋에서 하나의 센서 속성만 바꾼다.
장면은 Town10HD_Opt, spawn 0, Tesla Model3 정지 상태, 10초·10 Hz다.
센서는 640×360 전/후방 RGB, 16-channel LiDAR, radar의 고정 구성이다.
한 job에서 A와 B를 순차로 새로 캡처하므로 오래된 A와 새 B를 하나의 실행처럼 묶지 않는다.

## 주행 확장 · RGB + LiDAR + Radar

`driving_camera_fov` 프리셋은 Town10HD_Opt spawn 0에서 지도 기반 참조 제어기로 20초를
주행하며 전후방 RGB, 16-channel LiDAR, radar를 같은 native frame·timestamp로 수집한다.
목표 속도는3m/s, 시야각은 A90°→B70°이며 기존 세 정지 프리셋도 유지한다.
실행 결과에는 시점별 자차 pose·속도·누적 이동 거리와 제어 요청/적용 응답이 함께 남는다.
공통 timeline에서 RGB와 거리센서를 재생하고 각 A/B의 현재 속도·거리를 볼 수 있다.

새 optional adapter는 `carla-driving-multisensor/1`, 입력은 `carla-driving-multisensor-spec/1`,
원본 manifest는 `carla-driving-multisensor-result/1`이다. 기존 정지 수집기와 그 source hash는
바꾸지 않는다. 일반 Experiment API로는20–30초 single 또는 동일 장면 A/B를 지원하며,
빠른 시작 프리셋은20초·4센서로 고정한다. native dependency hash와 공유 engine lease를
확인하고10개의 제동 정착 프레임 이후 수집한다. 센서 pose는 매 응답 프레임의 자차 pose와
대조하며, 주행/센서 frame 누락이나 외부 actor/설정 변경은 실패 이유로 보존한다.

A/B는 같은 지도·경로·제어기 설정을 사용한 **순차 재실행**이다. 센서들은 각 주행 안에서
동시에 수집되지만 A와 B의 궤적이 수치적으로 같다고 보장하지 않는다. 센서가 제어기의
입력이 되는 학습형 폐루프는 아니며 실제 센서 물리 정확도도 평가하지 않는다.

주행 준비·리허설은 같은 공개 API script에 `prepare --driving`을 사용한다. 기존 준비 receipt와
구분되는 파일을 지정한다. 네이티브 연결은 `AXIOM_CARLA_DRIVING_SSH`, `_PYTHON`, `_SCRIPT`,
`_DIR`, `_ENGINE_SPOOL`을 명시하며 `_ENGINE_SPOOL`은 다른 CARLA 실행과 공유하는 기존 spool이다.

## 제품 연결

- 프로젝트별 프리셋과 대표 실행을 versioned catalog에 명시적으로 연결한다. 최신 job을 임의 선택하지 않는다.
- 프리셋 preview는 고정 source와 변경 값을 기존 Experiment 요청 및 native preflight로 변환한다.
- 생성·제출·진행 조회·실패 표시는 기존 Experiment API와 durable worker를 사용한다.
- 발표 화면은 전방 RGB A/B를 크게 배치하고, LiDAR/radar 탭과 상대 시간 cursor를 공유한다.
- 0.5/1/2배 재생, 시작·중간·끝 이동, 마지막 frame 정지, 전체 화면을 제공한다.
- 후방 RGB와 native clock·manifest·source identity는 펼쳐 보는 상세 영역으로 둔다.
- 다음 원본이 로딩 중일 때 검증된 직전 frame을 유지하고 로딩 안내 공간을 고정한다.
- 기존 고급 작성·결과 화면과 deep link는 계속 사용할 수 있다.

순차 A/B의 상대 시간 비교이며, 물리 정확도나 실제 센서의 우열을 측정하는 시험이 아니다.
반환점 수는 실제 native 출력의 관측값으로만 표시한다. 불완전한 원본, 지원하지 않는 값,
다른 프로젝트의 실행, 오래된 binding revision은 조용히 대체하지 않고 오류를 표시한다.

## API / SDK

| 경로 | 용도 |
| --- | --- |
| `GET /api/v1/sensor-sim/presets?project_id=…` | 고정 프리셋·편집 범위·대표 실행 목록 |
| `POST /api/v1/sensor-sim/presets/{id}/bindings` | baseline 및 선택한 대표 실행 연결, 낙관적 revision 검사 |
| `POST /api/v1/sensor-sim/presets/{id}/preview` | binding revision과 B 값을 확인하고 기존 Experiment 요청 생성 |
| 기존 `/api/v1/experiments` 경로 | preview hash에 고정된 생성·실행·결과/원본 조회 |

Python SDK의 `sensor_sim_presets`, `bind_sensor_sim_preset`, `preview_sensor_sim_preset`,
`execute_sensor_sim_preview`도 같은 경로를 사용한다. 별도 DB migration은 없다.
새 프로젝트의 최초 binding과 대표 native 결과 준비는 `scripts/sensor_sim_demo.py`로 수행할 수 있다.
이 script는 내부 우회 저장이나 직접 simulator 호출 없이 위 공개 API를 사용한다.

```sh
.venv/bin/python -B scripts/sensor_sim_demo.py --api http://127.0.0.1:8034/api/v1 --token-file <private-token-file> prepare --output <runtime>/prepared.json
.venv/bin/python -B scripts/sensor_sim_demo.py --api http://127.0.0.1:8034/api/v1 --token-file <private-token-file> rehearse --prepared <runtime>/prepared.json --output <runtime>/rehearsal.json
```

Rehearsal은 프리셋당 한 job, 총 세 job/여섯 capture를 순차 실행하고 성공한 실행을 대표 결과로
연결한다. 같은 receipt로 재개하면 저장된 job을 조회하며 실패한 job을 묵시적으로 교체하지 않는다.
로컬 발표자 환경은 기존 `scripts/internal_platform.py` launcher의 분리된 workspace를 사용한다.
외부 공개 서비스 배포나 익명 접근 제공은 이 범위에 포함하지 않는다.

## 최소 확인과 후속

완료 조건은 관련 backend/adapter/SDK 검사, production build, 세 프리셋의 실제 native A/B,
정상 브라우저에서 편집→preview→제출→비교→새로고침 재진입, 1080p 발표 화면 확인이다.
최신 수행 결과와 제한은 `docs/status.md` 및 validation receipt에 기록한다.
변경·실패·미해결 우려가 없는 광범위 반복 시험은 확대하지 않는다.

다음 단계는 depth/segmentation, 선택 frame의 모델 prediction,
지원 날씨/조명, Case 연결이다. 정밀 광학→RAW, 실측 상관성, rolling shutter/HDR 물리 검증,
radar IQ/ADC, HIL, 임의 장면 재구성, 학습형 센서 폐루프와 상용 release gates는 별도 후속이다.

## 기존 정지 결과와 3분 시연 순서

1. 데모 프로젝트의 카메라 시야각 카드를 열고 전체 화면에서90→70° 관측 영역 차이를 설명한다.
2. LiDAR 밀도 카드에서32,000→8,000points/s, 첫 시점 반환점1,645→400을 확인한다.
3. Radar 범위 카드에서80→30m를 확인하고 **중간**으로 이동해 반환점73→15를 비교한다.
4. 전체 화면에서 나와 B값을 변경하고 변경 확인→A/B 실행을 누른다. 저장 결과를 재생하며
   진행 상태를 확인하고 **새 A/B 결과 열기**로 새 결과를 명시적으로 연다.

세 기본 프리셋과 UI에서 작성한FoV80° 실행, 총4작업/8캡처/3,200센서 관측을 완료했다.
Safari의1920×1080전체 화면, 동기 재생·후방 보기·2배속·시점 이동·마지막 정지와 새로고침
복원을 확인했다. 실패했던 미리보기 숫자 직렬화와 저장 job 조회를 고쳤고 기존 원본 hash를 보존했다.

**알려진 native 제한:** radar 첫 표본의 속도에564–686m/s 이상치가 있다. 원본값과 Viewer경고를
유지한다. 확인한 후속index1/49/99는0m/s다. 이번 시연은 범위와 반환점 분포에 한정하며
실측 속도 정확도로 설명하지 않는다. 이상치를 삭제하거나 정상값으로 대체하지 않는다.

## 주행 확장 수행 결과 · 2026-10-09

공개 API에서 `driving_camera_fov` preview→Experiment 생성→큐 실행→대표 결과 연결을 수행했다.
`job_0441911a8a6445cf9c33f6605f6ef1a4`의 A/B 각각200시점×4센서, 총1,600개 관측을
보존했다. 각 주행의 이동거리56.854688m, 최고속도3.002704m/s, drop0, cleanup confirmed다.
전방 FoV90→70°와 시작/마지막 native RGB에서 실제 장면 변화도 확인했다.

현재 프런트엔드 parser로 공개 API의 sealed receipt와 양쪽 manifest를 다시 읽어 입력·rig hash,
frame·clock·ego pose·motion 연결을 확인했다. 중간index99는27.360m 이동, 속도2.956m/s,
LiDAR1,558점/radar73점이다. 새 주행의 확인한 radar0/99/199표본은 첫표본속도약0m/s,
이후약-3m/s이며 원본 SHA를 확인했다. 기존 정지 첫표본 이상치를 고쳤다는 주장은 아니다.

검사: backend/SDK44, 새 adapter22, native mocked19, 기존 capture27(+13subtests),
frontend39 및 production build 통과. **Mac이 잠겨 새 주행 화면의 실제 브라우저 조작·전체 화면
배치는 아직 확인하지 못했다.** 컴퓨터 제어 도구가 잠금 해제를 요구했고 사용자에게 해제를
요청했다. 원본 parser 확인을 정상 브라우저 workflow 확인으로 대신 계산하지 않는다.
