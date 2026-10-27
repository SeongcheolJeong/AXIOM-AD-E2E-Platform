# CameraE2E 렌즈 × 센서 중앙 진단 실험

2026-10-07. 내부 플랫폼의 **실제 기하 광학 계산 연결**이며 물리 카메라 검증이나
도로 장면·라벨 대응 인수는 아니다. 기존 CameraE2E 8772와 보존 RAW의 ISP A/B는 보존한다.

## 사용 업무

Experiments → Sensor simulation / capture → `camera-e2e-lens-sensor-scene`를 선택한다.
템플릿을 불러와 서로 다른 Lens DB 개정 두 개와 저장 센서 실행 두 개, 공통 slanted-edge
입력, 노출·휘도·noise·seed를 선택한다. 제한 사항 확인 → Preview workload →
Save frozen experiment → Submit execution으로 정상 제품 큐에서 제출한다.

한 실험은 `L1S1/L1S2/L2S1/L2S2` RAW 네 건과 고정 ISP v2 평가 한 건이다.
네 RAW 계획을 **첫 dispatch 전에 모두 고정·재검사**한다. 실제 네 원본 RAW가 나온 뒤에는
그 원본 결과·RAW 해시, 렌즈·저장 센서 identity, ISP와 실행 환경에 묶인 파생 평가 계획을
고정한다. 실행 완료와 진단 지표의 평가 가능 여부를 구분한다.

현재 default project에 명시적으로 연결한 원본 카탈로그만 지원한다. Viewer/Reviewer는
조회, Engineer/Admin은 작성·실행한다. 다른 프로젝트의 연결과 detector/safety 평가를
이 어댑터에 임의로 붙이면 preflight가 거절한다.

## 결과 화면과 원본

- 저장한 native RAW int32 NPY와 결과 JSON, 네 RAW의 원래 external child ID·accepted plan hash.
- 고정 ISP의 원본 float32 RGB와 valid mask NPZ, 실제 ISP 평가·조건·출처 JSON.
- 450/550/650 nm의 실제 모델 pixel-center 좌표 float64 NPZ와 배열 해시.
- 중앙 공통 각도 격자가 평가 가능한 경우 동일 좌표의 native antialias/barycentric
  RGB와 valid masks, 렌즈만/센서만 변경한 진단 차이. 불가능하면 미평가 사유.
- Native 이미지 갤러리와 550 nm 좌표 overlay·개별 pixel row/column 조회.
  PNG는 원래 RGB에 고정 `clip(0,1) → uint8`만 적용한 보기다. 자동 gain이나
  만들어낸 영상으로 대체하지 않는다. 각 이미지·다운로드는 제품 API의 SHA-256을 확인한다.

Native cycles/output-pixel은 서로 다른 output sampling 사이에서 단순 차감하지 않는다.
공통 각도 진단의 단위는 cycles/degree_object_angle이다. 필요 입력이나 valid support가
부족하면 해당 비교를 통과/실패로 바꾸지 않고 미평가로 유지한다.

## 버전 계약

제품 입력 `camera-e2e-lens-scene-spec/1`은 `lenses`, `sensors`, `scene`, `profile`,
`acknowledge_simulation_limits`와 `schema_version`을 요구한다. Seed는 공통 Experiment의
seed이며 네 native RAW specification에 실제로 전달한다. 고정 native default ISP v2만
지원한다. 기본 native crop은 32×32, source sampling은 256×256이며 full camera 영상이 아니다.

공통 API:

- `GET /api/v1/camera-bridge/scene-catalog?project_id=default`
- `POST /api/v1/experiments/preview`, `POST /api/v1/experiments`
- `POST /api/v1/experiments/{id}/executions`
- 기존 execution 조회·sealed receipt·artifact 다운로드와 SDK Experiment 메서드.

Optional adapter는 `AXIOM_CAMERA_SCENE_URL`을 별도로 읽는다. 기존
`AXIOM_CAMERA_E2E_URL`과 기존 ISP handler의 pin을 바꾸지 않는다. Core에 optics/engine
import를 넣지 않는다. Input은 네 native 계획, 원본 scene/lens/saved-sensor/ISP/seed,
native 전체 소스·패키지 환경 hash, optional connector 소스 hash와 서비스 origin hash를 보존한다.

새 **분리된** CameraE2E 서비스의 계약:

