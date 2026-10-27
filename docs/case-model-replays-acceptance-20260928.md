# Case의 실제 원본 모델 재평가 인수 · 2026-09-28

## 새로 완료한 내부 업무

기존 object-reference 파생시험과 별개로, Case에서 등록 모델을 선택해 기준 평가의 전체
원본 split을 실제 추론하고 동일 dataset/라벨/매핑/평가정책으로 다시 평가한다.
Preview→명시적 추론 제출→종료 예측 평가→Case에 결과 capture를 일반 UI/API/SDK에서 수행한다.
기존 queue와 Case 개정의 원자적 저장을 사용하며 두 번째 scheduler를 만들지 않았다.
모델 성능과 작업 성공을 분리한다. 지원 범위·실패 처리와 API는 [사용법](case-model-replays.md)에 있다.

## 실제 브라우저 업무

원본은 직접 작성한 여섯 이미지·라벨과 registered pixel-rule ONNX 모델이다. 실제 ONNX CPU와
pycocotools COCO가 기존 API/worker로 계산한다. 고객 데이터/학습형 주행/도로정확도 인수는 아니다.

하나의 Case 이미지에서 시작하지만 전체 baseline split6개를 새 후보로 재추론했다.
새 평가의 지표는 별도로 이미 실행해 둔 동일 후보의 평가값과 같았다. 기준 A 대비 오류가 증가한
이미지2개와 AP 하락을 그대로 표시했고 전체6개를 유지했다. 결과를 좋게 만들기 위해
불리한 이미지를 제외하거나 baseline 예측값을 채우지 않았다.

Preview는job0개, 명시적 제출 후 새 추론1개·평가1개가 생성됐다. 제출 응답을 끊고
같은 key/본문으로 재시도해도 연결 시도는1개다. Case r1→추론r2→평가r3→capture r4로
보존되고, Case는open·연결평가2개다. 원래Case r1과 baseline 평가 bytes가 유지됐다.
전체비교 deep-link, 다운로드hash, 새로고침, 390px 가로넘침 없음과JavaScript오류0을 검사했다.

개발 실행 경로에서 새 모델재평가, 기존Case, 기존참조재시험의Chrome3개 업무가 통과했다.
추론이 실제로 실패·취소되는 분모 계약은 별도의 fake-runtime 단위시험으로 검사했으며,
실제 ONNX crash/fault 주입이나 모든 실패 환경을 인수한 것으로 확대하지 않는다.

## 고정 패키지의 업무 연결

`package-pilot/relocated application`의191개파일을 사용하는 별도패키지에서Chrome업무1개를
완료했다. 같은 Case에 I1의원본, I2의reviewed조건, I3의captured참조시험을 보존한 상태로
실제 원본 모델재평가를 추가했다. Case r5→r6→r7→r8이며 이전참조시험과조건이 유지됐다.
동일모델재실행에서는 비교가능한 모든수치delta가0이었다. 미인증조회401과정상종료도확인했다.

실행경로의Node/Vite는없고기존Python dependency환경을사용했다. 이는새PCcleaninstall인수가아니다.
패키지의API/worker는둘다exit0,강제종료없음으로종료했다. 개발가이드의후속기획변경은
그이후작업문서에기록하며테스트한runtime소스가패키지내동일함을별도로대조했다.

## 보존·복원과 호환성

별도브라우저데이터63payload를coldbackup해새폴더에복원한후API를두번시작했다.
모델재평가report hash는백업전브라우저export와같았고,Case r4·전체6이미지·평가링크2개·
open상태를유지했다. SDK로report/과거Case/기준평가를다시내보냈다.
복원중worker·원격엔진을시작하지않았다. 직접uvicorn의종료코드−15는signal종료로별도기록했다.

DBschema12를유지하며새테이블이나원본migration이없다. 새Case/4와기존Case/1/2/3을
함께읽고조건편집·참조재시험·일반개정에서새연결을보존한다. 구패키지로새Case를편집하지않는다.
원본.data749개파일과Gitindex는시작전hash와대조한다. 커밋·운영배포·CARLA연구를하지않았다.

## 회귀·실패 이력

최초계약시험에서Job의primary key인submission sequence와외부job ID를혼용한조회가실패했다.
기존jobs._row조회계약으로수정했으며최초로그와수정후검사를분리보존했다.
마지막관련회귀232개가통과했고기존dependency경고2개가남았다.
전체회귀는2,820개통과·기존경고2개(529.23초)다. 기계판독기록의python_full에도보존한다. 전체실행중추가한
취소·지원범위·Agent계약검사는후속232개에포함하며검사범위를합산하지않는다.
Frontendtypecheck/build와검사범위는같은기계판독기록에남긴다.

## 산출물과 남은 범위

- 사용법: `docs/case-model-replays.md`.
- 기계판독기록: `docs/validation/case-model-replays-20260928.json`.
- 로그·브라우저증거·복원결과: `.runtime/case-model-replay-20260928/`.
- 고정실행패키지: 같은경로의`package-pilot/relocated application/`.

이단계는기존registeredONNX/COCO어댑터를실제Case업무에연결했다. 새로운외부공급사connector,
고객모델수용,멀티카메라·시계열E2E정책,GPU/분산훈련,실측센서조건coverage는미완료다.
후속은실제사용할모델/입출력과데이터범위에맞춰선택하며합성모델점수튜닝으로돌아가지않는다.
