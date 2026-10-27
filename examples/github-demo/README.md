# CARLA 없이 열어보는 작은 예제 DB 3개

운영 DB를 복사하지 않고 **원래 작성한 합성 입력을 현재 CPU 참조 구현으로 실행**해
만든 SQLite 예제다. CARLA, GPU, 실도로 데이터, 카메라 영상, 토큰이 없다.
입력·seed·참조 계산 결과는 반복 가능하지만 실행 ID·생성 시각·SQLite bytes는
재빌드마다 달라질 수 있다. HIL·센서·물리 차량 검증을 대신하지 않는다.

| 예제 | 실제 포함 결과 | DB 크기 | 파일 포함 크기 |
| --- | --- | ---: | ---: |
| `object-reference` | 8초 cut-in 2개: 기준 통과 1건·실패 1건, 각 161 상태 | 1,277,952 B | 1,381,325 B |
| `log-open-loop` | 같은 합성 MCAP의 original replay·open loop, 각 41 상태 | 1,675,264 B | 2,440,154 B |
| `log-feedback` | 같은 합성 MCAP의 planning feedback 2개, 제동 상한 0.8/6 m/s² | 1,703,936 B | 2,517,218 B |

각 폴더의 `platform.db`와 함께 `artifacts/`, `experiments/`, `recordings/`를 보존한다.
DB 파일만 단독 복사하면 원본 다운로드·재실행이 완전하지 않다.
[manifest.json](manifest.json)에 모든 파일 크기·SHA-256, 실행 ID, UI 경로와 생성 코드 해시가 있다.
모든 로그는 `source_kind=synthetic`이고 측정 데이터로 등록하지 않았다.

## 하나를 복사해서 열기

프로젝트 루트에서 [기본 설치](../../docs/github-installation.md)를 마친 뒤 실행한다.
로더는 먼저 파일 해시와 SQLite 무결성을 검사하고 **새 디렉터리로만** 복사한다.
기존 작업 DB나 배포 예제 원본을 덮어쓰지 않는다.

```sh
.venv/bin/python scripts/build_github_demo.py verify
.venv/bin/python scripts/build_github_demo.py load \
  --sample object-reference --data-dir .runtime/github-demo-object
ADE2E_DATABASE_URL="" ADE2E_DATA_DIR="$PWD/.runtime/github-demo-object" \
  ADE2E_SEED_EXAMPLES=0 .venv/bin/python scripts/dev.py
```

`http://127.0.0.1:5173`을 연다. 샘플을 바꾸려면 자신이 시작한 `dev.py`를 Ctrl-C로
종료한 뒤 아래 샘플 이름을 새 `--data-dir`로 복사해 그 경로로 다시 시작한다.
로컬 single-user 모드 예제이며 외부 공개 서버 구성은 포함하지 않는다.
기존 shell에 운영용 `ADE2E_API_TOKEN`·team 설정을 지정했다면 별도 깨끗한 shell을 사용한다.

## 무엇을 확인할 수 있나

### Object reference

- [통과 cut-in](http://127.0.0.1:5173/#runs?run=run_b4b678f8467745249721cf79281cfca8)
- [실패 cut-in](http://127.0.0.1:5173/#runs?run=run_a0c0eec084854a5cb666689321337c6e)
- Run explorer에서 참조 궤적을 재생하고 collision/TTC/감속 수치와 provenance·원본 근거를 본다.
- Scenario library에서 설정을 수정하고 Object Reference로 다시 실행할 수 있다.
- Validation의 예시 TTC 1초 기준은 이 참조 모델의 설명용 회귀 조건이다.

실패 예제는 실제 참조 사각형 접촉 1건·최소 TTC 0초를 보존한다. 축 정렬 사각형,
지연된 객체 정답 기반 제동, 마찰 제한 감속을 쓰며 카메라 인지·조향·충돌 역학은 없다.

### Log open loop

`--sample log-open-loop --data-dir .runtime/github-demo-log`로 복사한다.

- [원래 로그 재생](http://127.0.0.1:5173/#viewers?project=default&execution=job_98bc928d87f3404d8b3ba6ad6fbfb3ae)
- [Open-loop 요청](http://127.0.0.1:5173/#viewers?project=default&execution=job_10900eb40fa740999b22358ea0088fef)
- 결과 Viewer에서 Play·timeline·속도 차트·관찰값을 본다. 두 실행의 ego 위치는 원래 입력과 같다.
- Open loop는 제어 요청을 계산하되 다음 차량 상태에 적용하지 않는다.
- Data catalog에는 원래 작성한 MCAP가, Investigation cases에는 마지막 1초를 고른 Case가 있다.
- Log replay에서 고정된 입력과 모드·controller를 확인하고 새 Experiment를 제출할 수 있다.

원래 입력은 6 m/s의 ego가 22 m 위치에 정지한 lead를 향해 4초간 계속 이동하도록
작성했다. 따라서 원래 궤적의 접촉 관찰을 기록하며 이를 좋은 주행이나 모델 실패로 해석하지 않는다.

### Log feedback

`--sample log-feedback --data-dir .runtime/github-demo-feedback`으로 복사한다.

- [제동 상한 0.8 m/s²](http://127.0.0.1:5173/#viewers?project=default&execution=job_a38eb4b4ca6c465695cb8197034f0e83)
- [제동 상한 6 m/s²](http://127.0.0.1:5173/#viewers?project=default&execution=job_164947deb11648e0b7f28a3ce1c354c4)
- 요청을 다음 참조 차량 상태에 적용한다. 원래 ego 궤적과 새 궤적을 같은 화면에서 비교한다.
- 마지막 ego X는 각각 약 21.122 m·17.220 m, 속도는 3.855 m/s·0.932 m/s다.
- 0.8 조건은 접촉 상태 8표본, 6 조건은 0표본이다. 표본 수는 독립 충돌 사건 수가 아니다.

**두 feedback 실행 모두 진단 결과이며 안전 통과 판정이 아니다.** 주변 객체는 원래
입력을 그대로 재생하고 ego에 반응하지 않는다. 차선·목표 근거를 제공하지 않았으므로
`lane_departure`·`goal_arrival`은 `unevaluated`로 남는다. 실제 ECU, hard real-time,
CARLA 실행, 카메라 feedback이나 센서 물리 검증은 포함하지 않는다.

## 재생성·검증

기본 backend test 의존성만 사용한다. 선택형 engine 패키지는 검색하지 않는다.
아래 build는 normal API 작성 → reference 실행/queue → 원본 산출물 보존으로 생성하며
운영 DB·서비스·CARLA에 연결하지 않는다. 기존 출력 폴더에는 쓰지 않는다.

```sh
.venv/bin/python scripts/build_github_demo.py build --output .runtime/github-demo-rebuilt
.venv/bin/python scripts/build_github_demo.py verify --source .runtime/github-demo-rebuilt
.venv/bin/python -m pytest -q scripts/tests/test_github_demo.py
```

`verify`는 배포 원본을 수정하지 않고 임시 복사본에서 normal API 조회, retained source
preflight, Case/실행 manifest와 artifact 다운로드 해시를 검사한다. focused test는
새 생성, 이동한 DB의 반복 실행, 원본/open-loop/feedback 의미, 미평가 관찰값,
변조·symlink·기존 대상 거절을 검사한다. 브라우저 클릭·시각 검토는 별도 검증이다.

이 디렉터리는 작게 유지하기 위한 예제 패키지다. 새로 실행한 자신의 결과를 다시
여기에 덮어쓰거나 데이터·라이선스 출처가 다른 운영 파일을 섞지 않는다.
