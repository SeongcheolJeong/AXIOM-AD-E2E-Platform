# 첫 실험 진행하기

## 1. Overview에서 실행 확인

상단 숫자는 DB에 저장된 시나리오·실행·근거에서 계산합니다. 기본 예제는 직접 작성한 합성 참조 시나리오입니다. 재생 화면의 차량 위치는 선택한 실행의 시간별 궤적을 사용합니다. 3D 도로와 배경은 의미를 설명하는 도식이며 실측 지도나 카메라 출력이 아닙니다.

재생/일시정지, 타임라인, 카메라 뷰를 바꾸면서 속도·TTC와 사건을 확인합니다. TTC가 비어 있으면 해당 시점의 단순 예측 모델에서 충돌 시간을 정의하지 않았다는 뜻입니다. 실제 환경의 무조건적인 안전을 의미하지 않습니다.

## 2. Scenario library에서 시험 만들기

`New scenario`로 이름, 상황, 날씨·시간, 속도, 간격, 개입 시간, 마찰·응답 지연을 설정합니다. 단위는 m, s, m/s입니다. 참조 엔진에서 날씨·지도·시간 이름은 분류 메타데이터입니다. 이미지 밝기·노이즈·굴절을 계산하지 않습니다.

복제하면 독립 ID의 시험이 생깁니다. 편집은 개정 번호를 증가시키며 이미 저장한 실행 스냅샷을 바꾸지 않습니다.

## 3. 실행과 실패 조사

시나리오 실행 버튼이나 Execution queue의 `New execution`으로 `Launch a simulation` 창을 엽니다. 시나리오·어댑터·선택적 시드 override를 확인하고 `Run simulation`을 누릅니다. 이 동작은 `POST /jobs`로 입력 스냅샷과 실행 요청을 저장한 뒤 **Execution queue**로 이동합니다. 별도 worker가 어댑터를 실행하며 화면에서 결과가 나올 때까지 기다릴 필요는 없습니다.

응답이 끊겼다면 같은 창에서 `Retry submission`을 사용하세요. 입력이 같으면 원래 요청 키를 유지합니다. 입력을 바꾸거나 성공 후 새 실행을 시작하면 별도 요청입니다. 연결 실패나 브라우저 종료는 실행 취소를 뜻하지 않습니다.

큐에서 상태 필터, 현재 페이지 내 검색, 이전 제출 페이지 탐색을 사용할 수 있습니다. 상단 개수는 **현재 불러온 페이지** 기준입니다. 작업을 선택하면 현재 관측 상태, 이벤트 시각, worker, 입력 SHA-256, 캡처한 시나리오 개정과 요청을 확인합니다. 일시적인 갱신 오류에는 마지막 관측임을 표시합니다. 계산된 진척률이 없으므로 진행 퍼센트를 표시하지 않습니다.

| 큐 상태 | 의미 |
|---|---|
| `queued` | 요청이 저장되었고 worker 할당을 기다림 |
| `running` | worker가 작업을 소유함; 실제 어댑터 호출 의도 시각은 별도로 기록 |
| `cancel_requested` | 취소 요청을 기록했지만 실제 중단은 아직 확인하지 못함 |
| `completed` | 실행 결과가 저장됨; 지표 판정은 실패일 수 있음 |
| `failed` | 사전 확인·실행·결과 저장 중 오류; 오류와 연결된 근거 확인 필요 |
| `cancelled` | 호출 전 취소되었거나 협력적 취소로 중단이 확인됨 |
| `interrupted` | worker 소유권이 만료되었거나 실행 결과를 확정할 수 없음; 자동 재실행하지 않음 |

결과가 연결되면 **Open result**로 Run explorer를 엽니다. 여기서 통과/실패 지표, 충돌·급제동 사건, 입력 스냅샷, 시드와 해시를 확인하고 JSON 근거를 내보낼 수 있습니다. 큐의 `completed`는 실행 완료이고 Run의 `passed/failed`는 예제 판정 규칙의 결과입니다. Run의 `error`는 실행 오류이며 요구사항 만족으로 계산하지 않습니다.

