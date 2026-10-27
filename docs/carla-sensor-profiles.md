# CARLA LiDAR 설정 선택·비교

세 개의 불변 센서 recipe를 같은 UI·API에서 선택합니다. 기본 선택은 기존 `carla-lidar-ego/1`입니다. 센서 recipe와 stack kernel은 각각 명시적으로 선택하며, 센서 선택만으로 저장된 알고리즘을 교체하지 않습니다. **0.7.0의 near 설정과 kernel2를 실제 원격 서버에 배포해 세 실행을 완료했습니다. 세 주행 판정은 모두 failed이며, 아래 기존0.6 결과와 구분합니다.**

| 설정 | 채널·수직 FOV | 명목 광선/s | 20 Hz에서 명목 광선/frame | 지원 adapter |
| --- | --- | ---: | ---: | --- |
| `carla-lidar-ego/1` | 16 · −30°..10° | 32,000 | 1,600 | 0.4.0, 0.4.1, 0.5.0, 0.6.0, 0.7.0 |
| `carla-lidar-ego-dense/1` | 16 · −30°..10° | 128,000 | 6,400 | 0.6.0, 0.7.0 |
| `carla-lidar-ego-near/1` | 64 · −65°..10° | 256,000 | 12,800 | 0.7.0 |

모든 설정은 20회전/s, 수평360°, range80m, 동일 roof mount `[0,0,2.2]m`를 사용합니다. General dropout0.45, 강도 기반 dropout, 감쇠·noise 설정도 같습니다. `noise_seed`는 override를 반영한 **유효 scenario seed**이며 준비 단계에서 전체 profile·context·input hash에 고정합니다. 기존 seed42 baseline profile hash는 `8acf977bad61ec1231dd50c12192a97f8e2b8b3dc07486c888aa0445b99a2d22`로 유지합니다.

Near 설정은 더 아래쪽과 근거리의 지면을 표본화하기 위한 별도 실험 설정입니다. 기존8건의648관측에서 범퍼 앞 첫1m 격자에는 지면점이 없었고, 실제 가장 가까운 지면점은 범퍼보다 약1.12–1.28m 앞이었습니다. 새 설정의 실제 반환점에서는 첫 격자에 표본이 추가됐습니다. 다만 일부 미확인 cell은 남았고, 이 변화로 공백 전체가 관측되었다고 해석하지 않습니다. 지면점2개라는 격자 조건 자체는 빈 공간이나 도로 안전성의 보증이 아닙니다.

