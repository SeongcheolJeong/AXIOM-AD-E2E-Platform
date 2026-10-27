# 로컬 평가판 실행 패키지

**Verification Plan을 포함한 현재 패키지는 schema16**입니다. Schema15 이하 데이터는 백업한 뒤
별도 복원본으로 실행해 새 additive migration을 확인하세요. 구버전 앱과 새 API/worker를 혼용하지 않습니다.
이번 scene 검색은 로컬 RGB 특징 기준선이며 학습형 semantic 모델을 포함하지 않습니다.

자동 라벨링은 schema14에서 추가됐으며 현재 패키지에서도 유지됩니다.
원본 작업공간의 이관과 운영 배포를 자동으로 수행하지 않습니다.

2026-09-28. 신뢰된 단일 운영자가 쓰는 로컬 평가판이다. 일반 UI/API/SDK의 평가 업무와
기존 데이터 조회를 제공하되, 공개 서버·사용자별 권한·고객간 격리·OS sandbox를 제공하지 않는다.

## 사용자가 달라지는 점

API와 빌드된 화면을 **하나의 loopback 주소**에서 제공한다. Vite·Node·CARLA를 실행하지
않고 Python API와 평가 worker만 실행한다. 둘 다 준비된 뒤에 주소를 출력한다.
새 작업공간은 예제가 자동 생성되지 않는 빈 상태이며, ONNX 모델의 신뢰성/사용권과
데이터의 형식·권리·규모 확인은 운영자의 책임이다. 임의의 모델 파일을 안전하게
격리 실행하는 제품으로 취급하지 않는다.

모든 업무 API에는 필수 Bearer token을 요구한다. HTML shell·health·API 명세는 로컬에서
읽을 수 있지만 업무 데이터는 토큰 없이 읽거나 쓰지 못한다. 토큰은 별도 mode0700
접근 디렉터리의 mode0600 파일에 만들고 재사용하며, 로그나 URL에 출력하지 않는다.
Host와 브라우저 Origin을 제한한다. Loopback 제한은 사용자별 접근권한이나 sandbox가 아니다.
같은 OS 사용자의 다른 프로세스에 대한 보안 경계로 주장하지 않는다.

## 개발 환경에서 패키지 만들기

```bash
npm --prefix frontend run build
.venv/bin/python -B scripts/build_local_pilot.py NEW_OUTPUT_DIRECTORY
```

출력 경로는 새 디렉터리여야 한다. Backend/SDK/ONNX/COCO source와 빌드된 UI,
업무 예제와 출처 문서를 allowlist로 복사한다. `.data`, `.env`, 토큰, Git metadata,
node_modules와 CARLA adapter/native 연구 실행 도구는 포함하지 않는다.
Core에 존재하는 미등록 실험 component code까지 전부 제거했다는 뜻은 아니다.
실제 활성 registry는 고정된 reference·지원 sensor decoder·기본 stack·ONNX CPU·COCO다.
외부 entry-point plugin을 자동 탐색하지 않으며 기존 원격 CARLA 환경 설정도 승계하지 않는다.

`bundle.json`은 파일 크기·SHA와 dependency 버전을 고정한다. 체크섬은 서명이나 배포
권한 승인이 아니다. **Dirty working-tree snapshot의 내부 배포 후보**이며 clean checkout
release 또는 라이선스 심사가 완료된 상용 배포판이라고 표시하지 않는다.

## 실행 전 확인과 시작

패키지의 `PILOT-README.md`에는 별도 Python 환경 설치 절차가 있다. 새 장비의 dependency
설치는 인터넷/승인된 package cache가 필요하다. Node는 화면 build 시에만 필요하다.

```bash
PYTHON_PATH -B BUNDLE/run-pilot.py check --data-dir DATA --state-dir ACCESS --port 5273
PYTHON_PATH -B BUNDLE/run-pilot.py start --data-dir DATA --state-dir ACCESS --port 5273
```

`check`는 데이터·토큰을 만들지 않는다. 파일 변조·dependency 불일치·포트 충돌·잘못된
schema·활성 DB 핸들·예약/격리된 엔진·지원하지 않는 대기 작업을 검사한다. 기존 포트
프로세스를 종료하거나 작업을 삭제하지 않는다. Schema11–16을 허용하며 check는 DB를 변경하지 않는다. start에서 필요한 추가 migration을 적용한다.
새 데이터 경로의 부모는 미리 만들어야 한다. 앱·데이터·접근 상태는 서로를 포함하지 않는
별도 디렉터리여야 한다. 다른 프로그램이 동시에 workspace를 변경하는 사용은 지원하지 않는다.

Finder용 `Start AXIOM Pilot.command`도 제공한다. 기본 Python은 패키지 옆의
`axiom-pilot-env/bin/python`, 데이터는 `axiom-pilot-data`, 접근 상태는 `axiom-pilot-access`다.
`AXIOM_PYTHON`, `AXIOM_PILOT_DATA_DIR`, `AXIOM_PILOT_STATE_DIR`, `AXIOM_PILOT_PORT`로
명시적으로 바꿀 수 있다. 경로에 공백이 있을 때 반드시 따옴표를 유지한다.

시작 메시지의 주소를 열고 **Workspace settings → Bearer token → Test & save connection**을
사용한다. macOS에서 토큰을 화면에 노출하지 않고 복사하려면 `pbcopy < ACCESS/api-token`을 쓴다.
설정 화면에는 고정 개발 포트가 아닌 실제 현재 origin이 표시된다.

## 데이터와 종료

기존 workspace는 먼저 cold backup 후 새 폴더에 복원해 테스트한다. 기존 CARLA Run은
조회할 수 있지만 새 CARLA 실행은 unavailable이다. 이 실행기는 원격 엔진을 재연결하지 않는다.
지원되는 대기 local 작업은 유지하되, 실행 중 ownership·격리 상태가 남으면 먼저 기존 절차로
정리해야 한다. 시작 실패를 해결하려고 원본 DB의 상태 값을 직접 덮어쓰지 않는다.

Ctrl-C/SIGTERM은 이 실행기의 자식 API와 worker만 종료한다. worker는 현재 진행 중인
bounded local job을 마무리한 뒤 종료한다. 강제 중단 또는 비정상 자식 종료는 성공으로
표시하지 않으며 접근 디렉터리의 `*.result.json`에 남긴다. 이때 재시도 전 기존 job 상태를
확인해야 한다. 로그와 token은 데이터 백업에 포함하지 않는다.

## 검증 방식과 다음 gate

`e2e/local-pilot.config.ts`는 별도 source package를 만들고 Node가 없는 PATH에서 실행한다.
빌드된 화면에서 token 로그인, 실제 ONNX/COCO authored-image 업무, A/B 검토와 전체 분모
보고서 다운로드를 시험한다. `test_local_pilot.py`와 `test_build_local_pilot.py`는 변조,
인증·Origin·Host, 토큰 파일, 포트/경로, source/dependency 격리 경계를 검사한다.

기존 Python dependency 환경을 재사용하는 **source-isolated 실행 검사**와 신규 장비의
완전한 설치 인수는 다르다. 후자는 여전히 필요하다. 실제 고객 pilot, clean release/tag,
서명·전체 SBOM/권리 심사, OS별 설치·업그레이드 인수와 다중 사용자 보안도 후속이다.