- `POST /api/v1/lens-sensor-raw/plan {specification}`
- `POST /api/v1/lens-sensor-raw/planned {specification, plan_hash}`
- `POST /api/v1/lens-sensor-quality-studies/plan {specification}`
- `POST /api/v1/lens-sensor-quality-studies/planned {specification, plan_hash}`
- `GET /api/v1/lens-sensor-quality-studies/{id}/diagnostic-export`
- `GET /api/v1/lens-sensor-quality-studies/{id}/diagnostic-artifacts/{artifact_id}`

Plan 응답은 schema, `plan_hash`, 정확한 `frozen_request`, 제한된 output contract이다.
Planned submit은 native prepare를 다시 계산해 hash가 다르면 **dispatch 이전 HTTP409**를
반환한다. 지원 범위는 v5 dense common angular slanted edge와 quality-study v2다.
Returned child ID는 응답의 내용 검증보다 먼저 진단 receipt에 남긴다. 응답/timeout이
불확실하면 성공·cleanup을 추정하지 않고 원래 실행을 보존한다.
Diagnostic derivative는 기존 general artifact-owner index를 느슨하게 만들지 않고,
검증한 해당 quality job의 exact export에 포함된 ID·경로·SHA·byte size만 다운로드한다.

## 분리 서비스 재현

`integrations/camera-e2e/prepare_native_scene.py`는 CameraE2E의 dirty source를 새 목적지에
복사하고 별도 plan extension과 원래 MIT LICENSE를 보존한다. 존재하는 목적지를
덮어쓰지 않는다. Catalog와 upstream snapshot은 원래 workspace의 read-only,
hash-pinned input이다. 별도 native state/worker만 생성한다. Connector 서비스는 read-only
catalog/result/artifact 조회와 네 plan/planned POST만 허용하여 원래 catalog 작성 API를 차단한다.

```sh
.venv/bin/python integrations/camera-e2e/prepare_native_scene.py \
  --source /home/axiom-user/Documents/CameraE2E \
  --destination /absolute/new/native-scene-runtime
PYTHONPATH=/absolute/new/native-scene-runtime/src \
  /home/axiom-user/Documents/CameraE2E/.venv-platform/bin/python \
  /absolute/new/native-scene-runtime/serve.py \
  --workspace /home/axiom-user/Documents/CameraE2E \
  --state-dir /absolute/new/native-scene-runtime/state --port 8773
```

위 runtime은 local development용이다. 운영 서비스의 수정·재시작이나 외부 공개 배포를
이 작업이 승인하지 않는다. Source가 바뀌면 새 pin과 새 계획을 사용한다. 기존 실행의
소스와 실패 receipt를 지우거나 새 성공 결과로 덮어쓰지 않는다.

## 실제 근거와 검증 한계

초기 product job `job_a5a382c31d394f999d1a0e607352612c`는 native RAW 네 건과
ISP 한 건이 실제 성공한 후 adapter의 parent RAW artifact-closure 검사에서 실패했다.
그 실패와 원본 child 결과를 보존하고, 이미 받은 원본 parent artifact를 평가의 근거 closure에
포함하도록 수정했다. 서로 다른 sampling/격자 검사를 약하게 만들지 않았다.

초기 L1 wide-angle × L2 telephoto 조합에서는 공통 입력 sample density 조건이 부족해
common angular 진단을 **미평가**로 남겼다. Native 출력이 존재한다는 이유로 이를
평가 완료나 성능 통과로 바꾸지 않는다. 기본 L2를 별도 intermediate 개정으로 선택하는
다음 인수는 새 계획·새 실행이며 초기 실패와 미평가를 없애지 않는다.

초기 actual native terminal·artifact hash 근거:
`.runtime/camera-native-scene-20261007/first-native-terminal-evidence.json`.
두 번째 새 source pin의 product job `job_1ad46755580f44cc827d48d54c92ad6e`에서는
intermediate 렌즈 조합의 실제 공통 각도 진단과 native PNG 8장이 생성됐지만,
새 derivative blob의 일반 HTTP 다운로드가 등록된 owner를 찾지 못해 404로 실패했다.
그 실패와 native 계산을 `.runtime/camera-native-scene-20261007/second-native-terminal-evidence.json`에
별도로 보존했다. 위 quality-job-bound 다운로드 계약을 추가했으며, general artifact 조회나
내용 해시 검사를 약하게 만들어 실패를 우회하지 않았다.
Adapter contract tests와 native endpoint mocked tests는 실제 optics 실행 근거와 별도다.

