# 고정 센서·스택으로 시험 묶음 실행

Campaign은 저장된 시험 묶음의 모든 case를 선택한 CARLA 제어 구성으로 실행한다. 개별 실행과 같은 `control` 객체를 사용하며, 센서 설정·rig·stack의 정확한 개정을 선택한다. Case마다 저장한 seed·시나리오 개정·조건 변경·요구사항 기준을 유지한다. 제어 구성을 생략하면 기존 adapter 기본 제어 경로다.

## 사용 순서

1. Scenario library에서 지원되는 시나리오를 작성한다. 현재 CARLA shared stack은 로딩된 지도에서 20 Hz, 2–4초의 lane-follow/emergency-brake 경로를 지원한다.
2. Test suites에 시나리오 개정·조건 변경·seed·요구사항을 고정한다. 시험의 판정 기준은 센서 설정과 별도로 보존한다.
3. Run a test suite에서 CARLA와 공유 stack을 선택한다. Fixed roof profile 또는 [재사용 센서 설정](carla-configurable-sensors.md)의 정확한 rig/설정 개정을 고른다.
4. 전체 case와 유효 구성을 검토한 뒤 제출한다. 한 case라도 지원 범위 밖이면 전체 제출이 거절되며 일부 job만 생성되지 않는다.
5. Campaign에서 각 case의 예상 입력·제어 context와 실제 run의 일치 여부, 요구사항 판정과 원본 artifact를 조사한다. 실행 완료와 주행 기준 통과를 구분한다.

## API·SDK·CLI

`POST /api/v1/campaigns`의 `control`은 `POST /jobs`와 동일하다. 실제 반환된 ID와 개정을 아래 예제에 넣는다. `lidar_sensor_key`는 rig 내부의 sensor key이며 frame 이름과 다를 수 있다.

```json
{
  "suite_id": "SUITE_ID",
  "suite_revision": 1,
  "adapter_id": "carla",
  "label": "Front LiDAR · fixed pilot",
  "control": {
    "mode": "stack",
    "schema_version": "simulation-control-selection/2",
    "stack_id": "STACK_ID", "stack_revision": 1,
    "rig_id": "RIG_ID", "rig_revision": 1,
    "lidar_sensor_key": "front",
    "sensor_configuration_id": "CONFIGURATION_ID",
    "sensor_configuration_revision": 1
  }
}
```

제출에는 `Idempotency-Key`가 필요하다. 응답 유실 후에는 같은 endpoint·본문·키를 사용한다. 이미 campaign ID를 받았다면 그 ID를 계속 조회하며, 대기 시간이 끝났다고 새 실행을 제출하지 않는다. 생성 이후 library의 head가 바뀌어도 기존 member job의 입력은 바뀌지 않는다.

```python
import json
from pathlib import Path
from axiom_sdk import AxiomClient

control = json.loads(Path("selection.json").read_text())
with AxiomClient() as api:
    campaign = api.submit_campaign(
        "SUITE_ID", suite_revision=1, adapter_id="carla",
        label="Front LiDAR · fixed pilot", control=control,
        idempotency_key="front-lidar-campaign-001",
    )
    print(campaign["id"])  # Save before waiting.
    result = api.wait_campaign(campaign["id"], timeout=300)
    api.export_campaign(result["id"], "campaign-report.json")
```

`selection.json`에는 위 `control` 객체만 넣는다. CLI도 같은 객체를 사용한다.

```sh
axiom submit-campaign SUITE_ID --suite-revision 1 --adapter carla --label "Front LiDAR · fixed pilot" --control selection.json --idempotency-key front-lidar-campaign-001
axiom wait-campaign CAMPAIGN_ID --timeout 300
axiom campaign CAMPAIGN_ID
```

현재 campaign export는 조회 시점의 보고서다. 확정한 기준 결과는 별도 baseline으로 저장한다. 실행 실패·취소·미평가 case도 원래 분모에 남는다. Baseline은 승인이나 센서 보정 증명이 아니다.

## 저장과 검증 경계

서버는 campaign의 선택과 고정 context template, case별 예상 input/context/request hash 및 member job 관계를 독립적인 execution plan으로 저장한다. Case마다 유효 seed로 context를 다시 구성해 비교하고, 실제 job/run이 그 계획과 일치할 때만 요구사항을 평가한다. Job에 기록된 context만으로 자기 자신을 검증하지 않는다.

새 controlled campaign은 `carla-observed-contact-count/1` 판정 계약도 plan에 고정한다. CARLA의 `collision_count` 원본 단위는 `observed contacts`이며 측정 구간 안에서 collision callback으로 보고된 서로 다른 접촉 actor의 수다. 이 계약에서만 그 정수 개수를 suite의 `count` 기준에 적용하고, 원본 단위·값·기준은 그대로 보존한다. 적용한 check에는 `measurement_contract`를 표시한다. 누락·다른 단위·잘못된 개수는 미평가이며 이전 campaign에는 이 계약을 소급 적용하지 않는다. 관측된 접촉 수0의 통과는 collision stream 완전성이나 실제 무충돌 보장이 아니다.

Schema11은 기존 campaign 테이블에 nullable execution plan 열을 추가한다. 제어 구성을 선택하지 않은 과거 campaign에는 plan을 소급 작성하지 않으며 기존 요청·export·baseline bytes를 유지한다. 새로운 필드가 없는 과거 보고서는 원래 경로로 읽는다.

각 campaign은 하나의 제어 선택을 사용한다. 서로 다른 센서·rig·stack을 시험하려면 별도 campaign을 만든다. 기존 비교는 제어 context가 바뀐 case를 `not_comparable`로 표시하며 단순 수치 차이를 성능 개선으로 판정하지 않는다. 구성 변경의 효과를 정량 평가하는 별도 실험 설계·통계 모델은 후속 범위다.

현재 원격 CARLA의 관측값은 미보정 시뮬레이션 근거다. 요청 seed의 내부 RNG 적용, 물리 센서 정확도·차량 제동 모델, 실시간/분산 실행과 [전체 상용 출시 기준](roadmap.md)은 별도로 인수해야 한다. 구현 시험과 실제 실행 결과는 [개발 상태](status.md)에 구분한다.
