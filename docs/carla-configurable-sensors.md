# 재사용 센서 설정과 실제 장착 위치

CARLA adapter 0.8.0의 새 경로는 LiDAR 광선 설정과 장착 위치를 서로 다른 불변 개정으로 저장한다. 광선 설정은 **Simulation sensors**, 위치·회전은 기존 **Sensor rig**에서 작성한다. 실행할 때 두 개정과 관측·정책·제어 stack을 고정한다. 기존 지붕 profile과 과거 실행은 그대로 읽는다.

개발판에 구현·배포한 기능이다. 실제 정차·회전 정차·주행4건의 설정 적용과 원본 보존을 확인했다. 두 주행 판정은 failed이며, 구현 시험과 실제 엔진 인수 범위는 [개발 상태](status.md)와 [검증 기록](validation/carla-configurable-sensors-2026-09-14.json)에 구분한다. 선언한 위치·속성의 적용 확인은 물리 센서 보정이나 주행 품질 인수를 뜻하지 않는다.

## 사용 순서

1. Simulation sensors에서 광선 설정을 만든다. 채널 수, 초당 광선 수, 수직·수평 시야각, 거리, 노이즈와 dropout을 명시한다. 기본값도 전체 설정에 포함된다.
2. 기존 rig 편집기로 LiDAR의 frame 이름과 sensor-to-ego 위치·quaternion 회전을 저장한다. Ego는 x 전방, y 왼쪽, z 위의 오른손 좌표계다. 새 센서마다 다른 frame 이름을 사용한다.
3. CARLA 실행에서 configurable sensor 모드를 선택하고 stack·rig·센서·설정의 정확한 개정을 고른다. 기존 fixed profile 모드는 별도로 유지한다.
4. 실행 결과에서 요청값, native spawn, 실제 actor 속성, 정지 상태와 모든 센서 frame의 실제 장착 위치를 확인한다. 누락된 관측은 미관측으로 표시한다. 실패한 위치 확인은 제어 입력으로 사용하지 않는다.

설정이나 rig를 개정해도 과거 run 입력이 바뀌지 않는다. 새 장착 위치가 차체 밖인지, 필요한 지면·장애물이 관측되는지는 별도의 실험으로 확인해야 한다. 자차 반사점이나 가려진 영역을 자동으로 지우거나 채우지 않는다.

## API와 에이전트

UI와 SDK는 같은 `/api/v1` API를 사용한다.

| API | 의미 |
| --- | --- |
| `GET /simulation-sensor-capabilities?adapter_id=carla` | 설치된 설정 schema, 기본값, 지원 버전·범위 |
| `POST /simulation-sensor-configurations` | 전체 설정을 새 불변 자산으로 저장 |
| `GET /simulation-sensor-configurations` | 현재 head 목록 |
| `GET /simulation-sensor-configurations/{id}?revision=N` | 특정 개정의 전체 snapshot |
| `POST /simulation-sensor-configurations/{id}/revisions` | `expected_revision`과 함께 새 개정 작성 |
| `GET /simulation-sensor-configurations/{id}/revisions` | 개정 이력 |
| `GET /simulation-sensor-configurations/{id}/export?revision=N` | 원래 canonical manifest bytes 내려받기 |

작성·개정에는 `Idempotency-Key`가 필요하다. 응답이 유실됐을 때 같은 body/key로 조회하며, 다른 설정을 같은 key로 제출하면 거절한다. 설정은 capability `carla-raycast-lidar/1`, version `1`을 고정한다. 제출 body의 `parameters`는 capability가 반환한 JSON Schema로 작성한다.

새 실행의 control 선택 형식은 다음과 같다. ID는 실제 생성 응답의 값을 사용한다.

```json
{
  "mode": "stack",
  "schema_version": "simulation-control-selection/2",
  "stack_id": "stack_id_from_api", "stack_revision": 1,
  "rig_id": "rig_id_from_api", "rig_revision": 1,
  "lidar_sensor_key": "front",
  "sensor_configuration_id": "configuration_id_from_api",
  "sensor_configuration_revision": 1
}
```

이 객체를 기존 `POST /jobs`의 `control`에 넣는다. 시나리오·설정이 고정되고 지원 capability와 원격 준비 상태를 확인한 뒤에만 실행한다. context/2를 구버전 어댑터로 보내거나 fixed `sensor_profile_id`를 혼합하면 거절한다. Context/1 또는 context 없는 과거 입력은 원래 serializer와 hash를 유지한다.

