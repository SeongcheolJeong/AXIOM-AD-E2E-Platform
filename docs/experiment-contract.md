# 공통 Experiment · 팀 권한 · 형식별 검증 계획

2026-10-07 구현 계약. 기존 detection A/B Verification Plan과 기존 v1 Run을 유지한다.
이 문서는 연결 기능과 실행 근거의 경계이며 경쟁사 동등성·센서 물리·실도로 안전 인수를 뜻하지 않는다.

## 일반 업무 흐름

`Project → Experiment preview → 불변 Experiment 저장 → 명시적 실행 제출 → 기존 worker → 원본 파일/실행 receipt → 형식별 검증 계획 평가`.

Experiment는 `object_sim`, `planning_replay`, `sensor_capture`, `isp_compare`,
`driving_open_loop` 중 하나를 명시한다. 설치·설정된 handler만 preview할 수 있다.
지원하지 않는 입력·버전·비교 유형은 입력 동작에서 거절하며 예제 결과로 대체하지 않는다.
실행 상태 `completed`와 평가 기준 `passed`는 별도다.

`sensor_capture`의 선택적 `carla-feedback-sensor/1.0.1`은 지도 기준 계획·실제 자차
피드백 제어·새 RGB를 같은 frame/time으로 보존한다. 기존 정지 capture와 별도 입력/결과
계약이며 같은 preview/create/submit/artifact/export API와 SDK를 사용한다.
원격 host/path는 운영자 설정으로만 지정한다. [입력·시간·실행 경계](carla-feedback-sensor-viewer.md).

기록 입력은 정상 **Replay specifications → Imported MCAP ego/object channels**에서
허용된 원본→지원 채널→exact ordinal→입력 점검→freeze→ReplaySpec로 준비한다.
`POST /scene-sequences/preview`는 Viewer도 사용하는 nonmutating 검사다.
`POST /scene-sequences/freeze`는 Engineer가 `{definition, preview_sha256}`으로 같은 점검 snapshot을
저장하며 원본 drift는 409로 거절한다. 입력을 바꾸면 이전 점검으로 freeze할 수 없다.
지원 v1 planning payload의 capture timestamp는 native MCAP log timestamp와 정확히 같아야 하고,
선언한 log offset은 그 검증 후 정렬에 적용한다. 다른 capture clock은 원본을 보존하는 명시적 converter가
필요하다. 객체 누락·stale·크기·좌표 계약 부족과 미평가를 보존한다. 이 입력 점검은 calibration·인지 정확도·
주행 안전 판정이 아니다. [전체 입력 업무·제한](recorded-input-onboarding.md).

주요 API는 `/api/v1/experiments/capabilities`, `POST /experiments/preview`,
`POST /experiments`, `GET /experiments/{id}`, `POST /experiments/{id}/executions`,
`GET /experiments/executions/{job_id}`이며 모든 경로에는 `/api/v1` 접두어를 붙인다.
저장·제출에는 `Idempotency-Key`가 필요하다. 제출은 저장된 `preview_hash`와 정확히 일치해야 한다.
기존 작업 큐·lease·취소·worker fencing을 사용한다. 만료된 시도는 자동 재실행하지 않는다.

Experiment의 불변 입력은 project, 이름, 실행 형식, handler 버전, 원본 입력 snapshot,
모델·센서·평가기 구성, 비교 형식과 seed를 포함한다. Handler가 전달받는 JSON snapshot은
`prepare()`가 검증·고정한 입력이며 외부 원본 URI만 저장한 경우 해당 원본의 내용 hash와 개정을
handler가 별도로 검사해야 한다. 미지원 capability를 구성 메타데이터로 대신하지 않는다.

비교 형식은 `single`, `same_input_model_ab`, `same_raw_isp_ab`, `same_scene_sensor_ab`다.
비교 형식을 선택했다고 모든 handler가 이를 지원하는 것은 아니다. 모델·원본·대응 라벨의
실제 비교 계약은 선택한 handler가 검사한다.

## 파일과 실행 근거

