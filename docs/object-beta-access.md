# Object Sim 초대형 베타 접속과 실행 한도

참가자마다 별도 프로젝트와 Engineer 자격을 사용한다. 브라우저는 기존 gateway의
비밀번호 로그인과 HttpOnly 세션을 사용하며 운영자 bearer token을 입력하지 않는다.
모든 참가자의 API와 worker는 **같은 workspace DB와 같은 지정 CARLA resource**를 사용한다.
서로 다른 DB를 같은 엔진에 연결하면 이 DB의 배타 잠금으로 보호할 수 없다.

## 2026-10-10 운영 확인과 남은 인수

별도 프로젝트3개와 loopback gateway8863/8864/8865를 준비하고 비밀번호 로그인·프로젝트 격리,
사용자별 초안 복원을 확인했다. 네 템플릿의 native A/B8건과 최종 브라우저 수정 native1건,
기준 A/B·관찰 저장·같은 개정 재실행 취소·재로그인을 포함한20항목을 확인했고 page error는0건이다.
취소 실행은 불확실한 상태를 남기지 않았으며 큐 한도·동시 제출은 backend 계약 검사로 확인했다.
gateway/provisioning131개 검사는 실제 모델·texture·CC0 고지의 인증된 제공과 크기·경로 제한을 포함한다.
비교 설정은 요약과 접힌 원문으로 정리했다. 최종 보존 결과 브라우저4항목에서 설정 영역650px
미만, GLB·texture 최초/새로고침 응답200, RGB A/B·북마크와 page error0을 확인했으며 새 제출은 없었다.

8GiB GPU에서 CARLA 한 인스턴스가 약5GiB를 사용하므로 발표용2000과 베타2160은 순차 운영한다.
13:11:29UTC에2160 종료·2000 복원과 베타 API 작성·저장·기존 결과 재생 모드 전환을 확인했다.
2000은 Town10HD_Opt·비동기·동적 객체0 상태이며 GPU 사용량은4,982/8,192MiB였다.
현재 새 native 실행은 의도적으로 비활성화했다. 발표용 엔진과 겹치지 않는 전용 베타 시간대에
handler와2160을 준비하거나 별도 GPU를 확보해야 한다. 공개 ingress나 초대 발송은 수행하지 않았다.
최종 빌드 적용 후 gateway8863/8864/8865의 `/login` 응답200을 확인했다. 이 재시작으로 이전
세션은 폐기됐으므로 다시 로그인해야 한다.

loaded NVIDIA kernel595.84와 설치된 userspace595.99.02의 불일치는 공식595.84 패키지를
사용자 task 폴더에 extract-only하고 해당 CARLA 프로세스에 private library/ICD 환경을 적용해
우회했다. Vulkan/NVML에서 RTX2070SUPER를 확인했다. 시스템 패키지·커널은 변경하지 않았으며,
재부팅 뒤에는 kernel 버전을 확인하기 전 이595.84 환경을 재사용하지 않는다.

## 프로젝트 누락 접속 수정 · 2026-10-10

로그인 주소의 화면·비교·시점 정보를 유지하고 설정된 프로젝트를 복원한다. Object Sim은
접근 가능한 프로젝트 조회가 끝난 뒤 화면을 열며, 메뉴 이동도 계정의 프로젝트를 유지한다.
선택 상자만 공유 프로젝트로 보이고 내부 요청은 `default`로 나가던 오류를 수정했다.
실제 사용자 브라우저에서 로그인·프로젝트 없는 링크·메뉴 복귀·새로고침과 기존 작성안 복원을
확인했다. 다른 프로젝트 요청 차단은 유지한다. 근거는
`.runtime/object-beta-20261010/project-access-fix.json`에 보존했다.

## 준비

기존 서비스와 공개 터널을 자동으로 변경하지 않는다. 운영자가 별도 API workspace,
프로젝트별 지도·차량·시나리오, production frontend를 준비한 뒤 다음 helper를 실행한다.
`resource-id`는 `/api/v1/experiments/capabilities`의 지정 Object Sim handler 값을 사용한다.
공유 엔진 port2000은 이 helper에서 거절한다.

```bash
.venv/bin/python scripts/object_beta_access.py prepare \
  --upstream http://127.0.0.1:8063 \
  --operator-token-file /absolute/private/operator-token \
  --dist /absolute/path/frontend/dist \
  --destination /absolute/private/new-beta-invites \
  --resource-id carla-city-2160 \
  --participant tester-a --existing-project tester-a=project_SEEDED_A \
  --participant tester-b --existing-project tester-b=project_SEEDED_B \
  --participant tester-c --existing-project tester-c=project_SEEDED_C
```