최종 source pin으로 새 계획·새 실행을 만든 제품 API 인수
`job_6cf0c1935ce14e61b5c9696a503a2709` (seed42)는 RAW 네 건과 quality 한 건을
모두 실제 완료했고, 공통 각도 격자가 평가 가능한 결과를 보존했다. 제품 API에서
원본·파생 artifact 28개의 byte size와 SHA-256, sealed receipt SHA를 확인했다.
실행 summary는 66,755B이며 기존 1MiB 제한을 늘리지 않았다. 전체 native 결과 JSON,
배열, mask, 좌표와 이미지는 원본 artifact로 내려받는다.

이어 seed43의 별도 새 실험을 **정상 Chrome 개발 테스트 화면**에서 작성·실행했다.
`job_91152b7d9a4f4a439911123a73539bc0`는 Workspace 인증 → catalog/form 편집과 같은
DTO 확인 → 네 계획 preview → 동결 저장 → 실제 RAW4+quality1 큐 완료 → 이미지·550nm
좌표 조회 → 좌표 원본 JSON·sealed receipt SHA 다운로드의 여섯 업무 검사를 통과했다.
이 두 성공은 앞선 실패 두 건과 미평가 상태를 바꾸거나 지우지 않는다.

인수 근거는 다음과 같다.

- `.runtime/camera-native-scene-20261007/product-common-grid-v3-acceptance.json`
- `.runtime/camera-native-scene-20261007/lens-scene-browser.json`
- `.runtime/camera-native-scene-20261007/lens-scene-author.png`
- `.runtime/camera-native-scene-20261007/lens-scene-native-evidence.png`
- `.runtime/camera-native-scene-20261007/final-native-terminal-evidence.json`

마지막 native 확인은 worker running, 활성 RAW/quality 작업 0건이었다. 별도 native state에는
앞선 실패의 완료 child까지 포함해 RAW16/quality4를 보존했다. 성공 제품 실험 두 건의
실제 계산은 RAW8/quality2이며 mocked endpoint 실행과 구분한다. Adapter/retained contract
42검사와 native endpoint mocked 5검사를 통과했고 통합 UI build·실제 browser 인수를 수행했다.
실제 실행 native environment SHA는
`4a1b43a8c4888bf2e63b609fb073a8a0e2ec9bf0e30fa125df4795f2fcfe46aa`,
connector `lens_scene.py` SHA는
`1a2a8cd0c6b850183dec001b78b07af327c9c4341909111fb739272ad160b392`다.

현재 32×32 중앙 native crop은 전체 카메라 영상이 아니다. 인수한 L2S2 고정 ISP
PNG의 RGB byte 범위는 R0–3/G0–5/B1–2로 매우 어둡다. 표시를 밝게 만들려고 자동 gain이나
정규화를 적용하지 않았다. 사용자는 원래 float RGB·RAW와 exposure/휘도 계획을 함께
조사할 수 있지만, 이 영상이나 central MTF 숫자만으로 실제 제품의 영상 품질을 판단할 수 없다.

현재 핵심 한계는 unknown lens spectral transmission/relative illumination의 unity 가정,
diffraction이 없는 geometric PSF, 검증되지 않은 pixel integration/CRA/scene halo,
estimated saved sensor response, single-frame 지표다. 550 nm point center는 broadband
PSF footprint나 post-ISP label ground truth가 아니다. 실제 road scene, perception AP,
측정 센서 정확도, physical MTF, 제품 순위, 안전 통과로 확대하지 않는다.

## 라이선스 경계

CameraE2E 코드의 MIT LICENSE와 lens patent prescription/PSF assets, sensor source reports,
upstream ISETCam snapshot 및 수치 라이브러리의 권리·출처는 서로 별개다. 이 local 실행이
각 asset/database의 상용 재배포·상용 제품 검증 권한을 자동으로 확보하지 않는다.
새 analytic slanted edge와 이 UI는 자체 작성했다. 경쟁사의 proprietary UI·코드·자료를
복사하지 않았다. 정식 배포 전에는 실제 구성에 대한 개별 license/source inventory가 필요하다.
