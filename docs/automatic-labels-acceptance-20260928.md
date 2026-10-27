# 자동 라벨 초안·사람 검토 인수 · 2026-09-28

## 이번 사용자 업무

일반 Detection datasets UI/API/SDK/Agent에서 등록된 detector를 고르고 exact dataset/model 개정·
class mapping·작업량을 확인한 뒤, 원래 메시지를 기존 inference queue로 처리한다.
빈 미검토 이미지만 선택하며 기존 박스/검토는 건드리지 않는다. 모델 예측을 새 미검토 개정으로
발행하고 기존 box editor에서 직접 수정·개별 검토한다. Collection에서 발행한 dataset도 같은 경로다.

장면 태깅·유사검색·검증계획·새 E2E/센서 adapter는 이번에 구현한 것으로 표시하지 않는다.
이 순서를 현재 로드맵/AGENTS에 반영했다. 기존 queue·dataset·원본/개정 계약을 재사용한다.

## 실제 실행과 브라우저

독립 작성 이미지 6개·pixel-rule ONNX 모델을 사용했다. 모델은 등록된 원본 파일이며 실제
ONNX CPU 실행기에서 원래 카메라 bytes를 처리한다. 학습된 semantic detector/실도로 인수는 아니다.

| 항목 | 결과 |
| --- | --- |
| 전체 원본 | 6 images |
| 추론 대상 / 보존 | 5 empty-unreviewed / 기존 reviewed-negative 1 |
| 실제 모델 출력 | 박스 제안4 images / 검출0 제안1 image |
| draft publication | r2, reviewed1·unreviewed5·reviewed-negative1 |
| 사람 수정·검토 | 기존 editor에서 첫 box 좌표 수정과1image검토, r3 reviewed2·unreviewed4 |
| 출처 |5image의known model origin 유지; 원래 r1/r2와 task report hash 보존 |
| 자기 평가 | 생성 모델을 reviewed 제안에 평가하는 요청422 |
| 응답유실 | 동일key재시도에서annotation task/Job중복없음 |

개발 Chrome의 자동 라벨링/Collection/기록모델재평가3개 업무를 통과했다. 패키지에서도
자동라벨링/기록stack/Collection/기존ONNX·COCO업무4개를 인수했다. 전체 행과 모델 초안을
원본에 표시하고, export hash·reload·모바일 overflow·pageerror없음을 검사했다.

## 보존·복원·이관

이번 개발 데이터69payload파일을 별도 백업/복원한 뒤, worker없이 인증API를 두 번 시작했다.
r1원본/r2draft/r3검토개정/task report와known origin5images가 동일했고 무인증조회는401이다.
각 API는검사후종료했다. 실제 고객데이터·새PC설치·운영배포 검사가 아니다.

이전 Collection schema13 백업의 **새 사본**을 schema14로 이관했다.
기존55개업무테이블410행을 raw값으로대조해불변이며,annotation_tasks와annotation_publications
두테이블만추가됐다. 과거 dataset JSON을 새 형식으로 재직렬화하지 않는다.
신규 evaluation의optional annotation_lineage는 source_context에만 추가하고 과거 evidence는유지한다.

## 자동화 검사와 최초 실패 기록

전체Python묶음은 **2,896개 통과·기존의존성경고2개**다. 실행 이후 추가한 평가export출처
확장과 최종 변경은 관련11개모듈 **231개 통과**로 재검사했다. 두 범위는 겹치므로 합산하지 않는다.
Frontend타입검사/build도통과했다. 모델runtime을가짜로교체한계약시험과 실제ONNX브라우저실행은별도다.

초기계약검사에서는 라벨origin이없는Collection표본까지빈배열로집계한문제를발견해,
출처가실제로존재하는image만반환하도록수정했다. 최초브라우저검사는빈이미지를고정된배열
위치로추정한시험오류가있었다. 실제report의exact image_id로찾도록수정했으며제안라벨/분모는
바꾸지않았다. 최초로그·화면·trace는 `.runtime/annotation-assist-20260928/`에보존한다.

## 경계

검토선언은개인인증·정답독립성승인이아니다. known origin은일반개정과Collection라벨복사에
보존하지만임의외부복사/재입력까지추적하지않는다. 생성모델의가중치SHA를이용해자기평가를
거절하되,다른모델로평가한다고독립적인benchmark라고주장하지않는다. 원본·scope·라벨검토·모델권리는
별도수용조건이다. 멀티recording의Job분할은계약시험으로확인했고실제ONNX인수는1recording이다.

사용법: [automatic-labels.md](automatic-labels.md). 최종패키지/파일보존과검사산출물목록은
`docs/validation/automatic-labels-20260928.json`에기록한다. 커밋·원격CARLA·운영배포는하지않는다.

최종207파일패키지 `annotation-assist-20260928/package-pilot-r2`의4개Chrome업무는51.4초에통과했다.
검사후API/worker는exit0으로종료했고소유port는해제됐다. 원본 .data749파일과Gitindex는작업전과
동일하며,최종패키지의backend/SDK/adapter파일hash는현재checkout과일치한다. 패키지에는원본
데이터나토큰을넣지않고별도인수용data/access폴더를사용했다.
