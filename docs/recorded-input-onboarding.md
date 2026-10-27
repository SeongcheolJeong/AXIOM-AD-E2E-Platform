# 기록 입력 점검과 명시적 프로젝트 사용

기존 MCAP 조사·SceneSequence·ReplaySpec·Experiment를 연결하는 입력 업무다. 정상
**Replay specifications → Imported MCAP ego/object channels**에서 허용된 원본을 선택하고,
지원 채널·정확한 표본·시간·자차 자세와 크기·원본 hash를 확인한 뒤 점검→freeze→replay→실행으로 이어진다.
점검은 입력 준비 상태이며 인지 정확도·센서 보정·실도로 안전 판정이 아니다.

## 사용자가 완료할 수 있는 업무

1. 운영자가 원본을 기존 Recordings API로 등록하고 정확한 SHA-256을 특정 Project에 명시적으로 grant한다.
2. 해당 Project 사용자가 source picker에서 허용된 원본과 정확한 ego/object schema를 선택한다.
3. 원래 ordinal·log/publish/capture 시각·world frame·자차 크기를 보고 필요한 ego 표본을 선택한다.
4. 변경할 clock offset과 허용 age/gap을 선언하고 **Inspect selected inputs**로 확인한다.
5. 원본/메시지/schema hash, capture clock·pose, causal object 누락, 추가 sensor 참조와 source-use 제한을 검토한다.
6. 지원 replay mode를 확인하고 **Freeze inspected sequence**로 같은 snapshot hash를 고정한다.
7. ReplaySpec을 저장해 retained preflight를 수행하고 기존 Experiments preview·queue·native trace·Plan 근거로 진행한다.

새 화면에서 Recording ID나 sample ordinal을 다른 화면에서 복사할 필요가 없다. 채널·표본·offset·설정을
바꾸면 기존 점검은 무효가 되고 freeze가 비활성화된다. 서버도 freeze 시 원본을 다시 검증해 점검 이후
source drift를 409로 거절한다. 점검·freeze는 simulator나 GPU 작업을 제출하지 않는다.

## 현재 지원 범위

입력은 exact `axiom.PlanningEgo.v1`과 `axiom.ObjectTracks.v1` JSON/schema 계약이다.
Topic 이름으로 역할을 추측하거나 누락 자세·크기를 채우지 않는다. 이 계약의 capture timestamp는
original MCAP log timestamp와 정확히 같아야 하며 선언한 log offset은 이후 정렬에 적용한다.
다른 capture clock을 가진 로그는 원본 시각을 보존하는 명시적인 converter가 필요하다.

객체 누락·stale을 빈 공간으로 처리하지 않는다. 이러한 구간의 original/open-loop 조사는 지원 범위에서
가능하지만 planning closed loop는 필요한 causal observation이 완결되어야 한다. 속도가 없는 객체의
TTC는 미평가다. Lane geometry와 reactive camera 폐루프는 지원하지 않는다.
이 reference planner는 ego/object geometry를 입력으로 사용한다. 물리 camera calibration과 실제
학습형 모델의 six-camera·CAN·history 요구사항을 충족했다고 표시하지 않는다. VAD 입력은 기존
DrivingSequence 계약에서 별도로 검사하며 native 파일 검증 상태를 구분한다.

채널 inventory는 기본 128개, API 최대 256개씩 조회한다. 표본은 기본 50개, API 최대 100개씩 원래
시간·ordinal 순서로 탐색하고 선택은 512개로 제한한다. 원본을 임의 decimation·보간·정렬·복제하지 않는다.
지원되지 않는 schema는 inventory에서 표시하고 converter가 필요하다고 남긴다. 일반 변환 편집기를 제공하지 않는다.

## 프로젝트와 원본 사용 제한

새 immutable recording grant는 source의 소유권 이전이 아니다. 운영자가
`POST /api/v1/project-recordings/grants`에 `project_id`, `recording_id`, 정확한
`recording_sha256`, 선택적 `note`, stable `Idempotency-Key`를 보낸다. Project Admin도
새 legacy source를 자기 Project에 grant할 수 없다. 동일 grant와 재시도 intent를 원자적으로 보존한다.