명목 광선 수는 실제 hit·반환·유효 점 수가 아닙니다. 공식 CARLA 설명은 프레임당 채널 광선 수를 `points_per_second / (FPS × channels)`로 정의하고, tracing 전 general dropout과 hit 이후 강도 기반 dropout을 구분합니다. [CARLA 0.9.16 센서 문서](https://carla.readthedocs.io/en/0.9.16/ref_sensors/#lidar-sensor). 이 ray-cast 모델과 선언된 지면·rig는 실측 보정 센서 모델이 아닙니다. 누락 ray의 개별 원인을 기록하지 않으므로 더 조밀한 설정의 결과로 특정 dropout 원인을 확정하지 않습니다.

## UI·Agent 사용

Run simulation에서 CARLA·LiDAR shared stack을 선택하고 stack/rig 개정, roof sensor, sensor profile을 지정합니다. catalog를 읽지 못하거나 선택 profile이 adapter 버전과 맞지 않으면 제출이 막힙니다. 제출 요약과 저장된 execution context에 profile ID·hash가 남습니다.

```bash
.venv/bin/axiom simulation-sensor-profiles
```

```python
profiles = api.simulation_sensor_profiles()
control = {
    "mode": "stack",
    "stack_id": saved_stack["id"], "stack_revision": saved_stack["revision"],
    "rig_id": saved_rig["id"], "rig_revision": saved_rig["revision"],
    "lidar_sensor_key": "roof",
    "sensor_profile_id": "carla-lidar-ego-dense/1",
}
job = api.submit_job(scenario_id, adapter_id="carla", seed=42,
                     control=control, idempotency_key="my-dense-comparison-001")
```

`GET /api/v1/simulation-sensor-profiles`는 지원 버전·seed 없는 attributes·mount·명목 광선 수·출처를 반환합니다. 서버나 simulation을 시작하지 않습니다. OpenAPI와 `/api/v1/agent/tools`에 같은 endpoint를 제공합니다. 임의 attributes 수정은 지원하지 않으며, profile/hash를 함께 변조해도 고정 recipe와 다르면 거절합니다. 기존 최대20,000점/frame 및 원본·artifact byte 한도는 그대로이며 초과 결과를 성공으로 잘라 저장하지 않습니다.

## 버전과 원본 회수

0.7.0 worker는 새 실행에 정확히0.7.0 계약을 요구하며, 기존0.5.0·0.6.0 실행의 조회·archive 다운로드·회수는 원래 request/version/store/hash를 검증합니다. 완료 archive bytes를 재작성하거나, 예전 실행 ID로 새 simulation을 시작하거나, 부분 진단을 완료 궤적으로 바꾸지 않습니다. 이전0.5.0 요청에 dense recipe를 결합하거나0.5/0.6에 near 또는 kernel2를 결합한 입력은 읽기·전송·저장 검증 경계에서 거절합니다. 미지원 미래 버전도 거절합니다. [영속 회수 계약](carla-recovery.md).

## 0.7 실제 시험과 남은 문제

실제 CARLA build1ae5356·Town10HD_Opt에서 near profile을 사용한 4초 실행 3건을 완료했습니다. 각 실행의 80명령·81관측·원본 점군·MCAP·차량 응답은 저장됐지만, 기존 주행 기준은 모두 실패했습니다.

| Near profile 조건 | 저장된 명령 80구간 | 최대 감속 / 최대 jerk | 실행 상태 / 주행 판정 |
| --- | --- | --- | --- |
| Kernel 1 · cruise | `obstacle_stop` 80 | 8.633477 / 170.319628 | completed / failed |
| Kernel 2 · cruise | `emergency_stop` 80 | 8.633477 / 170.319628 | completed / failed |
| Kernel 2 · close-lead | `emergency_stop` 80 | 6.219846 / 73.066306 | completed / failed |

감속 단위는 m/s², jerk는 m/s³입니다. Kernel 2의 첫 1m row는 cruise 80/80, close-lead 79/80구간에서 세 lateral cell의 최소 2점 조건을 만족했습니다. 이는 표본 조건이며 free-space 증명이 아닙니다. 두 실행 모두 범퍼 기준 positive surface 거리가 80구간 내내 0으로 계산되어 정지 envelope를 충족하지 못했고, S-curve plan은 한 번도 생성되지 않았습니다. **S-curve의 실제 차량 추종이나 부드러운 제동 개선은 이번 시험으로 검증되지 않았습니다.**

이 근거리 positive point를 자차 반환으로 보는 설명은 아직 후보입니다. 실제 출처를 확인하지 않고 삭제하거나, near profile의 ground sampling 증가를 주행 성공으로 해석하지 않습니다. 과거 0.5 completed GT/live·interrupted partial 및 0.6 dense의 archive 4개와 artifact 33개는 새 0.7 reader에서도 원본 bytes가 일치했습니다. 최신 상태는 [status](status.md), 세 실행과 호환성의 통합 근거는 [0.7 인수 보고서](validation/stack-v2-2026-09-13.json)를 확인합니다.

## 이전 0.6 실제 시험 기록

실제 CARLA build1ae5356·Town10HD_Opt에서 baseline/dense 8건을 완료했습니다. 다음은 변경하지 않은 기존 결과입니다. [0.6 인수 보고서](validation/carla-sensor-profiles-2026-09-13.json).

| 조건 | Baseline | Dense |
| --- | --- | --- |
| 2m/s · 이동 선행차 · gap20m | jerk11.362139 · failed | jerk1.285987 · passed |
| 2m/s · 정지 선행차 · gap8m | 감속7.965472 / jerk152.474544 · failed | 감속8.948179 / jerk176.453677 · failed |

Cruise에서 baseline의 관측 부족 정지 1구간이 dense에서는 나타나지 않았습니다. 기존0.5 baseline과 새0.6 baseline의 81개 궤적·지표는 정확히 같습니다. 가까운 정지 선행차에서는 dense의 급제동 수치가 더 컸으므로 센서 sampling 증가를 전반적인 주행 품질 개선으로 일반화하지 않습니다.

각 실행의 80명령·81관측과 원본 점군·MCAP·차량 응답을 보존하고 재계산했습니다. Cruise wall time은 baseline28.2–28.3초, dense35.9–37.0초로 실시간 실행이 아닙니다.

**Seed 제한:** cruise는 요청 seed42/43/44로 profile마다 3회 실행했지만, profile별 원본 XYZI·소비 XYZ·궤적이 정확히 같습니다. Native callback journal에서도 동일하므로 저장 파일만의 재사용으로 해석하지 않습니다. 각 profile의 독립 무작위 표본 3개로 통계 처리할 수 없습니다. Blueprint의 noise_seed readback은 실제 actor RNG 내부 상태 검사가 아닙니다.

공식 [1ae5356 RayCastLidar.cpp](https://raw.githubusercontent.com/carla-simulator/carla/1ae5356/Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Sensor/RayCastLidar.cpp)는 생성자에서 기본 seed를 설정한 뒤 새 description을 적용할 때 RNG를 다시 seed하지 않습니다. 이후 실행 중인 Shipping ELF와 matching debug symbol에서도 configuration 함수의 reseed 누락을 확인했습니다. 실제 센서 RNG 내부 상태 readback과 수정 엔진의 인수는 남아 있습니다. [정적 바이너리 근거·patch 경계](carla-lidar-seed-diagnosis.md). Near 설정도 이 한계를 해결했다고 표시하지 않습니다.

Python/browser 계약시험의 fake-world 점, 기존0.6의 실제8건, 후속0.7의 실제3건을 구분합니다. 추가 ODD, 정밀 동역학, 실측 센서 상관성 및 상용 release gate는 후속입니다.