[SDK·CLI의 실행 예제](../sdk/python/README.md#carla-08-configurable-sensor-input)는 완전한 [광선 설정](../examples/carla-configurable-lidar.json)과 [rig 선언](../examples/carla-configurable-rig.json)을 사용해 등록→정확한 개정 선택→제출→같은 작업 대기→원본 artifact 다운로드를 보여준다. 예제 장착 위치는 검증할 입력이며 권장 센서 설계나 주행 품질 통과를 뜻하지 않는다.

## 실제 적용과 원본 데이터

위치는 rig가 유일한 기준이다. 광선 속성은 설정 개정이 기준이다. 새 context는 두 snapshot, capability, 유도된 전체 속성 및 원본 seed를 포함한다. Seed는 요청값이며, 현재 엔진의 내부 LiDAR RNG 적용을 증명하지 않는다.

센서-to-ego 오른손 행렬을 `H T H`, `H = diag(1,-1,1,1)`로 CARLA 좌표로 변환한다. Native Euler 값으로 만든 실제 client matrix를 다시 비교한 뒤 spawn한다. CARLA 회전 방향은 [고정 build의 Rotation 구현](https://raw.githubusercontent.com/carla-simulator/carla/1ae5356/LibCarla/source/carla/geom/Rotation.h)과 [Transform 구현](https://raw.githubusercontent.com/carla-simulator/carla/1ae5356/LibCarla/source/carla/geom/Transform.h)을 기준으로 한다.

새 경로는 두 차량 spawn·physics 적용 → LiDAR spawn → bounded rest → 센서·차량 readback → 초기 속도 요청 → 측정·제어 순서다. 관측마다 같은 world frame의 ego/sensor pose, sensor API pose, callback measurement pose와 parent actor ID를 남긴다. `carla-sensor-configuration.json`의 원본 행렬에서 상대 위치·회전·잔차를 다시 계산한다. Parent는 공개 API metadata이며 Unreal 내부 owner나 collision mesh의 증명이 아니다.

`carla-rigid-mount-readback/1`의 회전 행렬 허용오차는 2e-5, translation은 `2e-5 + 16 * 2^-23 * max(1, 관측 world translation의 절댓값 합)` m이다. Float32와 좌표 크기를 반영하는 수치 검사이며 이 한도가 1 cm를 넘으면 거절한다. Native spawn 변환 자체는 위치 1e-6 m, 회전 행렬 2e-6 이하다. 실제 주행 결과에 맞춰 이 값을 조정하지 않는다.

원본 XYZI는 그대로 보존한다. MCAP은 y sign bit만 반사한 센서 좌표와 선택된 frame 이름을 기록한다. 제어가 소비하는 XYZ는 모든 점을 원래 순서로 변환하며 기존 quaternion 산술과 정확한 bytes/hash 비교를 유지한다. 위치 readback 결과로 변환을 몰래 교체하지 않는다.

센서를 먼저 중지·제거하고 허용된 Python callback 종료를 확인한 뒤 차량을 정리한다. Drain 상태·실패·부분 prefix를 따로 남긴다. Python callback 종료는 C++ 전송 stream 전체의 완전성을 보증하지 않는다.

## 현재 한도와 남은 검증

한 개 LiDAR, 20 Hz, 2–4초 lane-follow/emergency-brake 경로다. 프레임당 20,000점, 개별 artifact 20 MiB, 전체 payload 32 MiB, wire 48 MiB 한도를 유지한다. 전체 실행의 N+1 frame을 raw·소비 XYZ·MCAP/base64·trace로 모두 저장할 용량을 사전 계산한다. Dropout이나 압축을 가정해 예산을 줄이지 않으므로, 프레임당 설정이 유효해도 긴 실행은 거절될 수 있다. 실제 수집 크기와 다음 명령의 저장 여유도 별도로 검사한다.

아직 다중 센서 융합, acquisition deskew, 다른 frame rate, 실제 광학/차량 보정, 학습형 인지 폐루프, 자차 반사 처리와 완만한 제동 품질을 인수한 것은 아니다. 조직 권한·분산 운영을 포함한 [전체 상용 출시 기준](roadmap.md)은 계속 열려 있다.
