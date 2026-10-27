# 실행 큐 사용과 중단 복구

시나리오를 실행하면 **Execution queue**에 작업이 저장됩니다. 입력은 제출 시점의
시나리오 개정·파라미터·시드·어댑터 버전으로 고정됩니다. 화면을 닫거나 API를
재시작해도 대기 작업과 관측된 상태는 DB에 남습니다. 계산을 마친 작업의
**Open result**에서 기존 3D 재생·지표·근거 파일을 확인합니다.

## 실행하기

`scripts/dev.py`와 Finder 시작 파일은 API·작업 프로세스·UI를 함께 실행합니다.
서비스를 각각 실행한다면 API와 같은 작업 디렉터리에서 worker도 시작합니다.

```bash
.venv/bin/python -m ade2e.worker
```

API와 worker는 같은 `ADE2E_DATA_DIR`·SQLite DB·어댑터 설정을 사용해야 합니다.
worker는 예제 데이터를 생성하지 않습니다. `--once`는 대기 작업 하나를 처리한 뒤
종료하며, 기본 모드는 대기를 계속 확인합니다. 종료 신호는 새 작업 수신을 멈춥니다.
실행 중인 외부 엔진은 즉시 중단되었다고 가정하지 않습니다.

## SDK와 CLI

```python
from axiom_sdk import AxiomClient

with AxiomClient() as api:
    scenario = api.scenarios()[0]
    job = api.submit_job(scenario['id'], idempotency_key='my-test-001')
    print(job['id'], job['status'])
    terminal = api.wait_job(job['id'], timeout=120)
    if terminal['run_id']:
        run = api.get_run(terminal['run_id'])
        print(run['verdict'], run['provenance'])
    else:
        print(terminal['status'], terminal['error'])
```

```bash
.venv/bin/axiom submit-job SCENARIO_ID --idempotency-key my-test-001
.venv/bin/axiom jobs --status queued
.venv/bin/axiom job JOB_ID
.venv/bin/axiom wait-job JOB_ID --timeout 120
.venv/bin/axiom workers
.venv/bin/axiom cancel-job JOB_ID
```

응답을 잃었으면 **같은 키와 같은 요청**을 사용합니다. 이미 중단된 작업도 같은
ID와 결과를 돌려주며 엔진을 다시 실행하지 않습니다. `wait_job`의 시간 초과는
대기 중단일 뿐, 실행 취소나 재제출이 아닙니다. 다시 `get_job`으로 확인하세요.
키의 범위는 endpoint별입니다. `/jobs` 요청의 응답을 잃었다고 같은 키를 `/runs`에
보내면 다른 실행이 됩니다. 원래 endpoint에서 재시도하거나 기존 작업 ID를 조회하세요.
새 키는 새로운 실행 의도이며 그 시점의 시나리오를 다시 고정합니다. 이전 snapshot을
재현하려면 현재 시나리오와 입력 차이를 먼저 확인해야 합니다.

`completed`는 결과 저장 완료입니다. 충돌 등 시험 판정은 별도 `run.verdict`의
`passed/failed`로 확인합니다. 오류나 취소를 통과로 집계하지 않습니다.

## 취소와 결과 불명

- 대기 작업 취소는 엔진을 호출하지 않고 끝납니다.
- 참조 엔진은 계산 단계에서 취소를 확인하고 중단을 알립니다.
- 중단 기능이 없는 외부 어댑터에는 요청 사실을 기록합니다. 이후 완전한 결과가
  도착하면 보존하며 `cancel_not_honoured` 이벤트로 취소가 실행되지 않았음을 알립니다.
- worker 소유권이 만료되면 `interrupted`로 남깁니다. DB 게시 권한은 회수하지만,
  원격 엔진이 중지됐다는 근거는 아니므로 자동 재실행하지 않습니다.

외부 엔진의 결과가 불명이면 같은 어댑터의 다음 작업은 정리 확인까지 대기합니다.
다른 사용 가능한 엔진의 작업은 계속 처리할 수 있습니다. 과거 dispatcher를 먼저
중지하고 원격 엔진의 실제 작업·차량·센서 정리가 끝났는지 확인한 뒤 아래 명시적
API를 사용합니다. 연결 확인의 `ready` 상태만으로 정리를 대신할 수 없습니다.

```python
api.reconcile_execution_resource(
    'carla', job_id='INTERRUPTED_JOB_ID',
    note='실제로 수행한 dispatcher 종료 및 원격 엔진 정리 확인 내용',
    confirmed_stopped=True,
)
```

이 요청은 엔진을 직접 종료하지 않습니다. 운영자가 확인한 사실을 기록하고 이후
작업의 예약을 허용합니다. 원래 작업의 불명 상태와 이력은 그대로 남습니다.

## 근거 선택과 운영 범위

실행에 진입한 작업 중 제출 순서가 가장 새로운 작업을 현재 근거로 선택합니다.
오래 걸린 이전 작업이 나중에 완료되어도 새 근거를 덮지 않습니다. 최신 시도가
중단·취소·실패 상태라면 이전 통과 결과로 돌아가지 않습니다. 실행 전 취소와 알려진
입력 거절은 새로운 실행 근거를 만들지 않습니다.

DB schema 3은 작업·이벤트·worker·엔진 예약 테이블을 추가합니다. 기존 실행과
MCAP 테이블을 유지합니다. 업그레이드 전 DB와 관리 파일의 일관된 백업을 보존하세요.
아티팩트는 DB 밖의 시도별 staging에서 준비하고, 살아 있는 소유자만 결과를 게시합니다.
일반 실패는 자신의 파일을 정리합니다. 프로세스 강제 종료는 미참조 staging/파일을
남길 수 있으며, 현재는 활성 작업이나 기존 근거를 잘못 삭제하지 않도록 광역 자동
삭제를 수행하지 않습니다. 모든 관련 프로세스를 멈춘 뒤 참조 관계를 확인하는
운영 정리 절차와 자동 orphan 수거는 후속 과제입니다.

현재는 같은 host의 SQLite·관리 파일과 신뢰된 설치 어댑터 범위입니다. 외부 엔진
예약 키는 workspace 안의 adapter ID입니다. 서로 다른 workspace에서 같은 CARLA
서버를 공유하는 분산 소유권, PostgreSQL 큐, 대규모 배치·재시도 정책·조직 격리는
추가 구현과 인수시험이 필요합니다. [상태/API 계약](jobs-contract.md)을 참고하세요.
