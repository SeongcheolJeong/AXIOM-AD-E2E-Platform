# 기록 입력과 렌즈 센서 확장 인수

2026-10-07. 정상 제품 화면에서 승인된 원본 선택·입력 점검·고정·재실행과 검증 계획의
미평가 근거 연결을 인수했다. 렌즈 두 개와 저장 센서 두 개의 중앙 모델 진단도 실제 계산과
정상 화면의 작성·실행·영상·좌표 조회·원본 다운로드를 완료했다.
이 확장은 [앞선 네 모듈 인수](four-module-expansion-acceptance-20261007.md)를 유지한다.
Applied 동등성·상용 출시·실측 센서 품질·주행 안전 인수를 뜻하지 않는다.
[기계 판독 근거와 봉인 해시](validation/input-and-lens-expansion-20261007.json).

## 사용자가 완료할 수 있는 업무

| 업무 | 정상 화면에서 가능한 흐름 | 근거 범위 |
| --- | --- | --- |
| 프로젝트 원본 사용 | 운영자 exact SHA grant → 허용된 원본 검색·채널·표본 조회 → 인증된 MCAP 다운로드 | 다른 프로젝트와 ungranted 원본 차단. 사용 권한은 상용 데이터 권한과 별도 |
| 기록 입력 준비 | 원래 ordinal·capture clock·pose·크기·누락 검사 → 동일 preview SHA로 freeze → ReplaySpec | 입력 준비 상태이며 calibration·인지 정확도 판정이 아님 |
| 실제 기록 재실행 | nuScenes 원본 기록을 변환한 MCAP → 일반 CPU queue → reference feedback·전체 trace·봉인 receipt | 기록 GT 객체, 선언한 자차 형상, 비반응형 주변 객체, 격리 비상업 연구 |
| 계획의 공백 확인 | 기존 완료 실행 → TTC 계획 평가 → 0/3와 부족 이유 조회 → 평가 receipt 다운로드 | `object_velocity_missing`, `not_evaluated`. 성능 실패 또는 안전 통과로 변환하지 않음 |
| 렌즈와 센서 진단 | Lens DB 두 개 × 저장 센서 두 개 → RAW 네 건 → 고정 ISP → 영상·좌표·공통 각도 격자 조사 | 실제 제품 실행과 UI 인수 완료. 중앙 32×32 모델 진단이며 물리 검증과 별도 |

API·SDK·agent catalog는 같은 `/api/v1` 계약을 사용한다.
[원본 입력 업무](recorded-input-onboarding.md), [nuScenes 변환](nuscenes-planning-logs.md),
[렌즈·센서 계약](camera-lens-sensor-experiments.md), [공통 실행·계획](experiment-contract.md)을 따른다.

## 접속과 실행 경계

새 주소는 **http://127.0.0.1:8021**이며 데이터는
`.runtime/road-log-onboarding-20261007/platform`에 있다. `Workspace settings`에
`.runtime/road-log-onboarding-20261007/access/api-token`의 값을 입력한다.
이는 로컬 API 토큰이다. 토큰 값은 문서·URL에 저장하지 않는다.

```sh
.venv/bin/python .runtime/road-log-onboarding-20261007/start-api.py
```

이 private wrapper는 명시적 엔진 설정과 격리 production UI build를 사용하는
`scripts/internal_platform.py`를 실행한다. 이미 실행 중인 포트를 중복으로 시작하지 않는다.
Lens 연결은 별도로 소유한 CameraE2E 사본 `127.0.0.1:8773`을 사용한다.
Launcher가 외부 엔진을 자동 설치·시작하거나 일반 운영 DB를 이관하지 않는다.

기존 8020/schema17, CameraE2E 8772, 원격 기존 CARLA와 운영 `.data`는 보존했다.
새 source18 작업공간은 이미 인수한 격리 연구 source18의 냉백업을 새 목적지에 복원했다.
8파일·6,634,286 bytes의 inventory와 원본 bytes를 확인했다. Schema17→18의 additive
migration 검사는 별도 사본에서 수행하며 기존 행·queue·FK를 유지한다.