Core는 engine 라이브러리를 import하지 않는다. `ExperimentRegistry`와 선택적
`ade2e.experiment_handlers` entry point는 `metadata`, `prepare`, `execute`를 연결한다.
API와 worker는 같은 기본 registry factory를 사용한다. API에만 등록된 실행 환경을 worker가
같은 버전으로 제공하지 않으면 실행하지 않는다.

Native 파일은 실행 소유 디렉터리에서 1 MiB씩 읽어 hash·복사한다. 기본 한도는 파일당 2 GiB,
묶음당 8 GiB, 256개 파일, JSON manifest 4 MiB다. 합쳐 저장하는 terminal receipt의 여유를 확보하기 위해
입력 snapshot은 2 MiB, 결과 summary는 1 MiB로 제한한다. 큰 이미지·point cloud를 JSON/base64로 모으지 않는다.
인증된 다운로드는 파일을 다시 검사하고 `X-Content-SHA256`를 반환한다. 출력 파일·receipt는
완료 상태와 함께 원자적으로 DB에 연결한다. 프로세스 종료 시 미연결 파일이 남을 수 있으며
이를 성공 근거로 집계하거나 자동 삭제하지 않는다.

외부 실행 실패·lease 손실은 `outcome_uncertain`로 남는다. Resource를 공유하는 handler는
같은 `resource_id`로 예약한다. CARLA 센서 capture는 기존 CARLA job과 `carla` 예약을 공유한다.
실제 원격 실행 중단·정리를 확인하기 전에는 해당 resource를 재사용하지 않는다.
운영자 reconciliation은 resource의 정리 기록이며 기존 terminal receipt·불확실성을 수정하지 않는다.
실패한 시도의 private 작업 디렉터리와 bounded `external-dispatch.json` 진단 receipt를 보존한다.
진단 파일은 실행 완료 또는 cleanup acknowledgement가 아니다.

## 팀 권한과 현재 프로젝트 범위

`ADE2E_TEAM_MODE=1`은 운영자 bootstrap `ADE2E_API_TOKEN`을 요구한다. Bootstrap token은
기존 단일 운영자 API를 유지하며 project 생성·사용자 token 발급을 담당한다.
`POST /api/v1/projects/{id}/tokens`은 임의 토큰을 한 번 반환하고 DB에는 SHA-256만 저장한다.
사용자 label과 역할은 project에 묶인다. 발급 값을 source·URL·문서 예제로 저장하지 않는다.
`GET /api/v1/team/whoami`는 역할과 사용 가능한 범위를 보여준다.

| 역할 | 권한 |
| --- | --- |
| Viewer | 해당 project의 새 도메인 조회·다운로드 |
| Engineer | 해당 project의 입력·Experiment·검증 계획 작성과 실행·취소 |
| Reviewer | 해당 project 조회와 고정 실행 근거를 계획에 평가·capture |
| Admin | 해당 project의 위 권한 및 사용자 token 발급·회수 |

현재 project 경계는 새 Experiment·ScenarioV2·SceneSequence·ReplaySpec·형식별 검증 계획과
명시적으로 grant된 Recording의 새 project API에 적용한다.
기존 Recording·Case·Dataset·모델 등은 전체 프로젝트 격리가 완료되지 않았다. 사용자 project token으로
이들 legacy API에 접근하면 403으로 거절한다.

운영자만 `POST /project-recordings/grants`에 project·recording·exact 원본 SHA와 stable
`Idempotency-Key`를 지정할 수 있다. Project Admin도 새 전역 원본을 스스로 grant할 수 없다.
Team token은 default project에서도 grant가 있어야 원본 catalog/detail/samples/discovery·freeze를 사용할 수
있다. 운영자 default project만 기존 ungranted 원본 선택 호환성을 유지한다. Grant는 불변 원본 사용 범위이며
소유권 이전이나 상용 사용권이 아니다. nuScenes 연구 제한과 원본 lineage를 검증·보존하고 unknown은 unknown으로 남긴다.

