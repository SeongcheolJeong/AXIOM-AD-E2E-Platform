# 로컬 작업공간 백업·복원

현재 도구의 지원 DB 범위는 **schema11–15**입니다. 백업/복원 자체는 이관하지 않습니다.
Scene discovery가 포함된 새 앱의 schema15 이관은 새 복원 경로에서 확인하세요.

지원 범위: macOS/Linux의 단일 `platform.db` SQLite 작업공간, schema11/12/13/14/15.
이전 schema14 이하 전용 앱으로 schema15 데이터를 열지 않습니다.
설치된 Python 환경과 `lsof`가 필요하다. PostgreSQL·네트워크 파일시스템·실행 중 hot backup은
지원하지 않는다. 도구는 운영 프로세스를 종료하거나 원격 CARLA를 호출하지 않는다.

## 백업

먼저 **이 작업공간의 API와 모든 worker를 정상 종료**한다. 실행 중이거나 대기 중인 작업은
완료/취소하고, 예약·격리된 엔진은 기존 절차로 정리한다. 다른 프로젝트를 종료하지 않는다.
백업 대상과 별도의 경로를 사용한다. 아래의 경로 이름은 예시이며 이미 존재하면 거절한다.

```bash
cd "/home/axiom-user/Documents/ChatGPT/AD E2E Platform"
.venv/bin/python -m ade2e.workspace_backup backup \
  --source .data --destination /your/backup-parent/axiom-backup-001 --offline
.venv/bin/python -m ade2e.workspace_backup verify /your/backup-parent/axiom-backup-001
```

결과는 `manifest.json`, `manifest.sha256`, `payload/`로 된 디렉터리다. DB와 함께
artifacts/recordings/model/dataset 등의 전체 데이터 파일을 보존한다. 남아 있는 WAL은
**사본에서만** SQLite backup API로 통합하며 원본을 checkpoint하거나 수정하지 않는다.
따라서 원본 파일 수와 백업 파일 수는 WAL/SHM 제외로 다를 수 있다.

`--offline`은 API/worker를 이미 멈췄다는 운영자 확인이다. `lsof` 열린 DB 핸들과 두 차례
파일 비교를 추가 검사하지만, 악의적 동시 쓰기나 다른 사용자의 비가시 프로세스를 허용하는
다중 사용자 hot snapshot 계약이 아니다. 데이터 디렉터리 쓰기를 중지한 상태에서 실행한다.

## 새 디렉터리 복원과 확인

```bash
.venv/bin/python -m ade2e.workspace_backup restore /your/backup-parent/axiom-backup-001 \
  --destination /your/workspace-parent/axiom-restored

ADE2E_DATA_DIR=/your/workspace-parent/axiom-restored \
ADE2E_SEED_EXAMPLES=0 ADE2E_CARLA_WORKER_URL='' \
  .venv/bin/python -m uvicorn ade2e.main:app --host 127.0.0.1 --port 8100
```

복원은 파일 검사·DB 무결성·외래키·테이블 집계를 통과한 새 디렉터리에서만 완료된다.
기존 `.data`를 덮어쓰지 않는다. 서비스나 작업을 자동 시작하지 않으며, 위처럼 API만 먼저
열어 과거 결과와 출처를 확인한다. API/worker의 데이터 경로는 함께 지정해야 한다.
Schema 번호를 내리거나 강제로 migration하는 기능은 없다.

## 보존·보안 경계

백업은 **암호화되지 않은 민감 데이터 사본**이다. SHA는 우발적 손상 탐지용이며 서명이나
권한 검사를 대신하지 않는다. 디렉터리와 파일을 제한된 권한으로 만들지만, 백업 저장소의
접근 통제·암호화·외부 매체 복사·보존 기간은 운영자가 별도로 관리해야 한다.

`.env`, `.runtime`의 연구 원본, SSH 자격증명, 외부 모델 경로, 원격 CARLA spool,
application code/설치 환경은 포함하지 않는다. 정확한 코드 release와 의존성 lockfile을
함께 보존하고, 아직 release가 고정되지 않았다면 출처가 확인된 working-tree snapshot도
별도로 보존한다. 같은 디스크에만 만든 복사본은 장비 고장에 대한 재해 복구가 아니다.

손상·추가/누락 파일·symlink·비지원 schema·미완료 작업·격리 상태는 거절하며, 실패하면
이번 호출이 만든 신규 출력만 정리한다. 기존 데이터와 실패 원본의 판정을 바꾸지 않는다.
