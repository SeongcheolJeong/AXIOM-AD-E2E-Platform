# 외부 프로젝트 접속

2026-10-07 사용자가 **수정과 실행도 허용**했습니다. 현재 공유한 `default` 프로젝트에는
Engineer 권한을 연결합니다. 외부 접속은 별도의 임시 HTTPS 터널과 비밀번호 로그인
gateway를 거칩니다. 서버의 API 토큰은 브라우저에 전달하지 않습니다.

처음 선택한 조회 전용 프로필도 유지합니다. 프로필 전환은 로컬 운영자가 명시적으로
설정하고 gateway를 다시 시작해야 하며, 화면의 토큰 입력으로 권한을 높일 수 없습니다.

## 접속과 종료

**2026-10-08 Viewer UX 반영:** Object Sim·Sensor Sim·결과 Viewer 메뉴와 작업 시작 화면을
검증한 별도 빌드로 교체했다. 임시 접속 주소는 바뀌었으며 현재 pointer가 새 세션을 가리킨다.
접속 비밀번호·Engineer 권한·API 범위와 기존 종료 시각은 보존했다.
[실제 정상 UI 업무와 근거](viewer-workspace.md).

현재 세션의 주소와 별도 접속 비밀번호는 생성 시 사용자에게 전달합니다. 브라우저에서
주소를 열고 비밀번호로 로그인합니다. 접근 권한은 세션에 설정한 종료 시각에 만료되며,
Mac과 로컬 API, gateway, 터널 프로세스가 실행 중이어야 합니다. Mac이 잠들거나
인터넷 연결이 끊기면 접속할 수 없습니다. 이는 상시 운영 서버 배포가 아닙니다.

**2026-10-08의 2일 운영 요청:** 현재 세션은 `2026-10-10 03:45:21 KST`까지 허용합니다.
gateway는 명시적으로 설정한 최대 48시간의 종료 시각을 확인하며, 개별 로그인 쿠키는
최대 12시간이므로 이후에는 같은 비밀번호로 다시 로그인합니다. 기존 foreground
gateway·터널의 종료를 확인해 새 주소로 복구했습니다. 현재 주소와 프로세스 기록은
`.runtime/external-access-current.json`이 가리키는 세션에서 확인합니다.

`scripts/external_access_supervisor.py`는 독립 세션에서 gateway·터널·`caffeinate -is`를
직접 실행하고 고정 종료 시각에 자신이 만든 프로세스만 종료합니다. 실행 도구가 끝난
후 parent PID 1로 유지되는 것을 확인했습니다. 잠자기 방지는 같은 종료 시각까지이며
화면 켜짐을 강제하거나 영구 전원 설정을 변경하지 않습니다. Mac 전원·인터넷은 유지해야
합니다. 접속 프로세스가 실패하면 새 주소를 조용히 발급하지 않고 종료 상태를 기록합니다.
[2일 운영과 실제 확인](external-access-48h-20261008.md).

저장소 루트의 `Stop External Access.command`를 더블 클릭하면 현재 기록된 외부 접속
프로세스에만 종료를 요청합니다. 프로세스 ID뿐 아니라 시작 시각과 명령이 같은지
확인하므로, 종료된 프로세스의 ID를 다른 프로그램이 사용하면 신호를 보내지 않습니다.
로컬 API·개발 UI·Linux CARLA는 종료 대상이 아닙니다.
2일 세션에서는 supervisor를 먼저 종료하고, 기록된 gateway·터널·잠자기 방지도 해제합니다.

```bash
.venv/bin/python scripts/stop_external_access.py
```

## 제공 범위

- 공유 프로젝트의 Behavior scenario 개정, 고정 시계열·ReplaySpec, 실험과 검증 계획을 조회합니다.
- 시나리오 개정 저장, 입력 점검·고정, 실험 작성·실행·지원되는 취소와 검증 계획 편집을 허용합니다.
- 결과 영상·좌표·봉인 영수증 다운로드도 로그인 후에만 가능합니다.
- 현재 Engineer 프로필은 감사한 GET 37개와 POST 17개만 연결합니다. 관리자·토큰 관리,
  다른 프로젝트, Reviewer 평가 생성과 아직 프로젝트 소유권이 없는 legacy API는 차단합니다.
- 임의 파일 업로드·삭제·서버 설정 변경은 제공하지 않습니다. Driving 입력 가져오기도 기존에
  설정된 입력 루트 안의 검증된 작은 JSON만 허용하며 입력 루트 자체를 바꿀 수 없습니다.