Viewer의 `GET /project-recordings/{id}/download?project_id=...`는 원본 SHA·크기를 확인한 같은 열린
파일 descriptor를 스트리밍하고 `X-Content-SHA256`를 제공한다. UI의 **Download original MCAP**과
SDK `download_project_recording`은 byte size·hash를 다시 확인한다. SDK는 응답의 임의 URL을 따르지 않고
선택한 ID·project를 같은 API에 보내며 실패 시 기존 destination을 보존한다.
이것을 기존 플랫폼 전체의 다중 tenant 격리 완료로 보고하지 않는다.

## 형식별 Experiment Verification Plan

`/api/v1/experiment-verification-plans`는 기존 detection 계획과 별도 additive 도메인이다.
검증 형식, 허용하는 실제 fidelity, metric의 단위·operator·threshold·필요 평가 비율을 고정하고
계획 변경은 새 개정으로 남긴다. 평가 요청은 계획 개정과 중복 없는 실행 ID 목록을 고정한다.
Reviewer가 정상 API를 통해 평가한다. 결과는 불변 receipt와 계획 hash·실행 receipt hash를 보존한다.

Object observer와 reference planning replay의 collision, 거리/TTC, lane departure, 속도·가속·jerk,
goal 및 지원되는 궤적 오차를 형식별로 평가한다. 단위가 다른 지표는 합산하지 않는다.
Planning 평가는 UI의 짧은 preview가 아닌 hash 검증된 전체 native NDJSON trace를 사용한다.
초기 가속/jerk 표본·drop·미지원 lane geometry는 원래 표본 분모와 미평가 이유를 남긴다.

평가 목록은 요청된 execution 분모를 유지하면서 `missing_input`, `unsupported`,
`infrastructure_failure`, `unevaluated`, `sut_failure`, `passed`, `context_only`를 구분한다.
같은 입력의 여러 실행은 독립적인 scene 다양성으로 세지 않는다. 모든 기준이 평가되고 충족될 때만
그 제한된 계획 기준을 `passed`로 표시한다.
센서·ISP·학습형 주행 receipt는 현재 이 계획에서 `context_only`이며 물리 성능·충돌 회피·안전성을
통과 처리하는 evaluator는 제공하지 않는다.

## 검증 범위

`backend/tests/test_experiment_core.py`와 `test_experiment_verification.py`는 authored handler로
API·권한·파일 hash·큐·실패 분류·불변 개정·전체 trace 사용을 검증한다. Simulator 또는 실제 RAW 인수와
구분해야 한다. 앞선 schema17 인수는 운영 DB를 변경하지 않았으며 schema16의 복원 사본에서
기존 job ID·submission sequence·key·payload·event·FK를 보존하는 migration을 검사한다.
현재 schema18은 project recording grant와 retry intent만 추가하며 `test_project_recordings.py`의
schema17 냉복사본 검사에서 기존 행·FK·원본 bytes를 보존한다. Authored MCAP의 정상 browser source
picker·다운로드·clock preview·freeze·reference planning 실행은 계약/제품 연결 근거다.
실제 도로 입력과 새 CameraScene의 인수 결과는 [입력·렌즈 확장 인수](input-and-lens-expansion-acceptance-20261007.md)로 구분한다.

CameraE2E `camera-e2e-lens-sensor-scene`은 default Project에 명시적으로 연결한 카탈로그에서 렌즈2×저장센서2의
같은 작성 입력과 seed·네 RAW 계획을 고정한다. Native RAW 네 건이 실제 완료된 뒤 같은 원본과 고정 ISP에
묶인 파생 quality 계획을 실행한다. API와 정상 화면의 새 제품 실험 두 건(RAW8·quality2), API artifact28개
byte/SHA·봉인 receipt, 정상 UI 여섯 업무를 인수했다. 앞선 parent closure와 derivative 다운로드404의
실패 receipt·native 원본은 후속 완료 실행으로 바꾸지 않는다.

