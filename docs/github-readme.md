# AXIOM · AD E2E Platform

자율주행 시나리오 작성, 실행, 로그 조사, 결과 비교와 실패 Case를 연결하는 내부 베타 플랫폼입니다.
Object · Sensor · Log · Neural · Software-DUT HIL 모듈이 같은 API와 Python SDK를 사용합니다.

이 저장소는 개발 작업공간에서 선별한 **소스 스냅샷**입니다. 기존 Git 이력, 운영 DB,
계정 토큰, 내부 서버 설정, 대용량 연구 원본은 포함하지 않습니다.
포함된 작은 예제 DB와 결과 파일은 CARLA 없이 열어볼 수 있습니다.

## 빠르게 시작하기

Python **3.12**, Node.js **22.12 이상**, npm을 준비합니다. macOS/Linux 로컬 환경 기준입니다.

```bash
bash scripts/setup_core.sh
```

[예제 DB 안내](examples/github-demo/README.md)에 세 샘플의 내용과 결과 링크가 있습니다.
아래 명령은 Object 샘플을 새 작업 폴더에 복사하고 바로 실행합니다.

```bash
.venv/bin/python scripts/build_github_demo.py verify
.venv/bin/python scripts/build_github_demo.py load --sample object-reference --data-dir .runtime/github-demo
ADE2E_DATABASE_URL="" ADE2E_DATA_DIR="$PWD/.runtime/github-demo" ADE2E_SEED_EXAMPLES=0 .venv/bin/python scripts/dev.py
```

화면: <http://127.0.0.1:5173> · API: <http://127.0.0.1:8000/docs>.
종료는 터미널에서 Ctrl-C입니다. 기존 서비스를 변경하지 않으며, 포트가 사용 중이면
`AXIOM_API_PORT`와 `AXIOM_UI_PORT`로 다른 포트를 지정합니다.

## 포함된 결과

| DB | 확인할 내용 | 근거 범위 |
| --- | --- | --- |
| Object reference | 간단한 시나리오의 통과·실패, 지표와 재생 | CPU 참조 시뮬레이션 |
| Log open loop | 작성한 로그 입력, 원본과 계획 결과 비교 | 합성 MCAP + 참조 계획 계산 |
| Log feedback | 자차 피드백을 포함한 계획 재생 | 참조 모델 폐루프, 비반응형 주변 객체 |

샘플은 현재 코드로 실제 계산한 예제입니다. 실차 측정·센서 정확도·실물 HIL 검증 결과가 아닙니다.
DB만 떼어 옮기면 연결된 파일이 빠지므로 **샘플 디렉터리 전체**를 사용하세요.
각 샘플의 manifest와 생성 스크립트를 함께 제공합니다.

## 외부 설치가 필요한 기능

- **CARLA:** 엔진·Unreal·맵 자산은 별도 설치. 기본 DB 조회에는 필요하지 않습니다.
- **Neural Sim:** 선택한 GPU 환경, 재구성 도구와 입력 데이터가 필요합니다.
- **HIL / RTL / SPICE:** 선택한 native 도구·가상 ECU·장치 설정이 필요합니다.
- **CameraE2E / 모델 추론 / 외부 지도:** 각 연결의 코드·모델·데이터를 별도로 준비합니다.

[설치 안내와 CARLA](docs/github-installation.md) · [포함/별도 의존성 및 권리](docs/github-dependencies.md)

[검사 결과와 실제 화면 미리보기](docs/github-verification.md)

## 검사와 프로젝트 문서

```bash
.venv/bin/python -m pytest scripts/tests/test_github_demo.py sdk/python/tests/test_client_protocol.py -q
npm --prefix frontend run build
```

GitHub Actions는 이 샘플 조회 경로의 검사, frontend build와 Chromium 재생·다운로드·새로고침 검사를 수행합니다.
CARLA 실행이나 GPU·실물 장비 검증으로 해석하지 않습니다.
추가 native 실행은 해당 도구 설치와 준비 확인 후 별도로 수행합니다.

[현재 상태](docs/status.md) · [로드맵](docs/roadmap.md) · [API/SDK](sdk/python/README.md)

과거 문서가 참조하는 대용량 로컬 검증 기록은 이 저장소에 포함하지 않습니다.
이번 배포 파일 목록·해시는 `PUBLICATION-MANIFEST.json`에 기록합니다.
플랫폼 자체 코드의 오픈소스 라이선스는 아직 지정하지 않았습니다.
동봉된 제3자 코드·글꼴·3D 자산은 각 원래 LICENSE/NOTICE를 따릅니다.