Launcher는 실행별 private UI snapshot과 파일 manifest를 보존한다. 이후 development build가
실행 중 화면을 바꾸지 않는다. 종료된 TCP 연결의 TIME_WAIT를 살아 있는 owner로 오인하는
재시작 검사도 수정했고, 실제 live listener 차단과 종료된 연결의 재사용을 구분해 검사했다.

## 실제 원본 기록과 검증 계획

원본 nuScenes archive·metadata·CAN 기록의 hash와 original row lineage를 유지한 converter가
세 시점의 MCAP를 생성했다. MCAP는 원래 nuScenes transport 기록이 아니라 명시적 파생 입력이다.
Original capture time을 derived log/publish clock으로 복사했음을 보존하고, 원래 transport time을
측정한 것으로 표시하지 않는다. [전체 원본·변환·제품 근거](validation/nuscenes-planning-product-20261007.json).

객체 수는 **78/86/94**, 이전·현재 annotation으로 유도한 속도는 **67/77/85**, 속도가 없는 객체는
**11/9/9**다. 없는 속도를 zero나 미래 annotation으로 채우지 않는다. 원래 ego pose와 과거 CAN의
측정 시각·age를 보존했다. 자차 4.5×1.9 m와 zero origin offset은 선언한 reference 형상이며
원래 차량의 실측값이 아니다. Annotation은 sensor detection이 아니다.

동일 origin의 정상 화면에서 원본 선택·MCAP byte/SHA 확인·exact 세 ordinal·입력 점검·
scene-sequence/2 freeze·새 ReplaySpec·일반 queue·feedback SVG·봉인 receipt 다운로드를 확인했다.
이 원본의 기준 feedback 궤적 차이는 최대 **0.1685227304 m**이며 실제 주행 정책 정확도 지표가 아니다.
개발 Vite origin에서 거절된 POST는 보존했고 최종 인수는 auth 완화 없이 같은 origin으로 수행했다.

같은 실행에 연결한 TTC 계획은 `minimum_ttc ≥2 s`, 필요 평가 비율 1을 명시한다.
2초는 작성한 진단 기준이다. 실제 결과는 **0/3 평가**, `object_velocity_missing`,
`not_evaluated`이며 요청 실행 1·미평가 1·통과 0·SUT 실패 0이다.
정상 Validation의 Experiment plans에서 이 상태와 assessment 다운로드 SHA를 확인했다.
전체 trace의 분모를 유지하며 화면 preview의 일부 표본만으로 평가하지 않는다.

최종 화면은 긴 원본 객체 ID를 기본으로 숨기고, 필요 시 짧은 라벨과 hover의 전체 ID를 제공한다.
세 시점은 각각 **20/78, 20/86, 20/94 표시**를 명시한다. 같은 원본 궤적·기하·전체 trace·평가 분모를
유지하고 bounded preview를 실제 객체 수로 오인하지 않는다. 최종 UI의 세 시점 재생과 기존 receipt
해시 불변은 새 실행 없이 읽기 전용으로 확인했다.

## 실제 렌즈 센서 계산과 화면

API seed42와 정상 화면 seed43의 **제품 실험 두 건**을 새 logical Experiment로 실행했다.
각 RAW 네 건과 quality 한 건, 합계 RAW8·quality2가 native에서 완료됐고 core도 `completed`다.
API 인수에서 원본·파생 artifact 28개의 byte size·SHA와 봉인 receipt를 확인했다.
정상 화면 인수는 Workspace 인증→카탈로그·같은 DTO 작성→네 계획 preview→불변 저장·명시적 제출→
실제 계산→영상·원래 좌표 JSON·픽셀 선택→원본·봉인 receipt SHA 다운로드의 여섯 업무 검사다.

32×32 원래 ISP 영상 네 장과 중앙 공통 격자의 영상 네 장, 450/550/650 nm의 원래 좌표 NPZ,
float RGB·valid mask·RAW를 보존한다. 실제 common grid는 19×19이며 550 nm point center에서
antialias·barycentric resampling한 모델 민감도다. Broadband PSF footprint, source halo의 물리
적합성이나 MTF estimator 수렴을 인수한 결과가 아니다. native 비교도 물리 MTF·제품 순위를 거절한다.