이 네 실행의 센서 계획은 분모4·`infrastructure_failure2 / context_only2 / passed0 / sut_failure0`,
verdict `not_evaluated`다. 정상 Validation의 기존 계획/평가 조회와 assessment SHA 다운로드를 읽기 전용으로
확인했다. 중앙32×32 geometric lens·estimated sensor 진단이며 physical/road/ranking evaluator는 제공하지 않는다.
550nm 좌표는 broadband PSF footprint 또는 post-ISP label GT가 아니다. 다른 Project의 카탈로그 연결은 거절하며,
같은 작성 장면의 다른 seed를 독립적인 scene 다양성으로 세지 않는다. [계약과 한계](camera-lens-sensor-experiments.md).

## 하나의 내부 실행 진입점

이번 입력 확장에는 `frontend/dist`와 기존8020을 보존하기 위해 별도 build를 사용한다.

```sh
cd frontend
npm run build -- --outDir ../.runtime/road-log-onboarding-20261007/ui-build
```

저장소 루트에서 아래 명령으로 신규 또는 종료된 인수 workspace를 점검한다.
이 점검은 선택한 DB를 사본에서 읽으며 디렉터리·DB·서비스를 만들지 않는다.

```sh
.venv/bin/python scripts/internal_platform.py --workspace .runtime/road-log-onboarding-20261007/platform --state .runtime/road-log-onboarding-20261007/access --ui .runtime/road-log-onboarding-20261007/ui-build --port 8021 --check
```

같은 명령에서 `--check`를 제거하면 loopback API·기존 queue worker·빌드된 화면을
`http://127.0.0.1:8021`에서 함께 실행한다. 이미 실행 중인 포트는 거절하며 그 소유 프로세스를 중단하지 않는다.
화면의 Workspace settings에서 `.runtime/road-log-onboarding-20261007/access/api-token`의 값을 입력한다. Token과 access state·로그는
소유자만 읽는 private 파일이며 실행 명령과 출력에는 token 값을 넣지 않는다.
Ctrl-C는 이 실행기가 소유한 worker를 먼저 drain한 뒤 API를 종료한다. 미확인 외부 실행이나
quarantine을 자동 해제하지 않는다.

실행 직전에 선택한 build의 전체 파일 hash·크기·경로를 검사하고 private `ui-<instance_id>`에 복사한다.
`internal-ui-snapshot/1` manifest와 snapshot 경로/hash를 access state에 보존하며 API는 그 실행별 사본을
제공한다. 이후 source build나 `frontend/dist`가 바뀌어도 실행 중 UI는 바뀌지 않는다. Symlink와 복사 중
drift는 시작 전에 거절한다. 이는 기존8020 프로세스를 새 snapshot 방식으로 묵시적으로 바꾼다는 뜻이 아니다.

실행은 명시적으로 선택한 신규 또는 offline schema18 workspace만 허용한다. 기존 schema17 이하를
자동 갱신하지 않으며, 필요한 경우 `--restore-backup <검증된 cold backup>`과 **새 workspace 경로**를
함께 지정하여 복원 사본만 additive migration한다. 기존 queued 작업은 검토 후 `--resume-queued`를
명시해야 재개한다. 운영 `.data` 경로는 이 진입점에서 제외한다.
앞선 `.runtime/internal-platform/data`와8020/schema17 인수 환경은 별도로 보존한다.

기본 실행은 기존 환경의 엔진 설정을 전달하지 않는다. 운영자가 설정한
`AXIOM_ESMINI_*`, `AXIOM_CARLA_*`, `AXIOM_CAMERA_E2E_*`, `AXIOM_CAMERA_SCENE_*`, `AXIOM_VAD_NATIVE_*` 등을 사용할 때
`--enable-configured-engines`를 명시한다. 이 옵션은 engine 설정과 선택적 adapter source를 전달하며,
simulator 부팅·다운로드·기존 프로세스 중단을 대신 수행하지 않는다.

`scripts/tests/test_internal_platform.py`는 신규 read-only 점검, 구버전 거절과 원본 보존,
검증된 cold backup의 새 사본 migration, UI snapshot의 hash·symlink/drift 거절,
실제 owned API/worker/static UI의 인증·origin 경계와
종료를 별도 임시 workspace에서 검사한다. 이 launcher check는 engine 실행 근거와 별도다.
