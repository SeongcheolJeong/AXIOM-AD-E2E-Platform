# 로컬 평가판 패키지 인수 · 2026-09-28

## 사용자 업무

개발 서버 대신 빌드된 UI와 API를 단일 loopback 포트에서 실행하는 패키지를 만들었다.
신뢰된 단일 운영자용이며 필수 Bearer token, 명시적 ONNX/COCO worker, 시작 전 점검,
소유 프로세스 종료와 상태 기록을 제공한다. CARLA/연구 실행 경로는 추가하지 않았다.

패키지 사용자는 먼저 환경 점검을 실행하고, 앱·데이터·접근 상태를 서로 다른 폴더에 둔다.
코드와 화면은178개 파일의 SHA/크기로 고정하고 runtime dependency30개를 버전으로 고정했다.
기존 데이터·토큰·Git metadata·node_modules·CARLA adapter/native debugger 도구는 묶지 않았다.
실행은 기존 Python dependency 환경을 이용하며 Node/npm/Vite는 실행하지 않는다.

## 실제 패키지 인수

`browser-final-r2`는 공백이 포함된 별도 `relocated application` 폴더에 source/UI를 고정했다.
패키지 밖의 작업 디렉터리에서, Node가 없는 PATH로 실행했다. 모든 app/SDK/ONNX/COCO
module origin이 패키지 내부인지 자식 process에서 검사하고, 기존 editable checkout을
묵시적으로 불러오지 않도록 했다. Inherited CARLA URL과 DB URL은 사용하지 않았다.

실제 Chrome에서 다음 업무를 완료했다.

1. 빈 workspace와 필수 token 로그인, 현재 주소를 표시하는 Workspace settings.
2. 일반 API/worker의 실제 ONNX CPU 추론 및 COCO 평가.
3. 여섯 원본 이미지의 A/B 비교와 오류 증가 이미지 두 장 필터링.
4. 필터 후에도 전체 여섯 이미지가 남는 HTML export와 API 선언 SHA 일치.
5. Desktop와390px mobile 표시, JavaScript 오류0.

원본은 authored pixel-rule 이미지·모델·라벨이다. 고객 데이터/실도로 정확도 인수는 아니다.
미인증/잘못된token401, 외부Origin403, 허용하지 않은Host400, 앱source 파일 경로404,
CARLA not_configured도 실제 HTTP로 확인했다. HTML shell은 public이며 업무 API만
token으로 보호한다. 사용자별 권한/프로젝트 격리/OS sandbox 구현으로 확대하지 않는다.

이 패키지의178개 file descriptor와30개 dependency는 사용하도록 남긴
`axiom-local-pilot-r2` 패키지와 정확히 같다. 생성 시각에 따라 bundle manifest 전체 hash는
달라질 수 있지만 runtime 파일 bytes는 같다. 소스 디렉터리에 같은 이름의 파일이 있는
것만으로 동등하다고 판정하지 않았다.

## 기존 데이터 복원본으로 시작·종료·재시작

이전 cold backup을 새 `restored-data-r2`에 복원해747개 payload를 확인했다. 원본 `.data`는
수정하지 않았다. 동일 패키지를8192포트로 두 번 시작하고 정상 종료했다.
양쪽 모두Run74·Artifact349·Evaluation3을 조회하고4개 artifact 다운로드 hash를 확인했다.
기존 완전한 평가 두 개의 A/B 검토는comparable이며, 미인증 조회는401이었다.
중복 시작은occupied로 거절하고 기존 실행은 유지했다. 재시작 시token 파일은 재사용됐다.
시험 종료 뒤API/worker exit code0, forced stop 없음, 복구 필요 없음과 port 해제를 확인했다.

토큰 값은 출력/서비스 로그에 포함되지 않았다. 접근 상태는 별도 폴더이며 원본 데이터
백업에 섞이지 않는다. 기존 데이터749파일과 Git index bytes는 작업 전과 일치했다.
커밋·배포·원격 엔진 실행·다른 포트 소유자 종료는 하지 않았다.

## 실패 이력과 수정

첫 browser 명령은 config 상대경로가 잘못돼 실행 전에 실패했다. 경로를 수정했다.
초기 기능 browser 검사는 통과했지만 종료 receipt는failed였다. 설치된 uvicorn은 graceful
ASGI 종료 후 받은 signal을 원래 handler로 다시 전달하기 때문에 서버 exit code가−15가 됐다.
이를 성공으로 무시하지 않고 자식 wrapper가 종료 후 전달되는signal을 처리하도록 수정했다.
또한 복원 검사기가 API만 준비된 시점에 종료하지 않도록 launcher의API+worker ready를 기다린다.
이전실패로그/receipt는 그대로 두고 새r2의정상종료와재시작을확인했다.

## 검사 범위와 남은 gate

최종 관련Python 회귀 **254개 통과·기존 dependency 경고2개**다. Package변조/경로/토큰/
Origin/Host/port·backup·평가·perception·queue·SDK를 포함한다. 이전부분검사와합산하지않는다.
Frontendtypecheck/build와 별도 packagedChrome업무1건을 통과했다. 기존 개발 경로의
라벨/평가·A/B검토·인지3개브라우저회귀도 모두 통과했다(36.7초).
전체 플랫폼 Python/브라우저시험을 모두 다시 실행한 결과는 아니다.

현재 패키지는 **내부 배포 후보**다. 기존 의존성 환경 재사용을 새 장비의 clean install로
표시하지 않는다. 다른OS의binary dependency 설치, clean checkout/tag, 서명, 전체SBOM/
배포권리검토, 실제 고객수용, 사용자별접근제어와공유SaaS는미완료다. CARLA연구는계속분리한다.

## 산출물

사용 패키지: `.runtime/local-pilot-20260928/axiom-local-pilot-r2/`.
최종 browser: `browser-final-r2.log`, 소유process receipt와 `browser-evidence/`.
복원/재시작: `restore-acceptance-r2.json`, `restored-access-r2/*.result.json`.
실패도 포함한 원본 로그와 고정 패키지는 같은 상위 디렉터리에 보존한다.
기계 판독 최종 결과는 `docs/validation/local-pilot-20260928.json`을 따른다.
