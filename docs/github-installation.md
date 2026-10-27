# GitHub 소스 설치와 로컬 실행

이 안내는 소스 체크아웃에서 UI·API·SQLite 작업 공간을 시작하는 절차다. 기본 참조 실행과 작은 샘플을 보는 데 CARLA·GPU·외부 계정은 필요하지 않다. 새 CARLA 실행, Neural 재구성, RTL/SPICE 실행은 [별도 의존성](github-dependencies.md)을 준비해야 한다.

아래 명령은 저장소 루트에서 실행한다. 문서 작성 중 새 컴퓨터에 설치하거나 외부 엔진을 검증한 것은 아니다. 저장소의 계약 검사와 과거 native 실행 근거도 각자의 범위로 해석한다.

## 1. 기본 개발 환경

| 항목 | 사용할 환경 |
| --- | --- |
| Python | **CPython 3.12** 권장. backend 선언은 3.11 이상이지만 전체 개발 lock과 선택 ONNX 조합은 3.12 이상을 사용한다. 다른 Python 버전의 wheel 가용성은 별도 확인한다. |
| Node.js / npm | **Node 22.12 이상인 22.x**와 npm. 현재 Vite lock의 Node 조건은 `^20.19.0 || >=22.12.0`이다. |
| OS | 아래 명령과 launcher는 Linux/macOS의 POSIX shell용이다. Windows 사용자는 별도 Linux 환경을 준비한다. Windows·WSL 설치 인수 완료를 뜻하지 않는다. |
| 브라우저 | WebGL을 사용할 수 있는 최신 Chromium 계열 브라우저. 기본 UI의 3D 형상은 센서 영상과 구별한다. |

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -c requirements.lock -e 'backend[test]' -e sdk/python
npm --prefix frontend ci
```

같은 최소 설치는 `bash scripts/setup_core.sh`로도 수행할 수 있다. Python 선택이 필요하면 `AXIOM_PYTHON`에 사용할 실행 파일을 지정한다.

`requirements.lock`은 버전 **constraints** 파일이다. `-c`는 설치할 패키지의 버전을 제한하며 파일에 있는 모든 패키지를 설치하지 않는다. 따라서 위 명령은 ONNX·CARLA client·CUDA를 설치하지 않는다. `backend[test]`는 검증 도구를 포함한다. 테스트가 필요 없는 사용 환경에서는 `-e backend`로 바꿀 수 있다.

기존 `bash scripts/setup.sh`는 CARLA **adapter**와 ONNX/COCO 패키지까지 포함한 더 큰 개발 환경을 설치한다. 이 스크립트도 CARLA 서버나 학습 가중치는 설치하지 않는다. 최소 설치와 구분해서 선택한다.

## 2. 첫 실행

```sh
ADE2E_DATABASE_URL="" ADE2E_DATA_DIR="$PWD/.data" .venv/bin/python scripts/dev.py
```

- UI: <http://127.0.0.1:5173>
- API 문서: <http://127.0.0.1:8000/docs>
- 연결 확인: `curl --fail http://127.0.0.1:8000/health`

launcher는 API, 영속 큐 worker, Vite를 함께 시작하고 `Ctrl-C`로 자신이 시작한 프로세스를 종료한다. 포트가 사용 중이면 기존 프로세스를 종료하지 않고 실패한다. 필요하면 `AXIOM_API_PORT`와 `AXIOM_UI_PORT`를 서로 다른 빈 포트로 지정한다.

빈 작업 공간은 기본적으로 직접 작성한 시나리오 8개를 **참조 모델로 계산**해 저장한다. CARLA 실행이나 측정 센서 데이터가 아니다. 샘플 DB를 그대로 열거나 빈 작업 공간을 원하는 경우 `ADE2E_SEED_EXAMPLES=0`을 지정한다. API와 worker는 같은 `ADE2E_DATA_DIR`를 사용해야 한다. UI/API만 시작하면 비동기 작업은 큐에서 대기한다.

운영 DB를 이 디렉터리에 복사해 시험하지 않는다. DB 파일과 관리 artifact는 함께 보존하고, 기존 환경의 업그레이드는 [백업·복원 절차](workspace-backup.md)를 따른다. 현재 schema와 마이그레이션 여부를 실행 버전에 맞춰 확인한다.

