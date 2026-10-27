# 첫 제품화 작업 인수 · 2026-09-28

## 사용자가 새로 완료할 수 있는 업무

일반 `Detection datasets → Compare evaluations`에서 기존 두 평가를 선택하고,
같은 조건인지 확인한 뒤 이미지별 TP/FP/FN 변화와 전체 지표의 B−A를 조사할 수 있다.
모델의 차이는 허용하지만 dataset revision/평가/runtime 정책이 다르면 비교를 보류한다.
입력·라벨이 미완결이면 전체 및 이미지 차이값을 보류하고 원래 분모를 유지한다.
화면 필터는 export 내용이나 평가 점수에 영향을 주지 않는다.

같은 기능은 `/api/v1/evaluation-comparisons`, OpenAPI, agent 도구 카탈로그와 Python SDK에
연결했다. JSON과 script-free HTML은 원래 평가의 manifest hash·비교 조건·전체 이미지 행을
포함하는 읽기 전용 요약이다. 사람이 승인한 결과나 원본 이미지/모델 전체의 묶음은 아니다.

또한 `python -m ade2e.workspace_backup`으로 schema11 작업공간을 cold backup하고
hash·DB 관계를 검증한 뒤 **존재하지 않는 새 데이터 디렉터리**에 복원할 수 있다.
원본의 DB/WAL을 SQLite로 직접 열거나 checkpoint하지 않고 복사본에서 통합한다.
서비스 중지 확인과 열린 DB 핸들 검사를 요구하며 queued/running/cancel_requested 또는
예약·격리된 엔진이 있으면 거절한다. 자세한 제한은 [백업 절차](workspace-backup.md)를 따른다.

## 실제 업무 검사

실제 ONNX CPU 실행과 pycocotools COCO bbox 평가를 일반 API/worker로 실행했다.
원본은 **직접 작성한 여섯 이미지·라벨과 pixel-rule 모델**이며 고객/실차 정확도 검증이 아니다.
모델 A/B를 비교해 후보의 더 많은 오류가 있는 이미지 두 장을 찾고, 필터를 적용한 화면에서도
내보낸 보고서는 전체 여섯 이미지를 유지하는 것을 확인했다.
이전 dataset revision의 미완결 평가를 고르면 차이값이 보류되고, 새 비교 조회가 실패하면
이전 비교 화면을 숨겼다. SDK와 브라우저의 내보낸 bytes를 API의 선언 SHA/크기와 대조했다.

Chrome의 세 workflow 검사를 통과했다: 기존 라벨 작성/COCO 평가, 새 A/B review,
기존 모델 등록/ONNX 추론/예측 비교. Desktop, 390px mobile, offline HTML 열기까지 확인했다.
첫 실행은 Playwright 번들 브라우저가 없어 실패했으며 설치된 Chrome channel로 전환했다.
최초 실패 로그와 최종 통과 로그를 모두 보존했다. 원격 CI가 실행됐다는 의미는 아니다.

## 기존 데이터의 백업과 복원본 API 검사

원본 `.data`의 **749개 파일**을 작업 전에 해시로 기록했다. Cold backup은 WAL/SHM을
DB 사본에 통합해 **747개 payload 파일**을 보존했다. 파일 수 차이는 삭제된 업무 데이터가 아니다.
복원본에서 원래 DB 집계 **Run74 / Job81 / Artifact349 / Recording11 / DetectionEvaluation3**이
유지됐다. 모든 백업 payload의 크기·SHA와 DB 무결성·외래키 검사를 통과했다.

복원본을 별도 8196 포트의 실제 uvicorn API로 열었다. Health200과 미인증 읽기의401을
확인하고, Run/Artifact/평가 목록을 조회했다. 표본 artifact8개는 실제 다운로드 bytes를
선언 hash와 대조했다. 저장된 두 완전 평가로 새 비교 API와 SDK JSON/HTML export도 통과했다.
복원 API가 열려 있는 동안 추가 cold backup을 시도하면 거절됐다.
Worker는 시작하지 않았고 CARLA로 실행을 보내지 않았다. 검사 후 소유 API를 정상 종료했다.

이는 단일 로컬 환경 복원 검사다. 다른 OS/장비, 재해 복구, hot backup, 암호화,
원격 engine spool 복원, 미래 DB schema migration까지 인수한 결과는 아니다.
현재 백업은 같은 Mac에 있는 사본이므로 외부 매체/암호화 저장소 보관은 별도로 필요하다.

## 회귀와 소스 범위

전체 Python 회귀 묶음은 **2,697개 통과, 기존 의존성 경고2개**였다.
전체 검사를 진행하는 동안 마무리한 OpenAPI/도구 등록·SQLite 연결 정리 변경은
그 후 **관련 backend/SDK 155개 회귀**로 다시 검사했다. 두 범위는 겹치므로 합산하지 않는다.
프런트엔드 typecheck/build와 Chrome3개 업무 검사도 통과했다.
이번 작업의 단위/계약시험과 실제 ONNX/COCO·복원 API 검사를 구분한다.

기존 staged 변경은 그대로 유지했다. 기존 연구 변경 전체를 무심사 commit하거나
운영 배포하지 않았다. 현재 소스·lockfile·문서를 working-tree snapshot과 파일별 manifest로
보존하며, 이것은 clean-checkout release나 배포 라이선스 승인 완료를 뜻하지 않는다.

## 근거와 다음 작업

- 기계 판독 인수 기록: `docs/validation/product-pilot-20260928.json`.
- 백업: `.runtime/product-pilot-20260928/workspace-backup/`.
- 복원 데이터: `.runtime/product-pilot-20260928/restored-data/`.
- 복원본에서 만든 검토 요약: `.runtime/product-pilot-20260928/restored-comparison.html`.
- 합성 업무 결과와 브라우저 증거: `.runtime/product-pilot-20260928/browser-evidence/`.
- 실행 로그·복원 HTTP 검사·소스 스냅샷은 같은 상위 디렉터리에 보존한다.

현재 작업판은 [product-pilot.md](product-pilot.md)다. 다음은 실제 제공된 고객 로그의
지원 형식·규모·수용 조건, 검토 가능한 소스/배포 기준판, 외부 사용 전 접근 통제다.
첫 로컬 업무 완결을 이유로 다중 사용자 보안·지원·가격·라이선스 검토 완료를 선언하지 않는다.
CARLA 제동·관측 범위 연구는 이 업무의 선행 조건으로 다시 묶지 않는다.