Team token은 default Project를 포함해 명시적으로 grant된 원본만 새 catalog/detail/samples/download/discovery 및
sequence authoring에 사용할 수 있다. 알려진 legacy ID만으로 접근할 수 없다. 기존 `/recordings` API는
operator 영역으로 유지한다. 운영자의 default Project만 기존 legacy 선택 호환성을 유지하며 다른 Project는
operator도 명시적 grant가 필요하다. 기존 Recording·Case·Dataset 전체의 소유권 migration은 수행하지 않는다.

정확한 MCAP metadata `axiom.source_provenance`의 `axiom.source-provenance/1` 계약을 해석한다.
nuScenes mini의 `isolated_noncommercial_research`, `commercial_use=false`, converter 개정과 lineage를
보존한다. 별도 `axiom.original_lineage`가 선언되면 exact field와 canonical original-row JSON의 SHA-256도
검증한다. 누락 provenance는 unknown, 잘못되거나 충돌하는 알려진 제한은 거절한다. Grant는 상용 권한을
추가하거나 기존 제한을 완화할 수 없다. Annotation 유래 객체는 ground truth이며 sensor detection이 아니다.
Operator가 선언한 footprint·reference origin도 실측 확인과 구분한다.

새 schema18은 grant와 retry intent 테이블만 추가한다. 기존 schema17 인수 데이터·sequence/1·execution
receipt를 바꾸지 않는다. 새 sequence/2는 원본 metadata와 해석한 source-use 범위를 snapshot에 보존하고,
과거 sequence/1 재검증은 과거 hash 구성을 유지한다. 실제 변경은 별도 복원 사본에서 인수한다.

## API·SDK

| API | 역할 |
| --- | --- |
| `POST /project-recordings/grants` | operator가 exact source grant |
| `GET /project-recordings?project_id=...` | 허용된 bounded catalog |
| `GET /project-recordings/{id}?project_id=...` | 원본 hash 확인과 bounded channel inventory |
| `GET /project-recordings/{id}/download?project_id=...` | Viewer도 가능한 인증된 원본 MCAP 스트리밍·SHA-256 확인 |
| `GET /project-recordings/{id}/samples?project_id=...&channel_id=...` | 한 개의 exact selector로 bounded 표본 탐색 |
| `GET /scene-sequences/sources/{id}?project_id=...` | exact schema role 발견 |
| `POST /scene-sequences/preview` | Viewer도 가능한 nonmutating 입력 점검 |
| `POST /scene-sequences/freeze` | Engineer가 definition과 preview SHA를 고정 |

모든 API에는 `/api/v1` 접두어를 붙인다. Samples selector는 `from_start=true`, `after_ordinal`,
`from_ordinal`, `at_ns` 중 하나다. SDK는 `grant_recording_source`, `project_recordings`,
`project_recording_samples`, `download_project_recording`, `replay_sources(project_id=...)`, `preview_scene_sequence`,
`freeze_scene_sequence`를 제공하며 동일 API를 사용한다. Role에 따라 생성된 agent catalog도 같은 경계를 제공한다.

Source picker의 **Download original MCAP**은 인증된 프로젝트 경로에서 원본을 받아 byte size와
SHA-256을 다시 확인한다. API는 먼저 원본을 검증한 같은 파일 descriptor를 스트리밍하며
`X-Content-SHA256`, `Content-Length`를 반환한다. 브라우저는 현재 원본 업로드 한도인 128 MiB까지
제한한다. SDK도 같은 프로젝트 API에서 스트리밍·해시 검증 후 destination을 원자적으로 교체하고,
실패하면 기존 파일을 보존한다. 응답에 들어 있는 임의 URL로 인증정보를 보내지 않는다.

Backend grant/clock/preflight 검사는 authored MCAP를 사용하는 계약 검사다.
`e2e/tests/recorded-input-onboarding.spec.ts`도 authored MCAP의 정상 UI/권한/점검/freeze/reference queue를 검사한다.
이를 actual road-data·차량 동역학·physical calibration 인수로 계산하지 않는다. 실제 원본 연구 로그의
converter와 정상 API 실행은 별도 원본/hash/모드/제한을 가진 근거로 기록한다.