`Cancel queued job`은 대기 요청을 취소합니다. `Request cancellation`은 실행 중 취소 의도를 전달합니다. 참조 어댑터는 계산 중 협력적 취소를 확인하지만, 현재 CARLA 원격 어댑터는 이를 지원하지 않아 취소 요청 후에도 결과가 도착할 수 있습니다. 화면에 **Execution outcome is uncertain**이 표시되면 외부 동작이 계속되었을 가능성을 확인해야 합니다. **cleanup confirmation required** 안내가 있는 어댑터는 운영자가 기존 dispatcher 종료와 외부 엔진 정리를 확인하고 명시적으로 자원을 reconcile하기 전까지 후속 작업을 보류합니다. 연결 확인 성공만으로 정리가 확인되지는 않습니다. 절차와 API는 [실행 큐 계약](jobs-contract.md)에 있습니다.

`scripts/dev.py`는 API·큐 worker·UI를 함께 시작합니다. API를 따로 실행했다면 저장소 루트의 별도 터미널에서 아래 명령을 실행하세요. 사용자 지정 `ADE2E_DATA_DIR`와 어댑터 설정은 API와 같아야 합니다.

```bash
.venv/bin/python -m ade2e.worker
```

worker heartbeat는 프로세스 상태를 나타냅니다. heartbeat가 없으면 대기 작업이 실행되지 않을 수 있으며, 이미 호출된 외부 엔진이 중단되었다는 근거도 아닙니다. 기존 `POST /runs`와 SDK `run()`은 같은 영속 실행 경로에서 결과를 기다리는 호환 방식으로 남아 있습니다. 새 자동화에는 작업 제출·조회 API를 사용하세요.

## 4. Validation에서 근거 연결

예를 들어 `collision_count == 0` 요구사항을 만들고 대상 시나리오를 연결합니다. 현재 개정과 설정에 맞는 기준 실행을 사용하며, 어댑터 호출에 들어간 작업의 제출 순서를 반영합니다. 늦게 도착한 오래된 결과는 더 최근의 시도를 덮어쓰지 않습니다. 새 실행이 진행 중이거나 중단·오류로 필요한 기준 근거가 없으면 오래된 합격으로 되돌리지 않습니다. 단순 대기나 호출 전 취소는 새 실행 근거를 만들지 않습니다. Coverage는 등록한 시험 공간의 관측 상태이며 실제 도로 전체나 규제 인증의 커버리지가 아닙니다.

## 5. Test suites에서 반복 시험과 비교 근거 만들기

여러 시험을 같은 조건으로 반복하려면 **Test suites**에서 시험 정의와 실행 결과를 구분해 저장합니다. 화면에서는 다음 순서로 진행합니다.

1. **New suite**에서 case별 고유 key, 시나리오 개정, seed·parameter override와 적용할 요구사항을 선택합니다. 같은 시나리오도 조건이나 seed가 다르면 별도 case로 묶을 수 있습니다. 저장된 suite revision은 선택한 입력과 요구사항 정의를 고정합니다.
2. **Run suite**에서 실행할 suite revision·adapter·campaign label을 확인하고 **Submit campaign**을 누릅니다. 모든 case의 실제 job이 함께 저장되고 기존 worker가 실행합니다. 응답을 잃었으면 **Retry campaign submission**으로 같은 요청을 재제출합니다. 입력이 같으면 같은 키를 유지하므로 새 작업 묶음을 중복 생성하지 않습니다.
3. Campaign에서 case별 작업 상태·판정 기준·미평가 이유를 확인하고, 연결된 Run을 열어 실제 궤적과 근거를 조사합니다. Campaign이 `finished` 상태가 되면 **Capture baseline**에 이름과 메모를 넣어 기준 결과를 고정합니다. 실패·미평가가 있는 기준도 저장할 수 있으며 기준 저장은 승인이 아닙니다.
4. 조건을 바꾸려면 **Create revision**에서 새 정의를 만들고 **Publish suite revision**으로 저장합니다. 과거 suite revision과 baseline은 그대로 남습니다. 다음 campaign에서 **Comparison baseline**을 선택하면 **Live comparison**으로 현재 후보와 고정 기준을 비교합니다.
5. 후보가 `finished` 상태가 된 뒤 **Capture comparison**에 이름과 검토 메모를 입력하면 비교 내용을 고정합니다. **Saved comparisons**에서 다시 열고 **Download comparison**으로 저장된 JSON과 해시를 확인합니다. 저장된 비교는 현재 시나리오나 캠페인 상태로 다시 계산하지 않습니다.

