# 외부 프로젝트 수정·실행 인수 · 2026-10-07

최종 접속·브라우저 확인: 2026-10-08 KST. 접속 만료: 2026-10-08 23:19:12 KST.

사용자가 조회 전용에서 **수정과 실행도 허용**하도록 범위를 변경했다. 비밀번호로
임시 HTTPS 주소에 로그인하면 공유 `default` 프로젝트의 Engineer 권한으로 시나리오
개정을 저장하고, 시계열 입력·ReplaySpec·실험을 작성해 공통 큐에 제출할 수 있다.
실험 검증 계획 편집과 기존 결과·봉인 영수증 다운로드도 같은 세션을 사용한다.
브라우저에 서버 API 토큰을 입력하거나 전달하지 않는다.

## 실제 완료한 업무

외부 HTTPS의 정상 화면에서 새 시나리오를 만들고 `ego.speed_mps`를 10에서 10.75로
수정한 개정 2를 저장했다. 개정 1의 내용과 hash는 보존했다. 별도의 합성 입력
21프레임을 고정해 ReplaySpec → 입력 점검 → 작업량 preview → 불변 실험 저장 →
공통 큐 실행 → 실제 feedback 조사 → 영수증·trace 다운로드까지 완료했다.
신규 제출은 CPU reference planning 시험 **1건**이다.

- Scenario: `scenario2_62b5eef1e5fa4d51b02ee8681bccac3c`, 개정 2.
- Execution: `job_0eca4de73c4145c2a3a1287e97d326d8`, `completed`.
- Adapter/fidelity: `reference-planning-replay/1`, `authored_planar_reference`, seed 42.
- 봉인 영수증 SHA-256: `45c1fbac0ff829ec7f956fca81634d2bd63c53bc2b8114593450823ff688ed77`.
- 21 tick의 hash와 20개 제어→다음 자차 상태 연결을 원본 trace에서 확인했다.

기존 native lens 결과도 외부 화면에서 L2S2·row 20·column 22를 선택하고 좌표 자료의
원래 SHA와 32×32 영상을 확인했다. 저장된 평가의 분모 4에는 미평가와 인프라 실패를
그대로 표시하며, Engineer가 Reviewer 평가를 새로 생성하지 못하는 경계도 유지했다.

## 접속 경계와 실행 환경

현재 프로필은 감사한 GET 37개와 POST 17개만 사용한다. 실제 토큰의 프로젝트가
`default`이고 read/execute만 허용된 Engineer인지 시작 시 확인한다. 다른 프로젝트,
관리자·토큰·입력 원본 grant 관리, legacy API, Reviewer 평가 생성, 임의 파일·소스·환경
파일 공개는 차단한다. 쓰기는 같은 HTTPS Origin과 제한된 JSON 형식을 요구하고
Idempotency-Key를 원래 API에 전달한다. gateway는 실행을 자동 재시도하지 않는다.

원래 8020/8021 API와 로컬 UI, 8772 CameraE2E 및 Linux CARLA를 교체하지 않았다.
외부 UI는 별도 production 빌드이며 기존 `frontend/dist`를 보존했다.
작성용 렌즈 목록 조회에서 8773 native API listener가 없음을 발견했다. 작업이 없는
기존 idle worker의 정확한 identity와 lock 소유권을 확인해 정상 종료한 뒤 같은 source,
환경, state로 API와 worker를 복구했다. 기존 RAW16 16개·quality 4개 작업 행과 모든
blob bytes, 제품의 완료 2개·실패 2개 영수증은 보존했다. 목록은 lens 4개·sensor 61개를
다시 반환한다. 이번 외부 인수에서 새 native 렌즈 계산이나 GPU 작업은 제출하지 않았다.

## 검증과 한계

gateway·종료 도구 계약 검사 115개와 HTTPS 경계·배포 파일 검사 71개를 통과했다.
초기 조회 프로필의 HTTPS 검사 103개에는 기존 결과 파일 28개의 원본 bytes와 SHA
확인이 포함되며, 현재 Engineer 검사와 범위가 겹치므로 합산하지 않는다.
최종 브라우저와 배포 증거는 [기계 판독 기록](validation/external-workspace-20261007.json)에 연결한다.

최종 production 빌드의 정상 외부 브라우저 검사 7개를 통과했다. 로그인·기존 native
결과·영수증 다운로드·API 토큰 없는 설정·저장된 검증 계획·센서 작성 목록과 템플릿을
확인했다. 360/390/768/1440px에서 폼 너비 초과가 없으며 모바일 메뉴 열기·닫기도
동작한다. 긴 native 식별자와 select의 최소 너비가 만든 초기 494px 넘침은 grid track,
폼 너비와 줄바꿈 규칙을 수정해 해소했다. 최종 조회 검사에서는 API 쓰기와 브라우저
오류가 모두 0개다. 수정·실행 인수는 앞서 완료한 1건이며 화면 점검을 위해 재제출하지 않았다.

합성 reference planning의 실행 완료는 실제 E2E 모델, CARLA 센서, 물리 카메라나
주행 안전성의 검증이 아니다. 주변 객체는 비반응 원본 track이며 camera feedback을
지원하지 않는다. 마지막 tick에는 다음 시간 구간이 없어 `next_ego`가 없지만 기존
trace의 `control_application` 문구가 open-loop로 남는 표시 한계가 있다. 앞선 20개
제어의 실제 reference 자차 상태 적용은 확인했으며, 이번 접속 작업에서 backend를
바꾸거나 실행 결과를 다시 쓰지 않았다. 이전 native API 종료 원인도 확인하지 못했다.

접속은 생성 후 24시간에 만료되는 임시 터널이다. Mac·인터넷·API·gateway·터널이
실행 중이어야 한다. 팀 상시 운영 배포나 경쟁사 전체 기능 동등성·상용 release 인수는
완료하지 않았다. 비밀번호·API 토큰·세션 쿠키는 이 문서와 기계 판독 기록에 넣지 않는다.
[접속과 종료 방법](external-access.md).
