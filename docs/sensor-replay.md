# Sensor replay · 로컬 사용법

같은 커서에서 MCAP 이미지와 LiDAR 관측을 조사하고, 사용자가 선언한 센서 장착 위치·시각 정책을 불변 개정으로 보존한다. 이 기능은 데이터 조사 도구다. 입력 기록의 물리적 정확도, 실제 취득 clock 동기화, 센서 calibration 또는 인지 모델 성능을 검증하지 않는다.

## 바로 열어 보기

`Start AXIOM.command`로 API·worker·UI를 실행하고 **Data catalog → Recordings → Import MCAP**를 연다. 원본 파일과 출처 종류를 지정하여 등록한 뒤 **Sensor replay** 탭을 선택한다. API가 실제 schema/encoding에 맞는 decoder를 찾은 채널만 Camera/LiDAR 선택기에 표시된다. 지원되지 않는 채널은 Raw messages에서 원본 bounded hex로 조사한다.

직접 작성한 합성 장면으로 전체 흐름을 재현하려면 저장소 루트에서 실행한다. 출력 폴더는 새 경로여야 한다.

```bash
.venv/bin/python examples/sensor_workflow.py --output .runtime/my-sensor-workflow
```

이 예제는 독립 ROS 2 작성기로 MCAP를 생성한 뒤 실제 HTTP API를 사용한다. RGB 640×360, 프레임당 LiDAR 18,164점, 별도 PNG 채널의 90개 메시지를 등록한다. 카메라와 LiDAR의 선언된 log offset은 각각 −3 ms/−5 ms다. 이는 예제 생성 규칙이며 실제 장비에서 측정한 값이 아니다. 장면에는 이미지 라벨로 `SYNTHETIC SENSOR REPLAY`를 표시한다. 점군은 해석적인 표면 표본이며 가림·광학·ray-cast 센서 물리의 결과가 아니다.

예제는 rig 두 개정과 binding 두 개정을 만든다. 두 번째 rig의 LiDAR X는 2 m다. 이전 binding은 원래 rig 개정과 native point bytes를 유지한다. `summary.json`, PNG, XYZ float32 LE, rig/binding JSON과 원본 MCAP가 출력 폴더에 저장된다.

## 커서와 원본 프레임

- 상단 timeline과 두 센서 패널은 하나의 정밀 ns 커서를 공유한다. API의 큰 시각값은 십진 문자열로 보존한다.
- 각 채널은 `log time + 선언된 offset ≤ cursor`인 마지막 관측을 선택한다. 미래 관측으로 빈 화면을 채우지 않는다.
- 최대 age를 초과하면 **Stale · frame withheld**로 표시하고 이미지·점군을 숨긴다. age 정책이 없으면 freshness는 unknown이다.
- **Next sample time**은 다음 정렬된 관측 시각으로 이동한다. **Frame steps**는 최대 초당 4회 커서를 전진하며 실시간 동영상 재생을 의미하지 않는다.
- **Inspect original sample**은 커서와 별도로 정확한 파일 ordinal을 조사한다. 원래 log/publish/capture time·sequence·payload SHA·hex와 원본 payload 다운로드를 제공한다. 명시적 원본 조사는 현재 프레임이라는 판정이 아니다.

카메라는 실제 payload를 해석하여 만든 PNG다. LiDAR는 실제 XYZ float32 bytes를 Three.js에 전달하며 intensity/height 색상과 point 검사, 표시 점 수 제한을 제공한다. 표시된 점 수·제외된 비정상 값·decimation stride를 함께 보존한다. 브라우저에서도 bytes 크기와 SHA-256을 확인한다.

## 센서 설정과 기록 연결

**Create sensor rig**에서 reference frame, 좌표 관례, 출처와 센서별 frame ID를 선언한다. 모르는 transform은 체크하지 않아 unknown으로 남긴다. 선언할 때만 translation(m)과 단위 quaternion `[x,y,z,w]`를 입력한다. camera intrinsics JSON도 선언으로 저장하며 자동 calibration/rectification은 하지 않는다.

**Bind recording**은 rig의 정확한 개정, 채널→센서, decoder ID/version, 최대 age(ns), log-time offset(ns)을 고정한다. age를 비우면 unknown이고 0은 정확히 같은 시각만 허용한다. 저장할 때 새 binding 개정을 만든다. 편집 중 다른 사용자가 개정하면 충돌을 반환한다.

재생은 **Unbound · native channels**로 시작한다. 사용자가 선택하거나 방금 저장한 binding만 적용한다. 새 rig/binding이 추가되거나 Refresh를 눌러도 현재 선택을 자동으로 최신 개정으로 바꾸지 않는다. Raw messages로 전환하거나 기록을 다시 열면 명시적으로 binding을 다시 선택한다.

**Native frame**은 원래 센서 frame의 좌표를 보여준다. **Declared rig frame**은 frame ID·kind·좌표 관례가 맞고 유한한 transform이 있을 때만 활성화한다. 광학 camera frame에 LiDAR를 투영하거나 자차 이동을 보상하는 기능은 아직 없다. intrinsics·transform을 입력한 사실이 물리 calibration의 증거가 되지 않는다.

## 실행에서 생성된 기록

MCAP를 포함한 완료 실행은 Data catalog의 해당 artifact에서 **Open as recording**을 선택한다. API는 원본 실행과 artifact bytes의 무결성을 확인하고 등록한다. 기록에는 별도 origin 관계로 run/scenario revision·seed·adapter version·fidelity·입출력 hash를 보존한다. 다시 요청하면 같은 기록으로 연결한다.

선택 설치하는 CARLA adapter 0.2.0은 캡처를 켠 성공 실행에서 RGB/LiDAR 세 쌍의 MCAP를 생성하는 코드를 포함한다. 실제 CARLA 서버에서는 아직 실행하지 않았다. [CARLA 실행 절차와 제약](../integrations/carla/README.md)을 따른다. 원래 JSON LiDAR와 MCAP의 좌표 변환 차이, 시뮬레이션 timestamp 변환과 캡처 간 공백을 manifest에 남긴다.

API·SDK·CLI의 상세 계약은 [sensors-contract.md](sensors-contract.md), [SDK README](../sdk/python/README.md), 지원 형식과 라이선스 근거는 [sensor-format-notes.md](sensor-format-notes.md)를 참조한다. 비디오·RAW/Bayer·depth·일반 custom ROS·범용 protobuf, 대규모 fleet 재생과 실제 clock/calibration pilot은 후속 범위다.