`--existing-project`가 없는 참가자는 빈 프로젝트를 생성한다. 해당 참가자가 시작하기 전에
Object Sim 카탈로그와 시나리오를 그 프로젝트에 준비해야 한다. 1–5명의 서로 다른 참가자,
서로 다른 전용 프로젝트를 지원한다. 이미 존재하는 출력 폴더는 덮어쓰지 않는다.
중간 실패 시 프로젝트·token 영수증을 남기므로 운영자가 확인하며 자동으로 재생성하지 않는다.

참가자별 폴더는0700, 설정·자격 파일은0600이다. `invitation-private.json`의 접속 비밀번호만
참가자의 지정 접속 주소와 함께 별도로 전달한다. gateway 설정, token, 전체 폴더는 공유하지 않는다.
helper는 초대를 발송하거나 서버를 시작하지 않는다.

```bash
.venv/bin/python scripts/object_beta_access.py serve \
  --config /absolute/private/new-beta-invites/tester-a/gateway.json --port 8863
```

서버는 loopback에만 수신한다. 외부 참가자에게 제공할 HTTPS ingress와 주소 배포는 별도
운영 작업이다. Secure·HttpOnly·SameSite=strict 쿠키를 유지한다. 기본 세션 설정은24시간,
최대48시간이며 gateway 재시작은 세션을 폐기한다. API token 폐기는 기존 프로젝트 token
revoke API로 수행한다. 저장된 결과와 프로젝트는 token 폐기만으로 삭제되지 않는다.

## API와 실행 동작

- `POST /api/v1/projects/{project_id}/object-beta-policy`: 운영자만 opt-in 설정. 입력은
  `{"enabled":true,"resource_id":"carla-city-2160"}`. 활성화·자원 변경 전 진행·대기 작업이 없어야 한다.
- `GET /api/v1/projects/{project_id}/object-beta-policy`: 해당 프로젝트 권한으로 실행 중·대기 수,
  남은 대기 자리, 제출 가능 여부를 조회한다.
- `GET /api/v1/team/whoami`의 `principal_id`: credential 값과 구별된 opaque identity로
  브라우저 작성 초안을 사용자별로 구분한다. 별도 token은 같은 사용자 label이어도 별도 identity다.
- `GET /api/v1/object-sim/executions/{execution_id}/behavior`: 해당 프로젝트의 보존 원본에서
  planner 상태와 native 응답 측정을 조회한다. gateway의 명시적 읽기 경로에 포함한다.

gateway의 production snapshot은 정상 GLB2·texture와 Kenney의 정해진 License/manifest 경로도
제공한다. 임의 JSON·TXT·소스·symlink는 공개하지 않으며 파일32MiB/전체100MiB 한도를 유지한다.
이 베타 wrapper의 코드·설정·static 빌드 변경은 gateway를 재시작해 반영한다. 재시작하면
참가자가 다시 로그인해야 한다. 이 wrapper는 SIGHUP 갱신을 지원하지 않으므로 보내지 않는다.

베타 프로젝트는 동시에 **실행 중1개와 대기2개**를 허용한다. 실행 중에는 `cancel_requested`도
포함되므로 엔진의 취소 확인 전 자리를 돌려주지 않는다. worker가 아직 시작하지 않았으면
대기2개가 한도이며, 첫 작업을 가져가면 대기 자리가 하나 생긴다. 대기 취소는 즉시 자리를 돌려준다.
제출은 기존 SQLite `BEGIN IMMEDIATE` 트랜잭션에서 판단하며 같은 idempotency key 재전송은
새 자리를 차지하지 않는다. 여러 브라우저나 같은 프로젝트의 추가 token도 이 한도를 공유한다.
한도가 찼으면 HTTP429와 대기 취소·다시 제출 안내를 반환한다.

기존 worker와 `EngineResourceRow` 잠금이 지정 CARLA 자원의 동시 실행을1개로 제한한다.
불확실한 엔진 종료는 기존 `reconcile_required`를 유지한다. 베타에서는 Object Sim과 지정
자원을 사용하는 실행만 제출할 수 있다. opt-in하지 않은 연구 프로젝트의 한도는 변경하지 않는다.

이 기능의 자동 검사는 인증·권한·동시 제출·취소·개정 보존 계약 검사다. 실제 엔진 실행,
외부 HTTPS 접속, 참가자80%의 도움 없는 업무 완료는 각각 별도의 인수 항목이다.
