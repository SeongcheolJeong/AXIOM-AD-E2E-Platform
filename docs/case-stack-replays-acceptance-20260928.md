# Case 기록 시계열 스택 비교 인수 · 2026-09-28

## 사용자가 새로 완료하는 업무

일반 UI/API/SDK에서 기록된 ego/LiDAR source의 Case를 만들고, 완료 baseline의 **전체 시계열**을
다른 저장 stack revision으로 재생한다. 기존 queue와 Case 링크를 원자적으로 발행하고,
모든 tick의 요청 차이를 조사해 terminal 결과를 같은 Case에 고정한다. 별도 시뮬레이터 실행기가 아니다.
사용법과 경계는 [case-stack-replays.md](case-stack-replays.md)에 있다.

## 실제 계산과 브라우저 검사

`examples/stack_replay_workflow.py`가 직접 작성한 MCAP와 실제 decoder/kernel을 사용했다.
처음 두 독립 실행은 time_headway_s=1.5/3.0의 저장 stack r1/r2다. 세 번째 실행은 Case UI에서
r1을 기준으로 r2를 선택하여 다시 계산했다. 기준 전체8tick 중 Case 원본에 직접 대응하는 것은1tick이다.

| 관측 | 결과 |
| --- | --- |
| 기준 전체 순서 | 8개 ego tick과 원래 predecessor LiDAR 선택 유지 |
| Case 재생 결과 | 독립 r2 실행의 모든 decision/control과 일치 |
| 변경된 요청 | 1tick; index2에서 approach→obstacle_stop |
| 입력 불가 조건 | index0/3/5의 unknown_stop 유지; no-past/empty/stale를 정상 여유 공간으로 바꾸지 않음 |
| Case 개정 | r1원본→r2실행연결→r3결과고정, 조사state=open유지 |
| 응답 유실 | 제출 후 응답을 끊고 재시도해도 같은 job과Case 개정 반환 |
| 이전 근거 | Case r1과 baseline export byte/hash 유지 |
| 내보내기 | 전체8행·전체시도 JSON의 SHA-256 일치 |
| 화면 탐색 | 정확한 candidate trace로 이동, 새로고침 후 결과 보존 |

이것은 authored geometry의 **open-loop 출력 요청 비교**다. 실제 차량 반응·센서 물리·학습형
E2E 모델·실차 정확도를 인수하지 않았다. 출력 요청은 실제 차량과 다음 기록 입력에 적용되지 않는다.
0/실패 tick을 제외해 점수를 개선한 시험도 아니며, 새 safety/comfort/ODD 통과 판정은 만들지 않는다.

개발 Chrome에서 새 기능·기존 모델재평가·조건분류3개업무를 확인했다.
별도 source/UI 고정패키지에서도 새 recorded stack workflow와 기존 ONNX/COCO·Case/retest
통합업무2개를 확인했다. 기존 package coverage검사는 자신의 recording으로 범위를 명시하여,
다른 업무의 Case가 존재해도 올바른 분모를 확인하도록 바꿨다.
Desktop/mobile screenshot과 가로넘침·browser pageerror 검사를 보존했다.
새장비cleaninstall은 아니며 기존Python의존성환경을사용했다. 사용자 토큰 값은 기록에 포함하지 않았다.

## 백업·복원·실제 API 재시작

브라우저 검사가 끝난 별도데이터의69파일 cold backup을 새폴더에복원했다.
그복원본에서 API를두차례시작/종료하며 Case r3, 전체8tick, 변경요청1개, 동일report hash,
Case r1·baseline bytes를대조했다. SDKdownload도크기/hash를확인했다.
인증없는조회는401이며 worker나CARLA를시작하지않았다.
API종료코드-15는소유한테스트프로세스에보낸SIGTERM이다. 정상드레인품질을이결과로확대하지않는다.
고정패키지의별도종료receipt는API/worker를각각기록한다.

## 회귀와 최초 검사 수정

새경로·SDK·기존recorded stack의첫선별검사는62개통과했다.
최종 전체Python회귀는2,844개통과(453.01초), 마지막관련회귀는159개통과(29.63초)했다.
두범위는중복되므로합산하지않는다. frontend타입검사와build도통과했다.
전체회귀의최종수와보존검사는`docs/validation/case-stack-replays-20260928.json` 및
`.runtime/case-stack-integration-20260928/`의실제로그에서확인한다. 중복범위를합산하지않는다.
Python의기존Starlette/httpx및AnyIO폐기예정경고는별도유지한다.

최초 반례검사는 '다른 recording'용 fixture가 동일 bytes여서 기존 import 중복방지에 걸렸다.
원본ego속도가다른파일을생성하도록fixture를수정했다. 제품의중복방지를완화하지않았다.
선별시험출력후shell종료코드변수에zsh읽기전용`status`를썼던wrapper도수정했다.
이는pytest의실패가아니며원래62개통과로그를보존했다.

## 저장·운영 경계

DBschema12유지,새Case/5만추가하며이전개정은그대로남는다.
조건·메모·기존참조시험·이미지모델재평가의후속개정이Case/5를낮춰저장하지않도록검사한다.
원본`.data`및Gitindex의작업전후hash는최종검증JSON에서대조한다.
커밋·운영배포·원격CARLA실험·native차량설정변경은수행하지않았다.
