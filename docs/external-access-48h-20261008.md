# 외부 프로젝트 2일 운영 · 2026-10-08

사용자가 외부 서버를 이틀 열어 두도록 요청했다. 기존 Engineer 권한·비밀번호·고정 UI·
API 경로를 유지하고 종료 시각을 **2026-10-10 03:45:21 KST**로 설정했다. 개별 로그인
쿠키는 12시간이며, 서버 만료 전에는 같은 비밀번호로 다시 로그인할 수 있다.

이전 foreground gateway·터널이 종료되어 기존 주소는 유지할 수 없었다. 원래 설정과
프로세스 identity를 보존하고 새 비밀번호 보호 HTTPS 주소를 발급했다. 현재 세션은
`.runtime/external-access-20261008-48h`이며 `external-access-current.json`이 가리킨다.
`Stop External Access.command`는 이 세션의 supervisor·gateway·터널·잠자기 방지만
정확한 PID·시작 시각·명령을 확인해 종료한다.

독립 supervisor는 `start_new_session=True`, 입력 DEVNULL, 소유자 전용 로그로 실행한다.
실행 도구가 종료된 후에도 parent PID 1로 유지되며 자식 gateway·터널·`caffeinate -is`
소유 관계를 실제 확인했다. 기간 제한 잠자기 방지의 idle/system assertion도 확인했다.
화면 켜짐이나 영구 전원 설정을 변경하지 않는다. 고정 종료 시각·신호·자식 실패에는
자신이 시작한 프로세스만 정리하고, 새 주소로 자동 재시작하지 않는다.

launchd GUI 등록을 먼저 시도했으나 supervisor 실행 이전 exit 78로 종료됐고 로그가
비어 정확한 원인은 확인하지 못했다. 해당 고유 job만 등록 해제한 후 이미 지속 중인
native API와 같은 독립 프로세스 방식으로 전환했다. 실패한 시도도 세션에 보존했다.

gateway·supervisor·종료 도구 계약 검사 **144개**를 통과했다. 최종 브라우저 검사
**7개**에서 로그인·기존 결과·봉인 다운로드·권한·작성 목록·모바일 메뉴와 폼을 확인했다.
이 기간 연장 검사는 새 실험·native 계산·GPU 작업을 제출하지 않는다.
최종 HTTPS 경계·배포 파일 검사 **71개**도 통과했다. 기존 8772 ISP 원본 목록 API의
종료도 확인해 같은 소스 369개·환경·state로 복구했다. 기존 완료 작업 3개·모든 행과
blob·제품 영수증 5개·토큰과 구성 bytes를 보존하고 별도 8773 API는 변경하지 않았다.
현재 8021 호출자에 기존 확정 ISP 입력을 그대로 preview하는 POST 1건이 HTTP 200을
반환했으며, 원래 native plan hash와 고정 요청도 같았다. 이 preview는 새 실행이나
RAW/ISP 계산을 제출하지 않는다. API74134와 소유 worker74137의 독립 지속도 확인했다.
현재 접속·기존 서비스 복구·해시 근거는 [기계 판독 기록](validation/external-access-48h-20261008.json)에 보존한다.

이는 임시 외부 접속 운영 설정이다. 48시간 연속 가동을 이미 관측한 근거가 아니며,
Mac 전원과 인터넷 연결이 유지되어야 한다. 다른 프로젝트·관리자·Reviewer 판정·legacy
도메인을 추가 공개하거나 실제 모델·센서·안전성 검증을 완료한 것은 아니다.
[접속·종료 방법](external-access.md).
