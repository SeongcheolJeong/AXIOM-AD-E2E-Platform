# Verification Plan v1 인수 · 2026-10-04

Validation → Plans에서 검증 목적·고정 데이터/모델 개정·조건·기준을 작성하고, 실행량 확인 후
모델 A/B 추론과 전체/조건별 평가를 제출할 수 있다. 데이터 부족·라벨/조건 검토 필요·충돌·
미평가/비교 불가·성능 악화를 구분하며, 원본 비교 화면을 조사한 뒤 계획으로 돌아올 수 있다.
계획 개정, 취소, 성공 추론의 명시적 재사용, 새 평가와 해시 검증 export를 UI/API/SDK로 제공한다.
Collection/Dataset/Case/Evaluation/Run/Suite/완료 Campaign은 개정 또는 당시 snapshot으로 연결한다.
이러한 문맥 근거만으로 검출 성능이나 주행 안전성을 통과 처리하지 않는다.

## 실행 근거

| 검사 | 결과 | 근거 범위 |
|---|---|---|
| API/큐/검출 평가/리뷰/라벨 출처/패키지/백업/SDK/COCO 관련 Python | 263 passed | 계획의 공백·부분 실패·취소·재시도·해시와 기존 평가 동작 |
| 이관/시나리오/Suite/기록/센서/ONNX/COCO/CARLA 원격 계약/SDK | 302 passed | 과거 schema→16, 동시 시작·rollback, 기존 어댑터 계약; CARLA 실서버 실행 아님 |
| 최종 계획/패키지/백업 관련 Python | 79 passed | Suite/Campaign 문맥, 완료 계획 복원, 변조 취소 거절, 하위 작업 없는 대기 계획의 어댑터 점검 포함 |
| SDK 최종 export 경계 | 5 passed | 미완료·변조 export가 기존 파일을 덮어쓰지 않음 |
| 개발 Chrome 업무 | 4 passed | 새 계획 업무와 기존 요구사항·시나리오·모바일·실행 조회 실패 처리 |
| 최종 독립 패키지 Chrome 업무 | 1 passed | 토큰 인증, 실제 CPU ONNX/COCO A/B, 악화/부족 표시, 조사·복귀·개정·export |
| TypeScript 및 production UI build | passed | 최종 UI 컴파일 |

Python 검사 묶음은 중복되므로 합산한 고유 테스트 수를 주장하지 않는다. 전체 저장소 테스트를
전부 실행했다는 의미도 아니다. TestClient의 기존 의존성 deprecation 경고 2종이 남아 있다.

브라우저의 입력 영상·ONNX 모델·라벨은 작성한 시험 자료다. 실제 ONNX Runtime 및 pycocotools를
사용했지만 학습 모델의 실도로 정확도 근거가 아니다. 조건 AP는 선택한 이미지/클래스에서 다시
계산하고 전체 AP와 별도로 유지한다. 단위 검사에서 실제 COCO 클래스/이미지 cohort 계산을 확인했다.
모바일 화면은 가로 overflow 검사와 데스크톱/모바일 캡처를 확인했다.

## 확인한 패키지와 재실행

패키지: `.runtime/verification-plan-v1-20261004/relocated application`

시험 데이터: `.runtime/verification-plan-v1-20261004/data`

접근 상태: `.runtime/verification-plan-v1-20261004/access` (토큰을 공유하거나 기록에 넣지 않는다.)

이 패키지는 소스 체크아웃 밖에서, Node/npm 없는 PATH와 명시적 local adapter로 실행했다.
기존 Python 환경의 의존성을 사용했으므로 새 장비 설치 인수는 아니다. 종료 receipt에서 API/worker
exit code 0, forced stop 없음, recovery 필요 없음으로 확인했다. 시험 서비스는 종료한 상태다.

기존 운영 DB에 연결하지 않고 이 시험 작업공간을 다시 열려면 저장소 루트에서 실행한다.

```bash
.venv/bin/python -B ".runtime/verification-plan-v1-20261004/relocated application/run-pilot.py" start \
  --data-dir ".runtime/verification-plan-v1-20261004/data" \
  --state-dir ".runtime/verification-plan-v1-20261004/access" --port 8191
```

출력된 로컬 주소에서 Workspace settings에 해당 private token을 입력한다. 이는 시험 데이터가 있는
내부 단일 운영자 패키지이며, 기존 운영 서버/외부 공유를 새 버전으로 자동 전환하지 않았다.
원본 운영 데이터의 schema16 이관은 냉백업·새 복원본·업무 확인 절차로 별도 수행해야 한다.

## 남은 범위

- 실제 학습형 주행 정책의 시계열 카메라 입력·궤적 출력 및 폐루프 시험은 미완료다.
- CameraE2E RAW/ISP를 통한 센서 A/B 실험은 별도 계약과 실제 처리 인수가 필요하다.
- 임의 객체 크기 bin, 시간 구간/독립 사건, 학습형 의미 검색, 자동 조건 변환·공백 생성은 미완료다.
- 선언한 작성자/검토자는 인증된 개인 권한이 아니다. 팀 권한·프로젝트 격리·승인 실데이터와
  깨끗한 소스/의존성 배포·출시 gate는 남아 있다. 상용 플랫폼 전체 목표는 열린 상태다.

[사용법과 API](verification-plans.md) · [기계 판독 인수 기록](validation/verification-plans-20261004.json)