현재 L2S2의 fixed ISP 보기 byte는 R0–3/G0–5/B1–2로 매우 어둡다. 이를 보기 좋게 만들기 위해
자동 gain을 적용하지 않았다. 원래 float RGB·RAW·노출·센서 추정 조건을 내려받아 조사할 수 있다.
이 어두운 결과와 미확인 광학/센서 조건을 실제 카메라 품질이나 제품 간 우열로 해석하지 않는다.
두 seed의 같은 작성 장면을 독립적인 도로 조건 또는 장면 다양성으로 늘려 세지 않는다.

형식별 센서 계획에는 **이전 infrastructure failure2와 완료 context evidence2**를 모두 연결했다.
요청 실행 분모4·`context_only`2·`infrastructure_failure`2·통과0·SUT실패0·`not_evaluated`다.
계산 완료가 물리 성능 통과로 바뀌지 않으며 실패한 연결 이력도 다음 성공으로 지워지지 않는다.

## 발견한 렌즈 연결 결함과 보존한 실패

첫 제품 실행은 RAW 네 건과 ISP 한 건이 native에서 성공한 뒤 parent RAW artifact closure
검사에서 실패했다. 원래 실패 receipt와 native 결과를 보존하고 parent 원본도 closure에 포함하도록
수정했다. 첫 wide-angle/telephoto 조합의 공통 격자는 source density 부족으로 미평가다.
다른 렌즈 개정을 사용하는 후속 계산으로 이 미평가를 지우지 않는다.

후속 제품 실행에서는 네 RAW·고정 ISP·공통 격자 진단이 실제 완료됐으나 generated diagnostic
artifact의 일반 다운로드 경로가 404여서 실패했다. 등록된 원본 artifact 경로와 파생 artifact
소유권 경로가 다르다는 연결 결함이다. 완료한 exact quality job에 묶인 별도 다운로드 경로에서
원래 byte size·SHA를 검증하도록 수정했다. 임의 blob hash로 접근하는 경로를 열지 않는다.

두 실패 receipt와 `outcome_uncertain`은 그대로 남는다. 외부 다섯 child의 terminal 결과와
활성 작업 0을 확인한 운영자 reconciliation은 resource만 정리하며 과거 봉인 receipt를 바꾸지 않는다.
완료한 후속 시도는 새 source pin·새 frozen plan·새 logical Experiment·새 submit key를 사용했다.

Native 품질 결과가 좌표를 반복해 1 MiB summary 한도를 넘는 문제도 수정했다. 전체 정확한
JSON·NPZ·PNG는 artifact로 보존하고 product summary에는 slot 지표·hash·원본 연결만 담는다.
화면은 원래 correspondence JSON을 인증된 artifact API로 받아 byte/SHA를 검증한 뒤 표시한다.

## 검사와 남은 조건

Grant·SDK 16개, clock/preflight/planning 관련 검사와 launcher·형식별 계획·권한 계약 검사를
수행했다. 실제 원본 converter 13개, 최신 원본·SDK·planning 계약 묶음 60개,
렌즈 adapter 42개와 native endpoint mocked 5개도 통과했다. 집합은 겹치므로 합산하지 않는다.
Authored MCAP browser와 실제 원본 browser, 기존 TTC plan read-only browser,
렌즈 정상 화면 여섯 업무, TypeScript와 격리 production build를 별도 근거로 유지한다.
종료 연결 재시작·live listener 거절 및 실제 소유 API의 launcher 검사2개도 통과했다.
Mock endpoint 검사는 optics 인수가 아니다.

현재 연구 입력과 모델의 비상업 제한을 유지한다. Native geometric PSF와 estimated sensor의
unity radiometry·회절/실측 calibration 부재·중앙 소형 slanted edge 범위를 도로 영상·물리 MTF·
post-ISP 라벨 ground truth·perception AP·제품 순위로 확대하지 않는다.

다음 핵심 조건은 승인된 내부 실제 데이터의 반복 사용, 실제 정책 두 개의 동일 입력 A/B,
일반 도로 장면의 렌즈·센서·라벨 대응, 반응형 카메라 폐루프, 의미 검색·시간 사건 구간,
legacy 데이터 소유권과 실제 팀원 업무수용이다. 물리/실도로 상관성·장시간/부하/장애·
상용 데이터·모델·엔진·asset 권한과 출시 gate도 열린 상태다.