- 엔진 연결 검사와 원격 실행 상태 probe는 조회 URL이어도 외부에서 차단합니다.
- 조회 전용 프로필에서는 모든 API 쓰기 요청과 외부 엔진 목록 조회를 차단합니다.
  Engineer 프로필은 작성에 필요한 고정 CameraE2E 목록 조회를 명시적으로 허용합니다.
  실제 엔진 실행은 기존 입력 계약·사전점검·불변 구성·작업 큐를 그대로 사용합니다.

gateway는 고정된 production 빌드만 제공합니다. Vite 개발 서버, 소스 코드,
`.runtime`, `.data`, 환경 파일, SSH와 CARLA 포트는 터널 대상이 아닙니다.
API는 준비 시 감사한 `/api/v1` 메서드와 경로에 한정하며 새 API는 자동 공개하지 않습니다.
쓰기에는 같은 HTTPS Origin과 JSON 형식을 요구하며 요청 크기는 8 MiB 이하입니다.
Idempotency-Key를 원래 API에 보존하고 gateway가 실행을 자동 재시도하지 않습니다.

## 구성과 검증

gateway는 `scripts/external_access_gateway.py`이며 다음 명령으로 실행합니다.
구성은 세션별 `.runtime/external-access-*/config.json`에 보관하고 파일 권한은 0600입니다.
비밀번호·세션 쿠키는 소스나 검증 기록에 저장하지 않습니다. 구성에는 임시 비밀번호가
있으므로 해당 파일을 공유하거나 커밋하지 않습니다.

```bash
.venv/bin/python scripts/external_access_gateway.py --config /absolute/path/config.json --port 8787
```

구성 필드는 `password`, `expires_at`(Unix seconds), `dist`(고정 빌드 절대 경로),
`upstream`(허용된 loopback API), `read_paths`(감사한 GET 경로 template 목록),
`access_mode`(`read_only` 또는 `project_engineer`), `write_paths`(명시한 method/path),
`upstream_token_file`(소유자 전용 0600 토큰 파일), `expected_project_id`입니다.
현재 upstream은 `http://127.0.0.1:8021`이고 예상 프로젝트는 `default`입니다.
시작 시 실제 토큰의 프로젝트·역할·조회/실행/검토/관리 권한이 설정과 정확히 일치해야 합니다.
bootstrap 운영자 토큰이나 다른 프로젝트 토큰으로 대체하지 않습니다.
기존 로컬 API 인증·DB·엔진 설정을 변경하지 않고 loopback에만 수신합니다.

터널은 Cloudflare 공식 release의 macOS ARM64 실행 파일을 사용하며 배포 asset의
SHA-256과 다운로드 bytes를 대조합니다. 버전·출처·해시, 빌드 파일 manifest,
프로세스 ID와 시작 identity, 실제 HTTP 검사 결과는 해당 세션 디렉터리에 기록합니다.
현재 세션 디렉터리는 `.runtime/external-access-current.json`에서 확인할 수 있습니다.
Engineer 세션의 실제 구성은 그 디렉터리의 `engineer-config.json`입니다.

Cloudflare Quick Tunnel은 시험·개발용 임시 주소이며 고정 주소·가용성 보장이 없습니다.
[공식 안내](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/).
상용 팀 접속에는 별도의 인증·조직 권한·고정 도메인·운영 배포와 인수 검증이 필요합니다.

2026-10-07 Engineer 프로필에서는 정상 외부 화면의 시나리오 개정 저장과 합성 CPU
reference planning 시험 1건의 제출·완료·봉인 영수증 다운로드를 확인했습니다.
gateway·종료 계약 검사 115개, HTTPS 경계·배포 파일 검사 71개를 통과했습니다.
2026-10-08 KST의 최종 브라우저 검사 7개에서는 모바일 메뉴와 360–1440px 폼도 확인했습니다.
기존 native 결과·평가 다운로드도 확인했습니다. 조회 프로필에서 진행한 기존 결과
28개 hash 검사는 별도의 역사적 근거이며 현재 검사와 합산하지 않습니다.
[실제 업무·복구·제한](external-access-acceptance-20261007.md) ·
[기계 판독 기록](validation/external-workspace-20261007.json).

2026-09-14 조회 프로필의 당시 확인: gateway/종료 도구 관련38개 시험, 외부 HTTPS 검사54개,
배포 파일32개의 원본 bytes 및 기존 PNG 다운로드를 확인했습니다. Safari와 Codex 내장
브라우저에서 로그인·기존 결과 표시를 확인했고 새 시뮬레이션은 실행하지 않았습니다.
브라우저의 일반 form POST가 Origin을 유지하도록 로그인 페이지에만
`Referrer-Policy: same-origin`을 사용합니다. 결과·API의 `no-referrer`, 엄격한 Origin
검사와 Secure 세션 쿠키는 유지합니다.