`finished`는 대기·실행 중으로 남은 job이 없다는 뜻이며, 중단·누락으로 결과를 확정하지 못한 항목도 포함합니다. 모든 case의 모든 판정 기준이 통과해야 campaign이 `passed`가 됩니다. 취소·중단·오류·누락은 전체 case/check 분모에 남으며, 하나의 기준은 실패하고 다른 기준은 미평가인 경우도 구분해 표시합니다.

비교는 case key로 연결합니다. 시나리오 개정·유효 입력·판정 기준·평가 계약·adapter ID/버전·측정 범위·결과 schema·단위가 다르면 `not_comparable`이며, 필요한 평가 근거가 없으면 `not_evaluated`입니다. 조건을 바꾼 수치를 성능 개선이나 회귀로 자동 분류하지 않습니다. 추가·삭제된 case도 전체 비교 수에 남습니다. 다른 저장 비교를 선택한 뒤 조회에 실패하면 이전 비교 표를 숨겨, 다른 근거를 현재 선택의 결과로 보여주지 않습니다.

상세 절차와 API·SDK 예제는 [시험 묶음·캠페인·기준 결과 사용법](test-suites.md)에 있습니다.

## 6. Data catalog와 Agent studio

Data catalog의 **Execution artifacts**에서 실행 파일·출처·해시·형식을 탐색하고 내려받습니다. **Recordings**에서는 MCAP 파일을 등록하고 채널·정확한 시각·JSON 신호·원본 바이트를 조사합니다. 시간 커서와 별도로 첫 샘플부터 페이지를 넘겨 같은 시각의 메시지도 모두 확인할 수 있습니다. [기록 데이터 사용법](data-recordings.md)에 합성 예제와 지원 범위를 정리했습니다. 대규모 실차 로그의 지속 수집·의미 검색·센서 동기 재생은 후속 단계입니다.

Agent studio에서 API 도구와 예제를 복사할 수 있습니다. Python SDK로 반복 작업을 자동화하는 전체 예제는 `examples/agent_workflow.py`입니다. 자연어 응답을 흉내 내는 채팅은 넣지 않았습니다.

## 7. 첫 CARLA 실행 준비

Integrations의 CARLA는 서버 설정 전 `not_configured`입니다. [CARLA 연결 문서](../integrations/carla/README.md)에 따라 CARLA HTTP worker를 준비하고 시나리오의 지도명을 현재 서버 지도와 일치시킵니다. 이 외부 엔진 서비스는 플랫폼 큐 worker인 `ade2e.worker`와 별개입니다. v0.1 외부 어댑터는 직선 도로의 `lane_follow`, `emergency_brake`만 지원합니다. 카메라·LiDAR 캡처는 실제 worker 결과가 도착한 경우에만 데이터 카탈로그에 나타납니다. CARLA 실실행과 센서 동작은 아직 별도 검증이 필요하며 현재 큐 기능을 상용 출시나 센서 검증 완료로 해석하지 않습니다.