## 3. 포함된 작은 샘플 DB 사용

샘플은 직접 작성한 CPU 참조 계산으로 만든 독립 작업 공간 3개다. `platform.db`와 관련 artifact/recording/experiment 파일을 함께 제공하며, `manifest.json`에 파일 크기·SHA-256과 실행 범위를 보존한다. 실제 도로 자료·CARLA 영상·학습 가중치·운영 DB를 포함하지 않는다.

| `--sample` | 확인할 내용 |
| --- | --- |
| `object-reference` | 참조 cut-in의 통과·실패 결과와 판정·궤적 |
| `log-open-loop` | 직접 작성한 객체/자차 MCAP의 open-loop 계획 재생 |
| `log-feedback` | 같은 합성 기록에서 주변 객체를 고정한 참조 feedback 계산 |

```sh
.venv/bin/python scripts/build_github_demo.py verify --source examples/github-demo
.venv/bin/python scripts/build_github_demo.py load --sample object-reference --source examples/github-demo --data-dir .runtime/github-demo
ADE2E_DATABASE_URL="" ADE2E_DATA_DIR="$PWD/.runtime/github-demo" ADE2E_SEED_EXAMPLES=0 .venv/bin/python scripts/dev.py
```

`verify`는 원본 inventory/hash와 SQLite 무결성을 확인하고, 임시 복사본을 API로 읽어 검증한다. `load`는 검증한 샘플을 아직 존재하지 않는 새 작업 디렉터리로 복사하며 기존 데이터는 덮어쓰지 않는다. 다른 샘플은 `--sample`과 작업 디렉터리를 함께 바꾼다. 출력의 `ui` 링크 또는 복사본의 `github-demo.json`에서 정확한 저장 결과로 이동한다. `ADE2E_DATABASE_URL=""`은 이전 shell에 지정된 DB 연결보다 이번 로컬 작업 디렉터리를 사용하도록 한다. 이 경로에는 core 외의 native dependency가 필요하지 않다.

샘플을 다시 생성하려면 아래 명령을 사용한다. 참조 계산을 새로 실행하므로 실행 ID·시각과 DB bytes가 배포 샘플과 같을 필요는 없다. 원래 샘플을 교체하지 않고 새 폴더에 생성한다.

```sh
.venv/bin/python scripts/build_github_demo.py build --output .runtime/github-demo-rebuilt
.venv/bin/python scripts/build_github_demo.py verify --source .runtime/github-demo-rebuilt
```

## 4. 설치 후 확인

```sh
.venv/bin/python -m pip check
.venv/bin/python -c 'import ade2e, axiom_sdk; print("core imports OK")'
npm --prefix frontend run build
```

이 확인은 패키지 일관성·import·frontend build 검사다. 그 다음 UI에서 새 시나리오를 작성하고 **reference** 실행 → 완료/판정 → 결과 재생 → 새로고침 복원을 확인한다. 엔진 연결은 설치 여부, 연결 확인, 실제 실행, 결과 판정을 각각 구분한다. 선택 adapter를 추가한 뒤에는 진행 작업을 확인하고 해당 API·worker를 함께 다시 시작해야 새 entry point가 반영된다.

모든 선택 패키지를 설치하지 않은 상태에서 전체 테스트 폴더를 일괄 실행하면 선택 기능 검사가 실패하거나 skip될 수 있다. 소스 배포 CI의 설치 범위는 [portable workflow](../.github/workflows/portable.yml)를 참고하고, 기능별 검사는 각 integration 문서의 명령을 사용한다. CI의 mock/fixture 검사를 실제 CARLA·GPU·장치 인수로 해석하지 않는다.

## 5. 접근·데이터 보관

기본 launcher는 loopback에만 bind하는 로컬 개발 도구다. 인터넷 공개 서비스, 다중 사용자 운영, 자동 재시작 서비스는 이 설치 범위에 포함되지 않는다. 토큰·SSH 키·운영 DB·실행 artifact·모델 가중치는 Git에 추가하지 않는다. 배포된 샘플은 자신의 작업 디렉터리에 복사해서 사용하며 원본 샘플과 manifest를 덮어쓰지 않는다.

소스 공개 여부와 외부 엔진·모델·지도·데이터 재배포 권리는 서로 다르다. [의존성과 권리 범위](github-dependencies.md)를 확인한다.
