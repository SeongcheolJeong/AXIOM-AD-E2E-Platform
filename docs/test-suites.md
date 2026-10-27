# 시험 묶음·캠페인·기준 결과 사용법

같은 시험을 반복하려면 어떤 시나리오와 판정 기준을 사용했는지 먼저 고정해야 합니다.
Suite는 개별 시험 case들의 개정된 정의입니다. Campaign은 그 suite의 특정 개정을
실제로 실행한 작업 묶음이고, baseline은 종료된 campaign의 결과를 보존한 비교 기준입니다.
실패나 미평가 항목이 있는 baseline도 만들 수 있으며, 기준 저장은 승인 판정이 아닙니다.

## 화면에서 사용하는 순서

1. Scenario library에서 시험할 시나리오를 작성하고 Validation에서 요구사항을 정의합니다.
2. Test suites에서 suite를 만듭니다. 각 case에 고유 key, 시나리오 개정, seed,
   입력 변경값과 해당 시나리오에 적용되는 요구사항을 선택합니다. 같은 시나리오도
   seed나 입력이 다르면 별도 case로 포함할 수 있습니다.
3. Suite의 개정과 사용할 adapter를 골라 campaign을 제출합니다. CARLA에서는
   [고정 센서·스택 선택](campaign-control.md)을 전체 case에 적용할 수 있습니다. 모든 case의 작업이
   함께 저장되고 기존 worker가 실행합니다. 제출 응답을 잃었을 때는 같은 요청으로
   재시도합니다. 새 요청 key는 새 실행입니다.
4. 각 case의 실제 작업 상태, 판정값, 미평가 이유를 확인합니다. 결과가 있으면
   해당 실행의 3D 재생과 원본 근거로 이동할 수 있습니다. 취소 요청이 원격 엔진의
   즉시 중단을 보장하지는 않습니다.
5. Campaign의 모든 작업이 종료되면 이름과 메모를 입력해 baseline을 저장합니다.
   이후 시나리오나 suite를 수정해도 이 기준의 case·결과·해시는 바뀌지 않습니다.
6. 다음 campaign에서 기준을 선택해 비교합니다. 실시간 비교를 검토한 후 종료된
   candidate의 비교 보고서를 저장하면 같은 내용을 다시 다운로드할 수 있습니다.

Suite를 개정할 때 현재 개정 번호를 함께 제출합니다. 다른 편집이 먼저 저장됐다면
409가 반환되므로 최신 정의를 읽고 의도한 변경을 반영해야 합니다. 과거 suite 개정은
원래 선택한 시나리오 개정과 요구사항 정의를 유지합니다.

## 수치를 읽을 때

- 작업 종료와 시험 통과는 다릅니다. 작업이 완료됐어도 하나의 판정 기준이 실패하면
  해당 case가 실패합니다. 하나라도 미평가 기준이 있으면 평가 완료로 표시하지 않습니다.
- 전체 case 수와 전체 check 수를 분모로 유지합니다. 취소·중단·오류·누락은
  분모에서 사라지지 않습니다. 실패와 미평가가 동시에 존재할 수도 있습니다.
- 비교는 같은 suite의 case key로 연결합니다. 추가·삭제된 case도 전체 비교 수에 포함됩니다.
- 시나리오 개정·유효 입력·판정 기준·평가 계약·adapter ID/버전·제어 context·fidelity·결과 schema·단위가
  달라지면 비교 불가 이유를 표시합니다. 측정 의미가 다른 수치를 회귀로 분류하지 않습니다.
- TTC의 명시적인 unbounded 값은 누락과 구분합니다. 숫자 차이는 양쪽 값이 유한하고
  단위와 비교 조건이 같을 때만 계산합니다.

Reference adapter의 결과와 도식 3D는 합성 객체 모델의 근거입니다. 이 workflow는
실제 CARLA 주행·센서·인지 성능이나 차량 안전성을 검증하지 않습니다.

## API·SDK·CLI

새 API는 기존 인증과 `/api/v1`을 공유합니다. 전체 응답 필드는
[suite 계약](suites-contract.md), 저장된 비교 근거는
[comparison 계약](comparison-evidence.md)을 참고하세요.

```python
from axiom_sdk import AxiomClient

with AxiomClient() as api:
    suite = api.get_suite("suite_id")
    campaign = api.submit_campaign(
        suite["id"], suite_revision=suite["revision"], adapter_id="reference",
        label="Regression candidate", idempotency_key="my-campaign-intent-001",
    )
    report = api.wait_campaign(campaign["id"], timeout=120)
    print(report["verdict"], report["counts"], report["evaluation_complete"])
```

`wait_campaign`은 조회만 합니다. timeout 후에도 같은 campaign ID로 조회를 이어가며,
취소나 재실행을 자동 요청하지 않습니다. HTTP 요청은 client 자체의 transport timeout을
따르므로 네트워크가 지연되면 전체 대기 시간이 길어질 수 있습니다.

```bash
.venv/bin/axiom create-suite /path/to/suite.json
.venv/bin/axiom submit-campaign SUITE_ID --suite-revision 1 \
  --label "Regression candidate" --idempotency-key my-campaign-intent-001
.venv/bin/axiom wait-campaign CAMPAIGN_ID --timeout 120
.venv/bin/axiom compare-campaign CAMPAIGN_ID --baseline-id BASELINE_ID
.venv/bin/axiom capture-comparison CAMPAIGN_ID --baseline-id BASELINE_ID \
  --name "Review record" --note "Input and missing evidence reviewed" \
  --idempotency-key my-comparison-intent-001
.venv/bin/axiom export-comparison COMPARISON_ID ./comparison.json
```

캠페인·baseline·comparison key의 범위는 각 endpoint별입니다. 응답 유실 후에는
같은 endpoint·key·본문으로 재요청하세요. SDK의 baseline/comparison 다운로드는
서버에 저장된 SHA-256을 검사하고 새 로컬 파일에만 기록합니다.

## 실행 가능한 예제와 현재 한계

API와 worker를 실행한 후 다음 명령을 실행합니다. 새 합성 시나리오 4개·요구사항 2개,
suite 2개정, campaign 3개와 작업 25개, 기준 결과와 비교 보고서를 실제 생성합니다.
출력 경로는 새 디렉터리여야 합니다.

```bash
.venv/bin/python examples/suite_workflow.py --output .runtime/my-suite-example
```

이 예제는 동일 입력의 8개 case 재실행, 라이브러리 개정 후 고정 입력 유지,
한 case의 조건 변경과 한 case 추가, 기준 파일 불변성을 검사합니다.
현재 suite는 최대 100개 case, case당 32개 요구사항이며 로컬 SQLite와 같은 host의
worker를 사용합니다. 분산 scheduler·통계적 유의성·독립 observer plugin·고객 시험 팩·
인증 기준·조직별 승인 절차는 후속 구현 및 인수시험이 필요합니다.
