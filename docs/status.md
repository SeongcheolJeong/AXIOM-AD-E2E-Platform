# 개발 상태 · 2026-10-27

## GitHub 소스·작은 예제 DB 배포 · 2026-10-27

운영 작업공간과 Git index를 보존한 별도 소스 스냅샷을 준비했다. Object 참조
통과/실패, Log 원본/open-loop, Log feedback의 SQLite 3개와 연결 산출물을
동봉하며 CARLA 없이 복사·조회·재생·export할 수 있다. 샘플은 새로 작성한
합성 입력의 실제 CPU 참조 계산이며 native CARLA·실물·센서 검증이 아니다.
최소 설치의 NumPy 누락과 Vite 보안 lock을 수정했다. 업로드 사본의 새 환경에서
Python 57개, Chromium 3개 검사와 frontend build를 통과했고 npm audit은 0건이다.
외부 엔진·모델·실도로 데이터와 과거 대용량 근거는 별도이며 상용 release gate는 남는다.
[설치](github-installation.md) · [샘플과 재검사·화면](github-verification.md).

## 한국 지도 내부 베타 · 세종 OSM native 주행과 우회전 미통과 · 2026-10-24

Object Sim의 `한국·외부 지도`에 원본 bytes·출처·권리와 불변 개정을 보존하고,
변환 진단 → 큐 기반 CARLA 지도 확인 → 근거가 일치하는 지도 게시 → 시나리오
작성으로 연결하는 UI/API/SDK를 추가했다. configured adapter와 현재 readiness,
변환 후보와 실제 엔진 확인을 분리하며 외부 원본 지도는 읽기 전용이다.

실제 세종 OSM 원본에서 좌표 변경 없이 8 ways·15 nodes를 선택했다. 약 1.639 km는
선택 중심선의 길이 합계이며 주행 거리가 아니다. 사전 `Osm2Odr` 변환 결과는
16 roads·3 junctions다. 일반 API에 XODR와 상위 OSM 등 5첨부를 보존하고 원본
export bytes를 대조했다. 기본 폭 3.5 m·누락차로수·생성교차로·고도/신호정책 부재를
명시하며 NGII 측량 정밀도로지도로 표시하지 않는다.

실제 `job_2e7f835782134bd88ef6780a1578c42d`에서 candidate/readback SHA가
일치했고 drivable nodes 1,682·spawn points 20, cleanup confirmed를 확인했다.
기존 2100 공통 lease를 사용한 지도 로드·차로 읽기 범위다. map catalog r1 게시와
네 시험 개정의 API 작성을 완료했다. 별도 3 m/s 실제 주행에서 직진 41.123 m·
좌회전 41.450 m가 기준을 통과했다. 우회전은 실행 완료·접촉 0이지만 횡오차
p95 0.715182 m가 0.5 m 기준을 넘어 fail이다. 기준과 실패 원본은 유지한다.
일반 UI에서 좌회전 r1의 속도·duration·이름을 편집해 r2를 저장하고 실제 실행·
재생으로 이어갔다. 좌회전 2 m/s r2는 41.394 m·횡오차 p95 0.305802 m로 pass,
600 native / 150 RGB / drop 0이다. 실제 4실행은 3 pass·1 fail이며 별도로 작성한
2 m/s r1은 미실행이다. 우회전 frame 272·13.65 s를 실제 UI에서 Case r1로 저장하고
새로고침 복원·봉인 download·SDK get/export의 4,305 bytes 및 SHA 일치를 확인했다.
정확한 retained JSON 프레임·실행·지도 계보를 보존하며, reactive planner event가
없는 route-follow Case의 거절 문제를 수정하고 story 45·Case 8개 회귀를 통과했다.
지도 로드 receipt의 `driving_evaluated=false`와 별도 경로 제어 결과를
구분하며 실물·센서 검증을 주장하지 않는다.
NGII 원본은 없고 선택 변환기의 31검사는 합성 semantic SHP에 한정한다.
[기능·원본·검증 경계](korea-map-pilot.md) ·
[실제 지도 확인 receipt](../.runtime/korea-map-20261024/qualification-first.json).

[최종 운영 확인](../.runtime/korea-map-20261024/runtime-final.json)에서 본 베타
active job 0, 2100 OpenDriveMap의 dynamic actor 0, 공통 lock free·quarantine 없음과
기존 엔진 관련 PID 4개 보존을 확인했다.
[소개 HTML](../reports/axiom-korea-map-introduction-20261024.html)과
[베타 안내 HTML](../reports/axiom-korea-map-beta-guide-20261024.html)의 링크·JS 문법·
미디어 참조 정적 검사는 통과했다. 브라우저 `file://` 정책 차단으로 HTML 시각
검토는 미실시이며 실제 제품 UI 인수와 구분한다. 상용 release gate는 미완료다.

## 추력 차량 시험장 · Kickflip native A/B · 2026-10-23

Object Sim에 불변 시험 개정 저장 → 실제 CARLA 실행/취소 → 외부·차량 영상과 관측
재생 → A/B·URL 복원·export를 추가했다. UI와 Python SDK는 같은 버전 API와 기존
Experiment 큐를 사용한다. 원본 자세 행렬로 명령을 재계산하고 힘·충격량 한도,
연속 프레임, 영상 디코딩, 접촉·착지 판정을 검증한다.

기준 10ms와 5ms는 약360° 회전 및 데모 착지 기준을 통과했고, 추력30%는
최대상승0.08m·회전 미완료로 미통과했다. native3조건·취소 정리, 실제 영상의
브라우저 A/B·시점/개정 복원·export를 확인했다. 관련 Python103개·기존37개,
브라우저 작성/모킹 계약2개와 build가 통과했다. 초기 실패는 보존했다.

Ramp는 Blender 4.2.23 LTS 설치와 FBX 3개 변환·57개 hull의 형상 재검사를 완료했다.
Unreal import/cook 코드는 준비했으나 실제 실행은 미검증이다. 호환 UE4.26 소스
접근 권한과 빌드 공간이 없어 native Ramp는 차단한다.
[설치·변환·남은 조건](validation/stunt-ramp-toolchain-20261023/acceptance.md).
일반 스포츠카의 가정한 강체·추력
모델이며 Tesla 재현이나 실제 차량·탑승자 안전 검증이 아니다.
[기능 계약](stunt-lab.md) · [실행·비교·검증 근거](validation/stunt-lab-20261023/acceptance.md).

전용 API/worker 종료로 생긴 프록시500을 복구하고, 세션 분리·로그·프로세스
identity 확인을 갖춘 시작 도구를 추가했다. 화면은 연결 실패 시 준비 상태를
무효화하며 재시도로 A/B 영상·시점을 복원한다. 시작 도구22개, 브라우저3개와
build 통과; 기존 실제 영상6파일 해시 및 사용자 화면 복구를 확인했다.
[서비스 복구 기록](validation/stunt-lab-20261023/api-recovery.md).

## 6.75초 FPGA 재주입 · 해제 후보 재취소 · 2026-10-18

직전5.85초 전원 강하 원본을 유지하고, CH0에6.75초200µs 정체를 추가한 새
native RTL·ECU·vCAN·CARLA A/B를 실행했다. 기존2.5초 source를4.5초·채널당
135프레임으로 확장했다. 4×1080p30·100MHz 그대로 정상540commit,
고장536commit/4abort, 비고장 채널135commit씩이며 픽셀/프로토콜 오류0이다.
이전 원본과 공통300프레임씩의 해시·상태·terminal·epoch도 일치했다.

입력6.75초의 해제 후보가6.80초 FPGA 재검출로 취소됐고, 보호 중 CAN87건·
Brake435건 모두 제동100%였다. 전체 해제는
6.805512→7.405788초,
CARLA 재이동은7.00→7.60초다.
보호 정책은 유지하고 실제 후보 순번/완료 구간과 최소 hold를 검증했다.
720개 제어·1,200개 측정 창의 원본 대조, API/SDK·기존 export 보존,
625개 로컬 검사·UI29개·build가 통과했다.

브라우저 인수는 정책 차단으로 미검증이다. 참조 ECU·가상 CAN·CARLA와 봉인
측정 재생이며, 원본 종료 뒤 tail_hold와 물리/안전 검증의 경계를 유지한다.
[CAN·ECU·CARLA A/B와 근거](validation/hil-recurrent-fpga-6750-20261018/acceptance.md).

## 5.85초 전원 재주입 · 해제 후보 재취소 · 2026-10-18

직전5.25초 FPGA 재발 원본을 재사용하고, native 전원 해석을4초로 늘려
5.85–6.15초6V 강하를 추가한 새 ECU·vCAN·CARLA A/B를 실행했다.
최대20µs와 보호 정책은 유지했다. 실제 native 표본은 정상200,008개/고장200,080개다.
요청/실제 표본250,000개·native64MiB·연결 artifact32MiB의 제한을 유지한다.

입력5.85초의 해제 후보가5.90초 전원 재검출로 취소됐고, 보호 중 CAN75건·
Brake375건 모두 제동100%였다. 전체 해제는
5.905587→6.805512초,
CARLA 재이동은6.10→7.00초다.
후보 생성이 처리 위상에 따라 한 주기 늦어질 수 있음을 실제 보고 순번과
가변 위상 단위 재현으로 검증했다. 최초 고정순번 검사 실패도 보존했다.
720개 제어·1,200개 측정 창의 원본 대조, API/SDK·기존 export 보존,
620개 로컬 검사·UI29개·build가 통과했다.

브라우저 인수는 정책 차단으로 미검증이다. 참조 ECU·가상 CAN·CARLA와 봉인
측정 재생이며, source 완료 뒤 tail_hold와 실물/안전 검증의 경계를 유지한다.
[CAN·ECU·CARLA A/B와 검증 근거](validation/hil-pending-power-20261018/acceptance.md).

## 5.25초 FPGA 재주입 · 해제 후보 취소 · 2026-10-18

해제 검증 대기 중 5.25초에 CH0 정체 200µs를 추가해 native RTL·참조 ECU·vCAN·CARLA를
새로 실행했다. 기존 1초 source로는 늦은 고장을 주입할 수 없어 4×1080p30·100MHz를
유지한 75프레임/채널·2.5초 원본을 양쪽 새로 만들었다. 정상 300commit,
고장 297commit/3abort, 다른 채널 75commit씩, 픽셀/프로토콜 오류 0이다.

입력 5.25초의 해제 후보가 5.30초 FPGA 재검출로 취소됐다. 보호 중 CAN 57건·
Brake 285건이 모두 제동 100%를 유지했다. 전체 해제는 5.305826→5.905587초,
CARLA 재이동은 5.50→6.10초다. 같은 정상 side의 360개 제어가 일치했고,
720개 차량 제어·1,200개 측정 창을 원본과 대조했다. UI에 실제 입력 프레임 수
설정과 ‘해제 후보 취소 관측’을 추가했다. 후보 취소는 ECU 보고 상태 전이의
관측이며, 정확한 내부 취소 시각으로 표시하지 않는다. 로컬 검사 545개·UI 29개·build가 통과했다.

브라우저 워크플로는 정책 차단으로 미검증이다. 봉인 native 측정 재생이며 source
완료 뒤에는 마지막 관측 상태를 유지한다. 실물 ECU/전원·하드 실시간·안전 검증과 구분한다.
[CAN·영상 A/B와 검증 근거](validation/hil-pending-fpga-20261018/acceptance.md).

## 50ms 경계의 보호 해제 검증 개선 · 2026-10-18

복합 모델의 마지막 개별 보호가 풀리면 바로 제동을 해제하던 경로에
`fresh-window-v2` 검증을 추가했다. 해제 후보 뒤 연속 두 완료 측정구간을 확인해
후보 이후 온전한50ms 구간을 포함한다. 새 이상·누락·반복·미래·순번 건너뜀·
입력나이10ms 초과는 해제를 막는다. 기존500ms·정상3표본·8V 기준은 유지했다.
개별 고장이 없어도 전체 제동이 유지되는 `해제 검증 대기`를 UI에 구분했다.

보존한 원본 감시 코드와 개선 코드를 같은500개 타이밍 조합에 적용했다.
기존255개 조합의50ms 공백을 재현했고 개선 후에는0개였다(단위 재현).
별도로4.349/4.350/4.351/4.399초 전원 재주입을 각각 native 모델·vCAN·CARLA로
독립 실행했고 모두5ms 조건을 통과했다. 보호 중ADAS CAN182건·Brake910건에
중간 해제가 없고 차량제어2880개·native 측정4800개를 원본과 대조했다.
동일4.35초 기준 전체 해제는5.255772→5.305826초,재이동은CARLA5.45→5.50초다.

기존/개선 A/B는 두 봉인 출처의 실행기·설치 차이가 감시 코드 한 파일에만 있는지
검증하고 로직 변경을 화면에 명시한다. 기존 결과·export는 유지하고,구계약의
새 실행은 개선 binding 저장 전 거절한다. backend/adapter/SDK637개·UI28개·
build가 통과했다. 브라우저 인수는 기존 정책 차단으로 미검증이다.
검증 범위는 기록한50ms 측정·입력나이≤10ms·주입/처리 위상이다. 최신 구간 이후
새 고장의 미래예측·실물 ECU·하드 실시간·안전 보장은 포함하지 않는다.
[CAN 파형·CARLA A/B·반복 검증 근거](validation/hil-release-qualification-20261018/acceptance.md).


## 4.35초 전원 강하 재발·FPGA 보호 해제 교차 · 2026-10-18

전원 모델에 재주입 일정·최대 해석 간격을 추가하고,1.75초 native ngspice A/B를
실행해4.35–4.65초6V 강하를 기존3.8초 FPGA 재발 시험에 연결했다. 첫 강하는
3.003–3.303초 그대로다. 최대20µs·87,556 native 관측점·1:1 재생이며 기존
4×1080p30·100MHz FPGA 원본과경계별500ms·정상3표본 정책은 유지했다.

새 CARLA A/B는5ms 조건을 통과했다.4.405초 처리 주기에서FPGA 보호 해제와
전원 재검출이 교차해도제동100%를 유지했고,전체 보호 해제5.255772초 뒤
CARLA5.45초 관측에서 재이동했다(기존4.60초). 보호 중ADAS CAN44건·Brake220건에
제동0이 없고720개 차량 제어가 수신CAN과 일치했다. 개정·preview·export·인과·
단독/복합 반복 표식,backend/adapter/SDK/SPICE59개·UI26개·build가 통과했다.

**남은 위상 경계:**50ms 측정 구간 때문에4.35초 강하가4.40초 입력에 관측된다.
다른 합법적 스케줄 위상에서FPGA500ms 보호가4.35초에 해제되면50ms command
공백이 가능한 authored unit counterexample을 보존했다. 이번 실행에서는 공백이
없지만일반 무공백/안전 보장은 아니다. 입력 관측 age를 고려한전체 release
qualification과모든경계위상 검증은후속이다. 보호 시간을 조정해 결과를 통과시키지
않았다. 브라우저 인수는 기존정책차단으로미검증이며실물검증이 아니다.
[실행·A/B 영상·검사·위상 한계](validation/hil-power-recurrence-20261018/acceptance.md).


## 3.8초 FPGA 정체 재발·전원 보호 해제 교차 · 2026-10-18

FPGA 모델에 재주입 일정 편집·저장·native 실행을 추가했고, 단일/복합 연결의
주입 지도와 인과 표식에 모든 구간을 보존한다. 이전/현재 고장 측 A/B 화면은
첫 검출과 FPGA 재검출, 개별/전체 보호 해제를 구분해 영상 커서로 이동한다.
빈 재주입 목록은 기존 단일 정체 요청의 serialization/hash를 유지한다.

3.8초100µs 재주입은 프레임 초기 버퍼가 흡수해 두 번째 abort·ECU 재검출이
발생하지 않았다. 이 결과를 보존하고 시작은3.8초 그대로, 지속200µs로 새 시험을
실행했다. 4×1080p30·100MHz·1초 입력을 유지해 정상120완료,고장118완료/2무효,
비의도 픽셀·프로토콜 오류0을 확인했다. 기존400ms ngspice 전원 원본을 재사용했다.

새 CARLA A/B는5ms 조건을 통과했다. FPGA 재검출3.855662초 뒤 전원 보호가
3.855702초에 해제돼도 FPGA 보호가 유지됐다. 전체 해제는4.405669초,
CARLA 재이동 관측은 기존4.05초에서이번4.60초로 늦어졌다. 전체 보호 구간의
ADAS CAN27건·Brake 출력135건에 제동0이 없고720개 차량 제어가 수신 CAN과
일치했다. backend/adapter/SDK47개·UI 해석25개와build가 통과했다.
브라우저 클릭·새로고침 인수는 정책 차단으로 미검증이며 실물 검증은 아니다.
[기존/재주입 A/B와 근거](validation/hil-model-recurrence-20261018/acceptance.md).


## 복합 고장 회복 순서 역전·실행 간 A/B · 2026-10-18

HIL Bridge에 `복합 고장 · 이전 실행 A / 현재 실행 B` 비교 축을 추가했다.
두 실행의 고장 측 CARLA 영상과 남은 보호 원인·경계별 회복 버튼을 같은 커서로
조사하며 `compareModel`로 선택 실행을 복원한다. 원본 시험·seed·경계·보호 정책·
실행기·설치·fidelity가 다른 조합은 거절하고 각 영상의 원본 실행·해시를 유지한다.

새 native 전원 모델을400ms 해석하고6V 강하를300ms 유지했다. 기존100µs FPGA
정체 원본을 재사용해 FPGA가 먼저 회복되는 새 CARLA 정상/고장 A/B를 실행했다.
두 경계 보호 설정은500ms·정상3표본으로 유지했다. FPGA 보호 해제3.556초 뒤에도
전원 보호가 남은6표본에서 제동100%·정지를 확인했고, 전체 해제3.856초 이후
CARLA4.05초 관측에서 재이동했다. 새 실행은 기존5ms 조건을 통과했다.

기존 전원 먼저 회복 결과와 최종 해제 입력 시점이 같아 정상/고장각360개 차량
제어값이 모두 일치했다. 입력3.70초에 남은 보호 원인은 기존FPGA·이번전원으로
바뀐다. backend/adapter/SDK124개, UI 해석23개, 공유 영상 회귀 검사와 build가
통과했다. 브라우저 정책 차단으로 일반 브라우저 인수는 미검증이며 모델 재생·
참조 ECU·가상 CAN·CARLA 범위를 유지한다.
[실행·비교 영상·검증 근거](validation/hil-model-recovery-order-20261018/acceptance.md).

## 전원·FPGA 복합 고장과 독립 회복 · 2026-10-18

HIL Bridge의 모델 연결에서 동시 감시 모델을 추가하고, 저장된 모델별 정상/고장
실행과 재생·보호 조건을 함께 저장한다. 참조 ADAS는 각 경계를 독립 감시하며
하나라도 보호 중이면 제동을 유지한다. 화면은 남은 보호 원인, 개별 경계 회복과
전체 보호 해제를 구분하고 기존 CARLA A/B·시간 커서·URL·export를 재사용한다.

전원6V 강하(차량축3.003–3.007초)와 CH0 FPGA ready300ms 정체
(3.005–3.305초)를 겹친 새 native/CARLA 시험이 기존5ms 조건을 통과했다.
전원 보호 해제3.605초 이후에도 FPGA 보호가 남은3.60–3.80초 입력 표본에서
제동100%·정지를 유지했다. 전체 보호 해제3.856초 뒤 제동이 풀리고
CARLA4.05초 영상 관측에서 재이동했다. FPGA는4×1080p30·100MHz를 유지해
정상120프레임, 고장110완료/10무효를 검사했고 비고장3채널은각30프레임 정상이다.

복합 초기화가 기존 단일 DUT 메시지 제한을 넘은 첫 실패는 보존했다.
게이트웨이의 기존8MiB 제한에 맞춰 전송 경계를 수정했고, 종료·CAN 무송신·잠금
해제를 관측한 뒤 별도 새 실행으로 통과했다. backend/adapter/SDK123개,
UI 해석19개와 production build가 통과했다. 브라우저 정책 차단으로 일반 브라우저
인수는 미검증이며, 봉인 모델 출력 재생·참조 ECU 검증 범위를 유지한다.
[실행·영상·독립 검사](validation/hil-model-overlap-20261018/acceptance.md).

## 모델 출력 → Virtual ECU → CARLA A/B · 2026-10-18

HIL Bridge에서 RAW·FPGA·전원·아날로그의 완료된 native 정상/고장 출력을 선택하고,
고장 없는 CARLA Network Lab 시험에 연결해 새 A/B를 실행한다. 참조 ADAS의 측정
감시 → CAN 명령 → 독립 Brake 보고 출력 → CARLA 제동·회복을 같은 화면과
시간 커서에서 조사한다. 모델 설정·실행·binding 해시와 선택 위치를 보존한다.

네 종류 모두 실제 Verilator/ngspice 출력과 UDP·분리 vCAN·CARLA로 실행했고,
최종 4쌍이 기존 5ms 조건을 통과했다. 총 2,880개 측정값을 원본 모델과 대조하고
차량 제어값을 수신 CAN bytes와 대조했다. 초기 RAW/FPGA 시간 조건 미충족 결과는
보존했다. 모델 입력 주기를 차량 요청과 분리하고, 제한된 CARLA 캡처 동안의
Python cyclic GC를 조정했다. 참조 ECU 판단과 최소 보호 조건은 유지했다.

현재 연결은 **봉인된 모델 출력의 1:1 시간 재생과 참조 측정 감시**다. 50ms 구간의
짧은 abort·전압 이탈을 보존하며 원본 종료 후 마지막 관측 상태를 유지한다.
픽셀 인지·CSI PHY·실물 ADC/전원·양방향 RTL/SPICE 연동은 미구현이다.
UI 해석 18개와 backend/adapter/SDK 115개 검사, production build가 통과했다.
브라우저 정책 차단으로 클릭·영상 연동·새로고침의 일반 브라우저 인수는 미검증이다.
[실행·경계·A/B 링크](validation/hil-model-loop-20261017/acceptance.md).

## HIL 고장 주입 확장 · 2026-10-17

HIL Bridge에서 참조 ADAS 리셋·제어 이벤트 누락 일정을 추가하고 vCAN/UDP·CARLA
A/B로 실행한다. 별도 모델 주입 패널은 RAW valid 단절·FPGA 출력 정체를 4×1080p30
Verilator에, 전원 강하·센서 오프셋을 ngspice 예시 회로에 연결한다. 설정 저장,
동일 API/SDK 실행, 원본 artifact/export와 실행 URL을 재사용한다.

실물 리셋/OS 드라이버, CSI PHY, FPGA 장치 I/O, 전원 공급기·아날로그 장비 연동은
미완료다. RTL/SPICE 모델은 CARLA·실제 ECU와 연결되지 않았으며 이 경계를 화면에
명시한다. 현재 브라우저 정책 차단으로 클릭·새로고침 인수는 미검증이다.
[구현 범위](hil-bridge.md) · [native 실행·검사 근거](validation/hil-boundary-injections-20261017/acceptance.md).

## HIL 주입 지도·설정 안내 · 2026-10-17

HIL Bridge 상단에 객체 UDP·Restbus·CAN 중계·출력 피드백의 클릭형 지도를 추가했다.
선택 위치의 주입값·원본 시험 개정, 참조 ECU 감시 기준, 시험기 스케줄 허용오차를
분리하고 원본 편집·피드백 설정·장비 설정으로 연결한다. 전체 지원 범위는 같은
profile API를 사용한다. 실물 표시에는 프로필과 일치하는 준비 결과 및 두 장치
identity가 필요하며, 가상 ECU 준비 결과를 실물 연결로 표시하지 않는다.

선택 위치와 지원 범위 보기는 URL에 보존한다. 현재 Binding hash와 일치하는 저장
결과만 관측 시각으로 연결하며, 편집 중인 조건·미관측 검출·실행 중단을 구분한다.
UI 해석 검사 23개와 backend/adapter/SDK 계약 검사 26개, production build가 통과했다.
브라우저 접근 승인 거부로 이번 화면의 클릭·레이아웃·새로고침 인수는 **미검증**이다.
실행기 변경과 새 native/실물 실행은 없으며 이전 실행·인수 기록은 유지했다.
[변경·검사 근거](validation/hil-injection-map-20261017/receipt.json).

## SIL → HIL Bridge · 가상 버스 연결 검증 완료 · 2026-10-17

기존 SIL 시험 개정을 선택해 HIL Binding 저장 → 장비/주입 경계 점검 → 실제
UDP·분리 vCAN 실행 → CARLA 영상·ECU/CAN 인과 A/B → 복원·export를
UI/API/SDK에서 수행한다. 사용자 승인 후 전용 UI5340/API8240와 격리 Linux
게이트웨이를 배치했고, 기존 UI5330/API8230·SIL 소스·봉인 결과는 유지했다.
최종 어댑터에서 정상/객체 시험 2쌍과 CARLA 고장 5쌍(각 A/B 360표본,
18초/측, seed42)이 5ms 조건을 통과했다. 127개 로컬 검사·브라우저 인수 통과.
피드백 상실 중단·취소·정리와 미지원 기능 거절도 확인했다.
초기 시간 초과 4건은 보존하며, 실물 ECU 연결·하드 실시간·물리 검증은 미완료다.
[기능과 남은 경계](hil-bridge.md) · [실행 인수](validation/hil-bridge-20261017/acceptance.md).


## Network Lab 검출→CAN→차량 시각 비교 · 2026-10-17

원본 trace의 고장 주입·ECU 검출·CAN 송신/수신·제동 적용·차량 readback을 연결하는
읽기 전용 인과 분석 API와 SDK를 추가했다. 화면에서 400ms UDP 지연과 Restbus 단절의
B측 실행을 나란히 비교하고, 시각을 눌러 해당 실행의 영상·ECU 구간으로 이동한다.
실행·시험·seed·manifest/trace 해시와 원본 JSON 위치를 보존한다.

기존 실제 CARLA 실행에서 검출은 각각 3.250/3.091초, CAN 송신은 3.250/3.100초,
제동 적용은 3.300/3.150초다. 주입 후 제동 적용은 300/150ms이며, 400ms 지연값을
검출 대기시간으로 해석하지 않는다. 첫 제어 readback과 이후 최초 속도 감소도 구분했다.
영상 촬영·ECU 입력·차량 관측 시각과 영상 offset을 별도로 표시한다.

현재 검출 인과 추적은 UDP 지연·Restbus 단절을 지원하며 다른 고장은 지원 불가로 남긴다.
원본 검출 이벤트·CAN 전달·제어 readback 누락을 추정으로 대체하지 않는다. 분석/계약
54개와 기존 UI 해석 7개, production build 및 보존 실행 브라우저 재개를 확인했다.
새 native 실행 또는 물리 제동 검증은 아니다.
[실행·API/SDK·브라우저 근거](validation/vecu-network-causality-20261017/receipt.json).

## Network Lab A/B 조건·선택 시점 한글 해석 · 2026-10-17

실행 receipt에 고정된 A 고장 미주입 / B 고장 일정·구간을 영상 위에 표시한다.
영상 아래에는 고장 위치 → ECU 보호·제어 → 다음 차량 반응을 두 행으로 요약하고,
기존 수신·OS·BSW·제동 경로는 펼쳐서 확인한다. `정상 CAN 명령`을
`수신 CAN 명령 적용 중`으로 변경해 명령 유효성과 적용 제동량을 분리했다.

보존된 실제 CARLA 실행에서 6.90초 복합 고장·제동 100%, 7.15초 고장 주입 종료 뒤
Brake 회복 확인·제동 유지, 7.25초 제동 해제와 아직 낮은 차량 속도를 구분했다.
URL 재개·영상 SHA 확인·상세 펼침을 확인했고 UI 해석 7개, API/adapter/SDK 42개
검사와 production build를 통과했다. 표시 변경이며 새 native 실행은 하지 않았다.
[검증 근거](validation/vecu-network-explanation-20261017/receipt.json).

## Worker import 오류·로그 증가 복구 · 2026-10-17

장시간 실행 중인 worker가 새 editable 패키지의 entry-point 등록 정보는 발견하지만
시작 후 추가된 `.pth` 경로는 읽지 못해 `axiom_engineering`, `axiom_isp_rtl`,
`axiom_vecu_network` import 오류를 매 polling마다 기록한 문제를 수정했다.
worker 시작 시 handler inventory를 한 번 검증·고정하고, import 오류는 준비 표시 전에
실패 종료한다. 일시적 polling 오류는 최대 60초 backoff와 반복 traceback 억제를 적용한다.
CLI와 내부 런처는 private 회전 로그(기본 10 MiB × 활성 1개 + 백업 3개)를 지원한다.

실패 worker 14개의 환경·작업 상태를 보존해 복구했다. 기존 로그 22,955,056,885바이트는
전체 SHA-256을 대조한 237,500,033바이트 압축본으로 보관했다. 기존 API/UI는 재시작하지 않았다.
최종 60초 관찰에서 14개 API의 worker heartbeat가 갱신됐고 오류 로그 증가는 0바이트였다.
기존 작업 ID·상태는 동일하며, 동시 중복 정리 이후 현재 내부 여유는 약 28.7 GiB다.
관련 backend/worker 검사 61개, 내부 런처 검사 8개, 실제 ngspice RC 실행 2건을 포함한
브라우저 A/B·재개·다운로드 흐름 1개를 통과했다.

별도 디스크 정리 작업의 1,197개 APFS 공유 복사본 전환과 겹친 파일 변경을 감지해
이 복구 작업의 추가 삭제는 중단했다. 기존 경로·보존 결과를 유지하며 해당 정리의 완료 기록을
재확인했다. [원인·복구·최종 관측 근거](validation/worker-log-repair-20261017.json).

## 통신 회복 경계의 6.9초 ADAS 리셋 · 2026-10-17

Restbus [3,7)초와 ADAS 리셋 [6.9,7.1)초를 새 CARLA A/B로 실행했다.
`job_29945db7947e4c428309a9e16619e34f`: 720개 제어·차량 적용 단계, 영상 720프레임,
28개 artifact와 export hash를 확인했다. 저장/API/adapter 검사 40개와 UI build도 통과했다.
Restbus 중 시작한 200ms 리셋이 통신 회복 경계를 가로지르는 저장 규칙을 지원한다.

heartbeat 3개는 7.001/7.011/7.021초, 리셋 후 새 제동 명령은 7.101/7.151/7.201초에
도착했다. 해제 요청 2개가 먼저 도착했어도 Brake의 3개 신규 명령 회복 조건이 제동 100%를
유지했다. 실제 제동 해제는 입력 7.25초, 속도 >0.1m/s는 응답 7.45초다. 기존 5초 리셋의
제동 해제 7.10초·재출발 응답 7.30초보다 150ms 늦었다. 두 정상측의 차량 상태·제어는 동일하다.

첫 오프라인 검사의 수신 요청/적용 제어 혼동은 수정했고 실패 검사 receipt/source를 보존했다.
독립 검증기는 7.15초의 가상 조기 해제 출력을 거부했다. 이는 별도 오프라인 검증기 검사이며
실제 CARLA 고장 실행으로 세지 않는다. [새 실행·원본 감사](validation/vecu-network-restbus-reset-boundary-20261017/receipt.json) ·
[5초/6.9초 비교](validation/vecu-network-restbus-reset-boundary-20261017/5s-vs-6_9s.md).

## Restbus 단절 중 ADAS 리셋 · 새 CARLA 실행 · 2026-10-17

Restbus [3,7)초 안에 ADAS 리셋 [5,5.2)초를 중첩한 시험을 새로 저장·실행했다.
이 조합만 제한적으로 중첩을 허용하며, 다른 고장 중첩은 저장 단계에서 거부한다.
고장별 시작/종료 커서와 동시 고장 표시, ADAS epoch/reset 카운터를 연결했다.

`job_72c91da7747840b8a8eb5c9b89a79f30`: 실제 CARLA 정상/복합 고장 18초 A/B.
720개 native 제어·차량 적용 단계와 영상 720프레임, 모든 artifact/export hash를 확인했다.
5초 리셋 후 epoch 1, 5.2초 제어 재개에도 heartbeat 보호와 제동 100%가 유지됐다.
7.05초 tracking, 7.10초 제동 해제, 7.30초 차량 응답에서 속도 >0.1m/s를 관측했다.
제어 시각은 입력 기준이며 차량 응답은 50ms 뒤다. API/SDK/adapter 계약 검사 39개와
production UI build가 통과했다. 브라우저에서 리셋 종료 사건 이동, 6초 URL 재개,
회복 영상 2개의 SHA 검증·제동·속도 표시도 확인했다. 전체 AUTOSAR·실물 ECU·주행 안전성 검증은 아니다.
[새 실행·영상·상태](validation/vecu-network-restbus-reset-20261017/comparison.md) ·
[원본·제어 감사](validation/vecu-network-restbus-reset-20261017/receipt.json).

## Virtual ECU Network Lab · 실제 CAN/Ethernet·공개 BSW·OSEK · 2026-10-17

일반 UI/API/SDK에서 네트워크·시험 개정 저장 → 실제 Linux native 실행 →
프레임/OS/BSW/제동 A/B → CARLA 영상 → URL 재개·export를 연결했다.
네 역할 Perception/ADAS/Brake/Restbus가 SIL Kit5.0.8의 실제 CAN/Ethernet 프레임을
교환하며, 제동은 공개 autoas Com/PduR/CanIf와 Trampoline OS의 실제 Task/Alarm/Event를
통과한다. C++ 제어 프로그램은 OS의 제어 요청 후 별도 프로세스에서 실행한다.

최종 native 인수 시험은 정상+7고장, 동일 입력3회, 정확한100ms watchdog 경계를
통과했다. 3,640step의 native 결과를 독립 감사했고 세 정상 실행의 논리 hash가 같다.
[실제 native 인수](validation/vecu-network-20261017/native-acceptance-r3/receipt.json) ·
[컴포넌트·범위](validation/vecu-network-20261017/native-validation.md) ·
[제품·실행 계약](virtual-ecu-network-lab.md).

실제 CARLA의 18초 정상/고장 A/B 7쌍(차량 실행 14회)도 완료했다. 공통 seed 42,
20 Hz와 고장 [3,7)초를 유지했다. 저장된 5,040개 제어 단계의 native 결과와 CARLA 적용
명령을 독립 재검사했고, 196개 artifact 및 전방·추적 영상 5,040프레임의 원본 해시를 확인했다.
UDP 지연·단절·반복은 같은 freshness 정책으로 3.25초 degraded, 3.30초 전제동,
7.15초 tracking을 보였다. CAN 명령 단절과 OS 이벤트 누락은 3.10초 전제동으로 같지만,
실제 C++ 실행 수는 각각 359회/279회로 고장 위치가 구분된다. 200 ms ADAS 리셋은
3.35초에 tracking으로 복귀했다. 시각은 입력 기준이며 CARLA 관측 프레임은 50 ms 뒤다.
[7종 CARLA A/B·실행 링크](validation/vecu-network-20261017/carla-comparison.md) ·
[전체 원본·제어 감사](validation/vecu-network-20261017/carla-checks.json).

초기 geometry 오류·저장 공간 부족·압축 계약 불일치 실패 3건과 대기 취소 1건은
별도 보존하고 성공 횟수에서 제외했다. 새 Network Lab 영상은 무손실 ZIP DEFLATE로
저장하며 BGRA 픽셀·해상도·선택 프레임·원본 해시는 유지한다. 이전 영상은 원본 ZIP을 보존한다.

현재 범위는 기능 수준 가상 프레임, 공개 BSW 일부, OSEK OS 서비스다.
`WITH_AUTOSAR=NO`; 전체 AUTOSAR/RTE/MCAL, OEM 신호 DB, SOME/IP/UDS,
물리 버스·중재 타이밍·MCU WCET, Silver 호환은 구현/검증하지 않았다.
CARLA actor ground truth가 제어 입력이며 영상 인지는 연결하지 않았다. 주행 안전성은 `not_evaluated`다.
8bit CAN 카운터 때문에127개 이상 연속 명령 기회가 사라지는 일정은 저장 단계에서 거부한다.
UI5330/API8230은 별도 workspace·remote 설치를 사용하며 이전 서비스·결과를 보존한다.

## C/C++ ECU 패키지 빌드와 CARLA A/B · 2026-10-17

Virtual ECU Lab에서 등록 C++ 소스 선택 → 실제 Linux 빌드 → 패키지 A/B 등록 →
동일 시험 실행 → 동기 영상·신호·이벤트 조사 → 정확한 URL 재개·export를 수행한다.
A는 기존 즉시 전제동 참조 정책, B는 300 ms 제동 상승·500 ms 복귀 정책이다.
소스·컴파일러·바이너리·패키지·실행 hash를 보존하며 실제 제어 명령은 C++ 프로세스가 계산한다.

정상·400 ms 지연·입력 단절·패킷 반복의 18초 CARLA A/B 네 쌍(차량 실행 8회)을 완료했다.
공통 seed 42, 20 Hz, 고장 [3,7)초를 유지했다. 정상 제어·속도는 A/B가 같았다.
지연 조건의 감지→정지 이동 거리는 A 0.564 m / B 0.817 m, tracking 복귀는
A 7.10초 / B 7.60초다. 완만한 제동이 관측상 더 긴 정지 거리를 만들었으며 안전성 개선으로 판정하지 않는다.

관련 backend/native/adapter/SDK 검사 206개와 고정 재생 6개를 통과했다.
실제 빌드 등록·새 native 고정 재생·보존 CARLA 영상의 브라우저 흐름 3개와 production UI build도 통과했다.
고정 재생 3회의 정책별 논리 hash가 일치했다. 12개 export와 140개 artifact의 hash를
재검증했다. 최초 source identity 불일치 실패는 별도 보존하며 성공 실행에 포함하지 않는다.
[실행 기록](validation/vecu-cpp-20261017/receipt.json) ·
[통합 검사](validation/vecu-cpp-20261017/checks.json) ·
[A/B 수치·검증 경계](validation/vecu-cpp-20261017/comparison.md) ·
[기능·계약](virtual-ecu-lab.md).

UI 5320/API 8220은 별도 workspace와 remote source directory를 사용한다. 기존 서비스와 결과를 보존했다.
이 패키지 경로는 등록된 두 C++ 참조 애플리케이션이며 임의 코드 업로드, 양산 ECU,
Silver 호환, 전체 AUTOSAR·FMU·Qualcomm 내부 실행과 물리 HIL은 미연결이다.
기능 수준 CAN/Ethernet·공개 BSW·OSEK 실행은 위의 별도 Network Lab에 연결했다.

## CARLA 패킷 반복 전달·검출 · 2026-10-17

Virtual ECU Lab에서 input_repeat을 저장·실행하고 영상의 중복/미수신 카운터를 확인할 수 있다.
새 reference DUT v2에서 반복 80회와 단절 80회를 구분한다. 중복 payload 변경을 거부하고,
중복 패킷으로 freshness 또는 신규 패킷 3회 회복 조건을 만족시킬 수 없다.
3–7초 repeat/drop/400ms delay를 실제 CARLA에서 각각 새로 실행했다. 세 조건 모두
3.25초 degraded → 7.00초 recovering → 7.10초 tracking이며 제어·속도 차이는 0이었다.
이는 현재 동일한 freshness 기반 완전 제동 정책의 결과이며 고장 전달 방식이 같다는 뜻은 아니다.
이전 v1 delay/drop과의 제어·속도 차이도 0이다. 관련 검사 216개와 production UI build 통과.
[실행/개정](validation/vecu-repeat-20261017/receipt.json) ·
[비교/검출 근거](validation/vecu-repeat-20261017/comparison.json).
UI 5310/API 8210 및 별도 remote source directory를 사용한다. 기존 서비스·결과는 유지한다.
실제 물리 ECU/네트워크/CSI 검증은 아니다.

Virtual ECU 영상 후속: 새 CARLA 실행 `job_603ab17dbeb04c549722a7ed33342e00`에서
정상/400 ms 지연·회복을 완료했다. 두 카메라 원본의 SHA 검증·동기 재생·전방/추적 전환을
ECU 상태·제동·다음 속도와 연결했다. 요청 시각과 제어 적용 후 촬영 시각은 50 ms 차이를 명시한다.
[실행·화면 검사](validation/vecu-carla-demo-20261017/checks.json).

## Virtual ECU Lab · 2026-10-17

호스트 ADAS reference 프로세스의 패키지/객체 입력/시험 개정 → native 실행 → A/B 조사 →
정확한 URL 재개·trace/export를 일반 UI/API/SDK에 연결했다. 20 Hz 고정 재생과 기존 CARLA
폐루프 실행을 분리한다. 정상·지연·중단·반복·설정 A/B 및 반복성 시험, 실제 CARLA 등
공개 SDK 실행 8개가 완료됐고 artifact/export hash를 확인했다. 관련 검사 92개 통과.
[기능·계약·제한](virtual-ecu-lab.md) · [실행 근거](validation/vecu-20261017.json).

현재 reference application이며 양산 ECU, Qualcomm 내부, Silver 호환, 실제 HIL 검증이 아니다.
카메라 RTL과 RGB→객체 인지는 연결하지 않았다. 기존 결과와 서비스를 유지하며 새 UI 5300/API 8200을 사용한다.

## ADAS 보드 전 검증 캠페인 · 2026-10-17

새 `보드 전 검증 캠페인` 화면에서 10초 연속 입력, seed별 초기화 비교, 반복 reset,
동시 고장과 FIFO 경계 sweep을 개정 저장 → native 실행 → 프레임 조사 → 정확한 재개·export로 연결했다.
진행 수치는 임시 상태이며 최종 판정은 sealed 실행 근거에서만 읽는다. 실제 full-HD 장기 실행
결과는 [공개 SDK 실행 기록](validation/adas-preboard-20261017.json)에 보존한다.

실제 공개 worker 캠페인 **19/19 통과**: 4×1080p30의 10초 연속 입력에서 1,200프레임을
모두 확정했다. seed 42/7/2026, seed당 8회 reset, 3채널 동시 고장과 FIFO sweep까지
총 1,477프레임 확정 / 의도한 35프레임 폐기이며 확정 영상·프로토콜·폐기 영상 공개 오류는 0이다.
고정 입력 위상의 FIFO 점유는 62 µs 정체에서 4,038/4,096, 64 µs에서 overflow였고
다음 프레임 회복을 확인했다. 참고 4상태 검사 8개도 통과했다. 동일 RTL의 별도 공개 OOC는
103.295 MHz 통과이며 campaign 885개와 build 31개 artifact 및 sealed export를 모두 재해시했다.
19개 캠페인 통과와 실제 보드 준비 여부는 별도 판정이다.

기존 전체 해상도 6개 시험도 새 RTL로 재실행해 통과했다. 이전 실행과 RAW 720개,
확정 RGB 717개, 폐기 3개 프레임의 상태/부분 해시 및 모든 지연이 일치했다(변화 0 ns).
이전 101.368 MHz → 새 103.295 MHz이며 LUT +171, FF +364, BRAM/DSP 동일하다.
이 비교에는 RTL retiming과 배치 옵션 변경이 함께 포함된다. 이전/새 artifact 3,300개와
14개 sealed export를 재해시했고 공유 저장소에서도 확인했다.
[회귀 및 A/B 근거](validation/adas-preboard-regression-20261017-comparison.md).

4상태 검사에서 원본 BLC의 미구동 상위 비트가 유효 픽셀에 X/Z를 만드는 결함을 발견했다.
MIT 원본은 보존하고 명시적 zero extension 파생 RTL을 생성한다. 메모리 cascade write와
FIFO 읽기/hold 경로도 retiming했다. 같은 seed 42·100 MHz에서 ADAS 전용 nextpnr
`placer_heap_timingweight=30`을 실행 identity와 manifest에 고정했고, **103.295 MHz**를
정식 빌드에서 동일 netlist/배선 hash로 재현했다. 최종 full-HD RTL은
`2998314794b4fe5b345019a2cc69219651fafdf4ff120d92b1e67ec5bf927a18`이다.
LUT 9,906 / FF 10,012 / BRAM 48 / DSP 4를 사용한다. 이전 101.368 MHz 결과는 이전 RTL의 역사적 근거다.

Icarus의 실제 생성 core 제어 초기화·BLC·16×16 4채널 픽셀 검사와 별도 비동기 FIFO/reset
참고 모델, 잘못된 reset/commit 대조군을 실행한다. full-HD X 전파나 물리 CDC/RDC signoff는 아니다.
예시 보드는 사용자가 선택을 위임한 **ULX3S 85F**이며 공식 소스 hash·제어 핀 subset·
25→100 MHz PLL 생성 파일을 보존한다. 3 Mbit/s UART는 제어/저속 전송용으로,
2.986 Gbit/s RAW 입력을 실시간 전달할 수 없다. 실제 CSI·출력 전송·board top·I/O 제약과
물리 HIL은 미설정이다. 예시 파일을 보드 연결 준비 완료로 표시하지 않는다.

새 UI 5280/API 8180은 별도 데이터 폴더를 사용하며 이전 3,267개 artifact를 독립 복사·재해시했다.
이전 5260/8160과 5270/8170 서비스는 유지했다.
[구현·한계](adas-preboard-validation.md) · [검사](validation/adas-preboard-checks-20261017.json) ·
[타이밍 재현/실패 근거](validation/adas-preboard-timing-closure-probes-20261017.json).

## ADAS Camera Module RTL · 100 MHz 타이밍 개선 · 2026-10-17

`HIL → FPGA Lab → Engineering Lab → ADAS Camera Module`에서 픽셀 영상/파형 A/B와
별도의 **ECP5 빌드 A/B**를 함께 선택하고 정확한 URL 재개·export를 수행한다.
기존 실행을 보존한 로컬 UI 5270 / API 8170에서 개선 전후를 직접 비교할 수 있다.

프레임 경계 검사 → abort → 이벤트 큐 경로를 fault 등록과 terminal retirement로
분리했다. 이어서 CSR 실행 단계, FIFO·MHC 라인 출력의 elastic register, WB 곱셈과
포화 단계 분리로 다음 임계 경로도 줄였다. 채널별 4096픽셀 FIFO, 독립 ISP 4개,
100 MHz, seed 42와 **4×1920×1080 RAW12·30 FPS·입력 1초** 조건을 유지했다.

동일 최종 RTL hash의 실제 Yosys/nextpnr 공개 API 빌드는 **100 MHz OOC 통과**다.
Fmax **82.1423 → 101.3685 MHz**, LUT **9747 → 9735**, FF **9152 → 9648**,
BRAM **48**, DSP **4**다. 목표 대비 주파수 여유는 **1.3685 MHz**이며 경로별 slack과
구분한다. 평가용 LFE5U-85F/CABGA381/speed6 프로필과 도구 hash도 동일하다.
기존 12.174 ns 경계/이벤트 병목은 해소됐고 최장 경로는 MHC 라인 RAM 출력의 9.865 ns다.

전체 해상도 6개 시험을 새로 실행했다. 정상·WB·30 µs 정체는 각각 120프레임 완료,
100 µs overflow·reset·입력 중단은 각각 119개 완료 + 의도한 CH0 한 프레임 폐기 후
회복했다. 확정 영상·프로토콜 오류는 모두 0이며 입력 중단의 194개 성분 차이는
폐기된 부분 영상 기록에 보존한다. 전후 입력 720개와 확정 RGB 717개의 hash가 일치한다.
RGB 출력은 30 ns, 정상 프레임 확정은 40 ns 늦어졌다. 고장 프레임의 부분 출력 수와
abort 시각 변화는 별도로 기록했으며 정상 결과로 간주하지 않는다.

backend/native/comparer 182개 검사, 실제 worker 브라우저 2개 흐름, 보존 빌드 A/B
브라우저 1개 흐름과 production build를 통과했다. 브라우저 새 실행은 32×24 기능 검사이고,
1080p 인수 근거는 별도의 공개 SDK 전체 해상도 실행이다. 전후 원본 artifact 3267개와
sealed export 14개를 재해시하고 새 API에서 14개 실행의 manifest hash를 확인했다.
기존 UI 5260/API 8160, OpenISP 결과와 타이밍 실패 근거는 유지했다.

실제 보드·PHY·HIL은 미연결이다. OOC 통과는 bitstream·핀·CDC·I/O 타이밍 검증이 아니며,
`hardware_target`은 계속 실행 불가를 반환한다. 다음 게이트는 정확한 보드·I/O 경로 확정과
실물 FPGA 비교다. Qualcomm 내부 IP·CPU/GPU/NPU 복제와 상용 release gate는 별도다.
[사용·구현 경계](adas-camera-module-rtl.md) ·
[개선 전후 A/B](validation/adas-module-timing-closure-20261017-comparison.md) ·
[새 전체 실행](validation/adas-module-timing-closure-20261017.json) ·
[검사](validation/adas-module-timing-closure-checks-20261017.json) ·
[타이밍 개선 probe](validation/adas-module-timing-closure-probes-20261017.json) ·
[초기 실패 근거](validation/adas-module-rtl-20261017.json).

## Open ISP RTL · 2026-10-17

`HIL → FPGA Lab → Engineering Lab → OpenISP RTL`에서 RAW 입력·BLC/WB 설정을
개정 저장하고 실제 Verilator 픽셀 실행, 정수 참조·RGB·차이 영상 A/B, 전체 top-port
VCD, 정확한 재개와 export를 연결했다. AMD mini-isp MIT 소스와 독립 정수 oracle을
사용하며 완료 프레임 RAM 뒤에서 MHC를 실행한다. 기본128×96, 최대320×240·2프레임이다.

보존 실행은 픽셀7건과 별도 ECP5 OOC 빌드1건이며 원본271개와 sealed export8개의
hash/크기를 재확인했다. 기본25,393cycles에서 실제 출력 중 정체는26,393cycles,
reset 재생은25,456cycles로 늘었고 출력 픽셀 hash는 동일하다. WB 변경은 동일 RAW에서
다른 RGB를 만들며 모든 정상 픽셀 실행의 정수 참조 불일치는0이다. 최초 출력 전 정체
실행도 효과 없음으로 해석을 붙여 보존했다.

ECP5 LFE5U-85F/CABGA381/speed6 OOC는 LUT1,040/FF1,118/BRAM13을 사용했고,
100MHz 목표에 대해 보고된 내부 경로 Fmax107.27MHz로 통과했다. 실제 보드·IO 타이밍이나
bitstream 검증은 아니다. GW2A/Gowin·CycloneV/Quartus는 프로젝트 템플릿과 미실행 상태만
제공한다. Apollo SM8750의 구조 참고이며 SA8650 내부 또는 Qualcomm bit-exact 구현이 아니다.

관련 backend/adapter110개 검사, 실제 worker의 새 OpenISP3개·기존 Engineering3개 브라우저
흐름, production build를 통과했다. 기존 bundle 크기 경고는 남는다.
새 로컬 UI5250/API8150 및 `e2e/.runtime/open-isp-demo`를 사용하며 기존 서비스를 유지했다.
[사용·범위](open-isp-rtl.md) · [실제 실행/hash](validation/open-isp-rtl-20261017.json) ·
[검사 근거](validation/open-isp-rtl-checks-20261017.json). 상용 release gate는 계속 열려 있다.


## Engineering Lab · 사용자 RTL / 아날로그 회로 · 2026-10-14

`HIL → FPGA Lab → Engineering Lab`에서 사용자 `.v/.sv` 소스나 SPICE netlist를
편집·가져오기 → 개정 저장 → 실제 Verilator/ngspice 실행 → 파형 A/B → 정확한 재개·
원본 다운로드로 연결했다. UI/API/SDK가 같은 immutable 시험·worker 계약을 사용하며,
소스·설정·seed·도구/adapter/공유 runner 식별과 모든 결과 hash를 보존한다.
RTL은 고정 32-bit ready/valid 단일 클록에서 Identity/XOR 기대 동작 또는 관찰 모드를
선택한다. 컴파일 오류·프로토콜/데이터 오류·timeout·관찰 완료를 구분한다.
SPICE는 self-contained R/C/L/V/I/D 과도응답이며 실제 adaptive 표본 시각을 유지한다.

최종 native 실행 6건에서 정상/정상 XOR는 각각256워드 통과, 의도한 오류 XOR는 첫25ns부터
256불일치, RC의 약1ms 출력은1kΩ 0.632139V / 2kΩ 0.393484V,
RLC 최대 출력1.854467V를 확인했다. 아티팩트69개와 sealed export6개의 hash/크기를 검증했다.
관련 backend/adapter/SDK 검사220개(신규 API48 + native/관련 회귀172), 실제 worker 브라우저
3개 흐름(새 실행7건, 소스 수정·실패·A/B·재개·다운로드·좁은 화면), production build를 통과했다.
기존 큰 frontend bundle 경고는 남아 있다.

별도 로컬 UI5240/API8140과 `e2e/.runtime/engineering-demo`를 사용한다. 사용자 RTL은 명시적으로
허용한 비공유 로컬 운영자 환경에서만 실행하며 검증된 OS sandbox라고 주장하지 않는다.
Workbench 그래프에 사용자 모듈 배치, 임의 포트/다중 클록, RTL-SPICE 혼합 신호 공동 실행,
실제 FPGA 보드 검증은 후속이다. CSV 표본은 상승 에지 직전 값이고 VCD 같은 시각은 에지 후 값이다.

**Qualcomm 실제 내부 구현은 연결되지 않았다.** 지정 자료 폴더의 원본 PDF8개 hash는 확인했지만
현재 연결된 대상 칩용 실행 런타임·지원 호스트가 없고 사용 권한·공식 인터페이스 확인도 필요하다. SA8650/SM8750/SM8850
연결 profile을 저장하고 준비 조건을 볼 수 있으며, 미설정 preflight는422로 거부하고 실행을 만들지 않는다.
Apollo 참조 모델 결과를 Qualcomm 실제 실행으로 대체 표시하지 않는다. 상용 release gate는 계속 열려 있다.
[사용·계약·실행 링크](engineering-lab.md) · [최종 native 근거](validation/engineering-lab-20261014.json) ·
[검사 근거](validation/engineering-lab-checks-20261014.json) · [Qualcomm 로컬 확인](validation/qualcomm-local-readiness-20261014.json).

## FPGA Workbench · 2026-10-14

`HIL → FPGA Lab → FPGA Workbench`에서 블록 추가·이동·포트 배선·삭제·Undo/Redo로
Source → 1–4 직렬 FIFO → Sink 회로를 작성하고, 그래프가 생성한 SystemVerilog를
실제 Verilator로 실행한다. 독립 SimPy 모델과 동일 자극을 비교하며 논리 파형·데이터·
버퍼·전송 계측기, 공통 클록 커서, 엔진/시나리오 A/B와 정확한 개정·시점 재개를 연결했다.
프로브·패널·위치 변경은 실행 해시를 유지해 재실행 없이 보존 신호를 조사한다.
미완성 회로는 저장할 수 있고 잘못된 배선·계측 연결은 실행 전에 해당 위치와 함께 거부한다.

4096워드·100 MHz·32비트의 최종 native 실행 4개에서 SimPy/RTL 불일치0을 확인했다.
1/2단 FIFO 정상 지연은 각각1/2클록, 2단 정체·회복은4096개 전달·최대128개 버퍼·입력130클록 정체,
리셋은4062개 전달+34개 명시적 폐기다. 원본56개 artifact와 sealed export4개의 hash/크기를 검증했다.
관련 backend/adapter/SDK 검사152개, 실제 worker 브라우저2개 흐름과 production build를 확인했다.
브라우저는 팔레트 추가→직접 포트 배선→새 실행→프로브 변경→A/B→재개·다운로드,
미완성 저장과 timeout 실패 표시를 포함한다. 큰 frontend bundle 경고는 남아 있다.

별도 로컬 UI5230/API8130과 독립 데이터 폴더를 사용한다. 기존 서비스·결과는 유지했다.
저장 파형 탐색은 엔진 일시정지가 아니다. Workbench 그래프 내 사용자 RTL 노드·CDC·
혼합 신호·실제 FPGA 보드·Qualcomm 내부 구현 검증은 후속이다. 독립 사용자 RTL/SPICE 실행은 상단 Engineering Lab에 연결했다. [사용·모델 계약](fpga-workbench.md) ·
[최종 native 실행 근거](validation/fpga-workbench-final-20261014.json).

## FPGA RTL Lab · 2026-10-14

`HIL → FPGA Lab → RTL A/B`에서 참조 SystemVerilog FIFO와 독립 SimPy 모델을
동일 자극으로 실행한다. 개정 저장·실제 Verilator 컴파일/실행·클록 파형·엔진/시나리오 A/B·
정확한 재개·VCD/CSV/JSON 내보내기를 UI/API/SDK로 연결했다.
100 MHz·32비트·깊이64·4096워드 보존 실행 4개에서 클록별 불일치0을 확인했다.
정상4096개 전달, 정체·회복4096개 전달/입력193클록 정체, 반복 정체842클록,
리셋4063개 전달+33개 명시적 폐기다. cycle-budget timeout은 trace를 보존하고 검증 실패로 표시한다.
관련 backend/adapter/SDK 검사111개, 실제 worker 브라우저 2개 흐름(정상·정체·리셋·timeout), production build를 확인했다.
[사용·모델 계약](rtl-lab.md) · [실행/hash 근거](validation/rtl-lab-20261014.json).
별도 로컬 미리보기는5220/API8120이며 기존 Apollo·SA8650 서비스는 유지했다.
고정 Stream32 사용자 RTL과 독립 SPICE 실행은 상단 Engineering Lab에 연결했다.
범용 포트·CDC·혼합 신호·실제 보드 타이밍·Qualcomm 내부 구현 검증은 후속 범위다.

## Apollo SM8750 Camera Lab · 2026-10-14

별도 Apollo target에서 1–4채널 RAW 입력, TFE/OFE/IPE CPU 영상, 공유 DDR·큐 타이밍,
Preview/ZSL을 개정 저장 → worker 실행 → 영상·타이밍 A/B → 정확한 재개·export로 연결했다.
1080p 4채널 120프레임 실제 모델 실행에서 정상 120개 기한 내 완료, CSI 12개 폐기,
OFE 63개 폐기, DDR 99개 폐기와 ZSL Snapshot 3/3 완료를 보존했다.
73개 관련 backend/adapter 검사, 실제 worker 브라우저 2개 흐름, frontend build를 확인했다.
문서 구조와 가정한 성능의 CPU 참조 모델이며 Qualcomm bit-exact/SA8650/RTL/물리 검증은 아니다.
상세 범위·사용법·남은 작업은 [Apollo Lab](apollo-lab.md), 실행 근거는
[20261014 receipts](validation/apollo-lab-20261014.json). 상용 release gate는 계속 열려 있다.

## SA8650 Target ECU Lab · 2026-10-14

`HIL → FPGA Lab → SA8650 Target ECU`에서 1–4채널 송신과 ECU profile, 명령·고장 일정을
개정 저장하고 실제 SimPy 실행·사건/프레임 재생·수신 큐·원인·A/B·정확한 재개·JSON/CSV를
UI/API/SDK로 수행한다. 모든 채널이 하나의 시간축과 공유 CSI·처리기를 사용한다.
기존 FPGA Lab v1·Software-DUT HIL과 실행 종류를 구분하며 기존 서비스는 재시작하지 않았다.

1080p RAW12 보존 실행에서 정상60/60, 준비 전 송신57/60, 링크 단절57/60(정상 채널30/30),
4채널 CSI 병목108/120·deadline120, 처리 적체61/120·폐기59를 확인했다.
VC 중복 예제는422 사전 거부이며 실행 성공으로 세지 않는다. 수치는 가정한 모델의 결과다.
독립 oracle·고장 경계·권한/개정/SDK·기존 FPGA/HIL/Experiment 검사85개와 실제 worker를 쓰는
브라우저 ECU2개·기존 FPGA2개 흐름, production build를 확인했다. React Flow 노드/선 표시와 좁은 화면도 확인했다.
기존 큰 bundle 경고는 남아 있다.

새 로컬 미리보기는5199/API8099, 독립 DB는`e2e/.runtime/ecu-demo`다. 처리율·지연·queue·감시
정책은 모두 model assumption이다. 실제 SA8650 firmware/ISP/RTL/PHY/보드 검증은 미포함이며,
다음은 실제 보드 포트·startup/control·실측 지연으로 별도 profile을 보정하는 단계다.
[사용·모델 계약](ecu-lab.md) · [실제 실행/hash 근거](validation/ecu-lab-20261014.json).

## FPGA Lab v1 · 2026-10-13

`HIL → FPGA Lab`에서 합성 RAW 1채널의 고정 경로를 편집·개정 저장하고 실제 SimPy
CPU 계산을 제출할 수 있다. DDR/FIFO 점유율, Trigger/송수신 파형, 사건·프레임 재생,
원인 사건, 저장 결과 A/B, 정확한 개정·시점 복원과 JSON/CSV를 UI/API/SDK에 연결했다.
정상·대역폭 부족·공급 지연·타이밍 고장 네 예제를 실행했고 정상 60/60 수신,
60 FPS 대역폭 부족 99/120 수신·21 overflow, 공급 지연 60 deadline 초과,
고장 주입 6 누락을 모델 결과로 확인했다. 실제 FPGA/RTL/PHY/ECU 측정은 아니다.

집중 backend/기존 HIL/Experiment 검사 53개, 실제 SimPy 브라우저 workflow 2개와
production build를 확인했다. 브라우저에서 제출 응답 유실 후 새로고침·재시도에도
실행 1건을 유지한다. 로컬 8098/5198 미리보기와 별도 DB를 준비했으며 기존 서비스는
재시작하지 않았다. 일반 build의 큰 bundle 경고는 남아 있다.
다음은 사용자 설계 조건 피드백이며 RTL 예제·실제 RAW·보드 연결은 별도 단계다.
[사용·모델 계약](fpga-lab.md) · [실제 계산 근거](validation/fpga-lab-20261013.json).

## Sensor 배치 편집 베타 · 2026-10-11

`Sensor Sim → 센서 배치 · Beta`에서 전방·후방 RGB, LiDAR, Radar의 B 위치·회전을
수치와 상단/측면 도식으로 바꾸고 이름·메모·개정·복제·정확한 재열기를 사용할 수 있다.
고정 A와 20초 참조 주행 프로필을 유지하며 기존 주행 adapter/Experiment queue의 preview·
실행을 정확한 배치 개정/hash에 연결한다. 결과에는 실행 당시 배치를 표시하고 선택 센서의
A/B 공통 시간축·시점 관찰·JSON을 제공한다. 공통 베타의 카메라 방향·LiDAR 높이·Radar 방향
과제와 개정·실행·관찰·의견·이어하기도 연결했다. UI/API/Python SDK가 같은 계약을 사용한다.

격리된 인증 로컬8088에서 카메라 yaw −15° 저장 r1, LiDAR Z 2.5m 저장 r2,
범위 밖 입력 거절·새로고침·r1 복원·복제와 안내 과제의 r2 이어하기·피드백을 확인했다.
두 브라우저 JSON 다운로드는 내보낸 시각을 제외하면 SDK와 일치한다.
backend/SDK 통합121개, 기존 수집기/데모67개, frontend56개와 production build가 통과했다.
관찰 원본·중복 제출·잘못된 개정 연결은 작성한 fixture의 계약 검사이며 native 인수가 아니다.

이후 사용자 승인으로 HIL→Sensor를 잠시 전환해 저장 r2의 실제 A/B
`job_ef6750df40a0431e86480accc3d9069c`를 완료했다. A/B 각각 20초·200tick·센서 관측800개,
네 stream별200개·누락0이며 소유 객체 정리·world 설정 복원·lease 해제를 확인했다.
정상 브라우저에서 네 센서의 A/B 재생, 9.9초 LiDAR 관찰 저장·새로고침·정확한 시점 복원과
JSON의 SDK 일치를 확인했다(`exported_at` 제외). 연결 베타 과제는 새 실행·관찰 완료로 기록했다.
**크게 보기**는 페이지 안 확대와 Escape 닫기로 동작한다. 개발자 업무 인수이며 실제 참가자 완료는 아니다.

종료 후 HIL2100·8075를 복원했고 Sensor8088은 엔진 미설정의 작성·보존 결과 재생 상태다.
새 Sensor 실행에는 GPU 사용 시간과 엔진 준비가 필요하다. 상세 물리 검증·독립 참가자3–5명 인수·
초대·공개 운영은 후속이다. [기능과 운영 경계](sensor-sim-beta.md) ·
[오프라인 작성 인수](validation/sensor-layout-beta-20261011.json) ·
[실제 실행·브라우저·운영 복원](validation/sensor-layout-native-20261011.json).

## ADP 공통 베타 작업공간 · 2026-10-11

정상 시작 화면에 준비 상태, 프로젝트 과제·이어하기, 재현 정보가 붙는 문제 신고와 운영자
진행·처리·JSON/CSV를 추가했다. Object·HIL의 정확한 저장 개정·실행·관찰을 연결하고 Log의
기존 안내 과제·의견은 원본을 유지한다. 공통 API·Python SDK·agent tool이 같은 계약을 사용한다.
Object의 실행 전 이어하기는 실제 도시 편집기에 지정 개정을 복원한다. 원래 결과와 다른 실행을
보고 있는 경우 잘못된 과제로 의견이 연결되지 않도록 확인한다. 참가자별 프로젝트 경계와
운영자 전용 집계를 유지하며 설정만 있는 엔진은 실행 준비 완료로 표시하지 않는다.

격리된 브라우저에서는 Object 개정 저장 연결·새로고침·정확한 편집기 복원·신고, Log/HIL 기존
화면 이어하기, 다른 참가자의 접근 차단, 운영자 처리·내보내기를 확인했다. 새 관찰을 기존 API로
저장해 보존 결과 체험 과제에 연결하고 재접속 후 복원했다. 집중 검사94개, gateway141개,
최신 frontend build와 브라우저8개 흐름을 확인했다. 작성한 fixture를 썼으며 새 CARLA·센서·ECU
실행 근거가 아니다. 실행 엔진 동작을 바꾸거나 기존 서비스를 재시작하지 않았고 실제 참가자
초대·사용 인수·공개 배포는 남아 있다. [사용과 준비](beta-workspace.md) ·
[검증 기록](validation/beta-workspace-20261011.json).

## HIL 앞차 감속·정지 후 재출발 · 2026-10-11

④ 장면 확대를 UI/API/SDK에 연결했다. 기존 Town10 base에서 앞차 감속과 정지 후
재출발을 선택하고, 고장 시간·DUT 프로필과 함께 정확한 개정/hash로 저장한다. 원래
시나리오 hash와 선택한 앞차 일정 hash를 구분하며 이전 정지 장면과 영수증은 유지한다.
입력 일정과 실제 응답 시점의 앞차 속도·사건을 A/B에서 함께 조사할 수 있다.

감속 `job_12d1d59d58024dd8bd1d1257e9701f26`은 400ms 지연 3–7초 조건으로 완료했다.
입력 4초의 2→0.5m/s 요청 뒤 양쪽 앞차는 응답 4.15초에 ≤0.75m/s로 감속했다.
정지 후 재출발 `job_1afbb7648d694d14b805441d6da4ae03`은 단절 8–9초 조건으로 완료했다.
입력 4초 정지·8초 재출발 뒤 실제 속도는 4.20초에 ≤0.2m/s, 8.45초에 >0.5m/s였다.
두 작업의 각 A/B는 제어 360개·RGB 360장·누락 0이며 종료 정리를 확인했다.
기존 15건의 영수증 bytes를 보존했고 8075는 같은 DB·자격으로 `launch-scenes.py`를 사용한다.
새 native 소스는 독립 디렉터리에 배치했으며 CARLA 2100은 재시작하지 않았다.

감속의 정상 브라우저 저장·실행·관찰·JSON export와 재출발의 SDK 저장·실행을 확인했다.
재출발 결과도 정상 이력에서 native RGB 재생·8.45초 관찰·URL 복원을 확인했고,
두 장면의 브라우저 JSON은 내보낸 시각을 제외하면 SDK와 일치한다. 브라우저 오류는 0건이다.
기존 정지·감속·재출발 3조건 목록은 UI 저장·새로고침을 확인했으며 시도 0건인 작성 인수다.
native/adapter 191개, backend/SDK 216개, frontend 81개와 production build를 통과했다.
종료 뒤 프로세스별 GPU 조회 정상·같은 2100 프로세스를 확인했다. 로컬 여유 공간은 약
2.08GiB이며 다음 native 출력 용량을 먼저 확인해야 한다. 보존 자료 삭제는 하지 않았다.
이는 기존 base의 제한된 앞차 행동 2종이며 새 지도·독립 장면 다양성이나 안전 커버리지가 아니다.
다음은 **사용자 DUT**다. 실제 참가자 업무 인수·공개 운영·실제 ECU·하드 실시간은 남아 있다.
[범위](hil-sim-beta.md#고정-장면-선택-계약) · [근거](validation/hil-scenes-20261011.json).

## HIL 3–5조건 순차 실행 · 2026-10-11

`시험 관리 → 순차 실행`에서 저장된 시험의 정확한 개정 3–5개와 순서를 고정하고,
준비 확인 후 서버가 한 조건씩 실행한다. 화면을 벗어나거나 새로고침해도 진행 기록을
다시 열 수 있다. 중단·실패 뒤에는 종료와 자원 정리를 확인하고 미완료 조건만 명시적으로
다시 실행한다. 원본 receipt·trace hash에 묶인 DUT 열화·추적 회복 시각과 최대 입력 age·
제동 요약에서 실제 A/B와 시점 관찰로 이어지며 같은 API를 Python SDK에서도 제공한다.

목록 `hilbatch_7c73213b9dc64d9a97e3f8e9faaaeb87`은 r14에서 3조건 완료·5시도를 보존한다.
실제 실행 중 취소 2건과 완료 3건이며, 300ms 정상/고장 비교·400ms 정책 비교·단절 정책
비교의 각 A/B에 제어 360개·RGB 360장·누락 0을 확인했다. 첫 중단은 완료 조건이 없을 때,
두 번째는 앞의 두 조건 완료 후 세 번째 실행 중에 요청했다. 유휴 상태에서 수정 API를
재시작한 뒤 남은 1조건만 준비 확인·재개했고 앞의 두 결과를 다시 실행하지 않았다.
다음 조건의 접수는 이전 조건의 종료 뒤에 이루어졌으며 기존 영수증 10건의 bytes와
catalog 56개 행을 보존했다. 기존 엔진 2100의 프로세스 식별자는 그대로이며 종료 후
자원 idle·잔여 객체 0과 프로세스별 환경의 GPU 조회 성공을 확인했다.

정상 Engineer 브라우저에서 작성·순서 변경·저장·복제·새로고침, 2조건 목록 차단,
준비 확인·시작·중단·재개와 배치 화면을 떠난 동안의 진행을 확인했다.
400ms 결과의 2.30초 관찰을 저장하고 정확한 새 실행·응답 시점의 URL 복원과 영상 재생·
일시 정지를 확인했다. 브라우저 JSON은 내보낸 시각을 제외하면 SDK와 같으며 이 묶음의
새 관찰 1건만 포함한다. 최종 브라우저 오류는 0건이다.
집중 검사 247개 후 완료 기록 보존·취소 경합 수정의 관련 72개를 다시 통과했다.
frontend 73개 후 관련 19개와 production build를 확인했다. 각 재검사 수에는 중복이 있다.
다음 접수 실패 뒤 완료 조건을 잘못 재실행할 수 있던 경로는 작성한 handler 검사로 재현·
수정했다. 실제 엔진 실패를 주입한 결과가 아니며 최종 native 세 번째 조건은 수정 API로 완료했다.
실제 실행은 3조건이며 5조건 경계는 계약 검사로 확인했다.

이 인수 당시 다음 단계였던 ④ 장면 확대는 위 별도 기록으로 이어진다. 기존 18초 Town10 앞차 정지 장면과 CARLA 객체 GT를
입력하는 참조 Software DUT 범위이며 조건 3개를 독립 장면 수나 안전 검증 커버리지로 세지 않는다.
실제 참가자 업무 인수·공개 호스팅·실제 ECU·하드 실시간은 남아 있다.
[범위](hil-sim-beta.md#순차-실행-계약) · [근거](validation/hil-sequential-20261011.json).

## HIL DUT 임계값·회복 프로필 · 2026-10-11

`시험 관리`에서 이름 있는 DUT 프로필을 저장·개정·복원하고 A/B에 각각 고정해 같은 고장
조건의 반응을 비교한다. 입력 age 임계값은 50–1000ms·50ms 단위, 회복 조건은 연속 fresh
입력 1–10회다. 프로필 최신 개정이 바뀌어도 저장 시험·실행의 개정과 hash는 유지된다.
기존 정상/고장 비교도 그대로 열 수 있다. 동일한 참조 Software DUT의 정책 비교다.

Engineer 브라우저에서 A 250ms/3회 r1과 B 500ms/8회 r2를 만들고 B의 500ms/3회 r1 보존을 확인했다.
기존 시험 r4의 400ms·2–5초 지연 실행 `job_3acf348b96584fafbd8cbba89ff0b75c`을 완료했다.
3.55초에 입력 age는 양쪽 400ms였고 A는 정지·제동 100%, B는 2.530m/s·제동 0%였다.
A는 2.30초 degraded 진입·5.15초 tracking 회복, B는 tracking을 유지했다.
SDK로 만든 단절 2–3초 시험의 `job_9b19439acaf845a3bca67caefa032b58`도 완료했다.
degraded 진입은 A 2.30초/B 2.55초, tracking 회복은 A 3.15초/B 3.40초였다.
두 실행의 각 A/B는 제어 360개·RGB 360장·누락 0이며 종료 정리와 기존 8건의 hash 보존을 확인했다.

backend/SDK/native 통합 273개, frontend 62개를 통과했고 마지막 시간 표시 문구 수정 후
관련 13개와 production build를 다시 통과했다. 브라우저에서 프로필 작성·개정·범위 오류 차단,
지연 시험 준비·제출·영상·3.55초 관찰 저장·새로고침과 오류 0건을 확인했다.
단절 이력의 3.20초에서 A tracking·제동 0%, B recovering·제동 100% 관찰을 저장했다.
브라우저 JSON 다운로드와 두 시험의 SDK export에서 프로필·실행·메모·표본 연결을 확인했다.
8075는 같은 DB·자격을 유지해 갱신했고 기존 HIL 2100을 재사용했다.
이 인수 당시 다음 단계였던 ③ 순차 실행은 위의 별도 기록으로 이어진다.
④ 장면 확대도 위 별도 기록으로 이어지며 사용자 DUT·실제 ECU·하드 실시간은 후속이다.
[범위](hil-sim-beta.md#dut-프로필-계약) · [근거](validation/hil-dut-profiles-20261011.json).

## HIL 고장 시간 편집 · 2026-10-11

`시험 관리`에서 지연·단절의 시작·종료를 0.5초 단위로 편집하고 개정 저장→native 실행→
A/B 조사를 정상 UI/API/SDK로 완료할 수 있다. 시작 1–17초, 지속 0.5–4초, 종료 ≤17.5초의
단일 연속 구간을 지원한다. 작성 구간과 실제 보존 결과의 입력·응답 이벤트 시각을 구분한다.

Engineer 브라우저에서 기존 3–7초/400ms 조건을 2–5초로 바꿔 r3 저장·복원 후
`job_1d103b20361b4891aaa08d6dfa60bfe7`의 새 CARLA A/B를 완료했다. 양쪽 각각 제어 360개·
RGB 360장·누락 0, B의 고장 입력 60스텝과 응답 시작 2.05초·종료 5.05초·회복 5.15초를
확인했다. 이전 실행 7건의 manifest hash를 보존했고 자원 idle·잔여 객체 0이다.
8075는 같은 DB/자격으로 갱신했으며 HIL 2100의 GPU 배정은 유지했다.
backend/SDK/native 계약 검사 194개, 프런트엔드 35개와 production build를 통과했다.
정상 브라우저 편집·실행·영상·관찰·북마크 복원 및 SDK export를 확인했다. 3.55초가 소수점
오차로 직전 표본에 연결되던 문제도 수정했으며 최초 메모와 정정 기록을 함께 보존한다.
④ 장면 확대는 위 별도 기록으로 이어지며 반복 고장 패턴·실제 ECU·하드 실시간은 남아 있다.
[범위](hil-sim-beta.md#고장-시간-편집--2026-10-11) ·
[근거](validation/hil-fault-timing-20261011.json).

## HIL 엔진·GPU 복구 및 베타 새 native A/B · 2026-10-11

8075의 `시험 관리`에서 400ms 시험 r2 저장→실제 준비 확인→새 native A/B 제출→이력→
영상 재생→4.55초 관찰 저장·URL 복원을 완료했다. 실행은
`job_c3d63693eaa34413b73962569c9ec475`이며 양쪽 각각 제어360개·RGB360장·누락0이다.
4.55초 A는2.158m/s·입력 age0ms·제동17%, B는약0m/s·400ms·제동100%였고
B는7.15초에 tracking으로 회복했다. 원본 receipt/hash를 유지한 Software DUT 관찰이다.

2100은 이전 GPU 반환 과정에서 의도적으로 종료됐고, 베타 서버는 worker 없는 offline 모드였다.
GPU 조회 오류는 로드된 kernel595.84와 시스템 NVML595.99.02 불일치였다. 기존 공식595.84
사용자 라이브러리를 GPU 조회·CARLA 프로세스에만 적용해 정상화했다. 시스템 전역 driver는
변경하지 않아 기본 `nvidia-smi`의 불일치는 남는다. 커널 변경 시 이 환경도 다시 맞춰야 한다.

유휴·객체0인 발표용2000의 복원 명령과 프로세스 식별자를 보존하고, 공유8GiB GPU를
HIL2100에 할당했다. **현재2100은 실행 중이고2000·2160은 중지 상태**다. 다른 API와 기존
자료는 유지했으며8075만 동일 DB/자격으로 재시작했다. 명시적 native 옵션은 HIL 작업만
정상 claim/lease/heartbeat/publication 경로로 실행한다. 실행 후 자원idle·잔여객체0을 확인했다.
UI는 연결 설정과 실제 preview 확인을 구분하고 편집값을 보존하는 설정 새로고침을 제공한다.

backend/SDK/adapter90개, runtime4개(작성 fixture), frontend22개 및 production build 통과.
정상 Engineer 브라우저의 새 native 흐름과 console error0, SDK의 r2/새 실행/관찰 export를 확인했다.
실제 참가자 업무 인수·공개 접속·시스템 전역 드라이버 정합·GPU 배정 자동화는 남아 있다.
[운영·복원 절차](hil-sim-beta.md#native-복구와-현재-gpu-배정) ·
[새 실행 근거](validation/hil-sim-native-recovery-20261011.json).

## HIL · 사용자 시험 관리 베타 첫 기능 묶음 · 2026-10-11

HIL의 `시험 관리`에서 이름·목적·고장값 저장, 개정/복제/복원, 정확한 조건의 실행 연결과
이력, A/B 응답 시점의 관찰·문제 기록, JSON 다운로드를 일반 UI/API/Python SDK로 제공한다.
저장에는 native 연결이 필요 없고 실제 제출 전 preview에서 준비 상태를 확인한다.
기존 Experiment 큐를 사용하며 접수 후 연결 응답이 유실되면 같은 실행 ID로 복구한다.
관찰의 실행 receipt→trace hash→응답 표본 연결을 확인하고 사용자 관찰로 표시한다.

backend/SDK79개, frontend·시점 계약20개와 production build를 통과했다.
정상 Engineer 브라우저에서 300ms/r1→400ms/r2→r1 복원, 다른 조건의 연결 거절,
실제 보존 A/B 연결, 4.55초 문제 기록, 복제와 새로고침, JSON 파일 다운로드를 확인했다.
시점 URL 키 불일치, 다른 시험의 메모 표시, preview 변경 시 복구 정체를 수정했다.
최종 고정 빌드에서 영상·관찰·4.55초 복원과 console error0을 확인했다.
API에서는 Viewer 읽기/쓰기 거절과 offline preview422도 확인했다.

새 로컬 환경은8075이며 기존 HIL 발표 서비스8036과 다른 서비스는 변경하지 않았다.
보존 완료4건·실패2건의 published artifact92개를 별도 DB/독립 APFS clone에 hash 대조했다.
**이번 새 native 실행은0건**이며 transport와 worker를 비활성 상태로 명시한다.
실행 의미를 바꾸지 않은 시험 관리 기능의 인수이고 live native 베타 인수는 남아 있다.
원격 디스크 여유는 최신 읽기 관측74GB로 이전 약4GiB 기록과 다르다.
CARLA2100/2160은 중지돼 있고 GPU 상태 조회는 NVML 불일치로 확인하지 못했다.
실제 참가자 초대·업무 완료, 전용 native 시간대·HIL 실행 제한, 외부 접속 운영은 다음 단계다.
[범위·참가자 업무·다음 기능](hil-sim-beta.md) · [검사 근거](validation/hil-sim-beta-20261011.json).

## Log Sim 내부 베타 · 2026-10-11

안내형 후속에서는 추천 팩→조건 저장→실행→조사→검토 완료의 업무 기록과 이어하기,
막힌 단계·도움 필요 의견, 운영자 현황·JSON/CSV를 연결했다. 참가자용 독립 프로젝트3개와
각 팩을 별도8072에 준비하고 중복 준비 없이 복원했다. 집중 검사73개와 정상 브라우저의
대표 팩·입력 오류·후보 없음·응답 유실 복구를 확인했다. 새 CPU reference4개만 실행했으며,
실제 사람의 베타 인수·초대·공개 운영은 아직 수행하지 않았다.
[안내형 베타 근거](validation/log-sim-guided-beta-20261011.json).

지원 MCAP 업로드→채널/구간 점검→조건 개정 저장→실제 reference A/B→후보 조사→Case 검토·내보내기를
정상 UI/API/SDK에 연결했다. 영상 없는 입력은 BEV로 조사하며, 중복 시간 후보 묶기와 시점 복원,
원래 배치 후보 복귀를 제공한다. 별도 로컬 테스트 환경에 보존 CARLA 원본 5개와 작성한 오류 예제를
구분해 연결했다. 첫 512개 이내의 명시적 선택, 총2,528개 표본에서 reference10회가 완료됐고
조사 후보55개·실패0개다. 새 CARLA 실행이나 기존 서비스 재시작은 하지 않았다.
실제 사람3–5명의 업무 완료 인수·초대·외부 배포는 아직 남아 있다.
[지원 범위·참가자 업무·재현](log-sim-beta.md) · [검사 근거](validation/log-sim-beta-20261011.json).

## Object Sim · 초대형 베타8건과 빠른 시연 추가6건

네 도시 템플릿 복제 → 이름·객체 속도·출발/목적지·행동 편집 → immutable 개정 저장 →
기존 Experiment 실행/취소/비교 → 개정과 시점에 묶인 관찰 기록을 일반 UI/API/SDK에 연결했다.
별도 프로젝트3개와 참가자 비밀번호 gateway를 준비했고 정상 브라우저에서 편집·r1/r2 저장·
새로고침·재로그인 복원·프로젝트 격리를 확인했다. 실제 초대는 발송하지 않았다.
프로젝트당 실행1/대기2, 같은 DB의 CARLA 자원 배타 실행, 안정적인 사용자별 초안 키를 추가했다.
정상 API로 새 네 템플릿 A/B8건을 완료했고 native 응답/RGB 각각5,160개·누락0·cleanup confirmed를
보존했다. 정지·재출발의 실제 정지2.05/4.05초, 끼어들기 완료8.5/10.8초와 목표 차로 진입,
좌·우회전과 모든 자차 도착을 확인했다. native 접촉 기록은0건이다. 좌회전 A/B의 자차 궤적은
동일하며 교차 차량 통과 시차6.0/7.4초만 달랐으므로 자차 개선·TTC·안전 판정으로 설명하지 않는다.
행동 보고는 planner 이벤트와 native 위치·속도 측정을 구분하고 원본 hash·개정 연결을 유지한다.

빠른 시연의 장애물 우회·간격 대기·8대 교차로·도시 주행도 기존 testerA 프로젝트에 정상 API로
준비하고 **별도 native6건**을 완료해 해당 프로젝트의 새 결과를 연결했다. 기존 네 베타 binding과
권한은 유지했다. 응답5,280개·RGB2,880장·누락0·cleanup confirmed, 고정 평가 **5pass·예상 fail1건**이다.
장애물 B는 차로 변경을 끄면 출발점에 머물러 이동0m·목표 오차120m인 목표 미도달 사례다.
간격 B는 후방차 통과가0.4초 빨랐지만 자차 대기4.95초와 횡방향 이탈7.85초는 A/B가 같았다.
교차로8대는 모두 도착·정지했고 도시의 작성된2초 wait는 실제 정지2.15초로 관측했다.
첫 세 프리셋은 generated map, 도시는 native Town10이다. 준비 helper4개 검사와 정상 preview를
확인했다. 참가자 브라우저에서 새 네 장면과 기존 우회전의 원본 RGB5종, 장애물·간격·우회전
RGB A/B3종과 정확한 실행 URL의 새로고침 복원까지8개 묶음을 통과했다. console error0이며
새 실행·저장은 없었다. 별도 작성 확인6개 묶음도 통과했다. 안내형 story API의 gateway 허용 목록
누락은 기존 권한·자격을 유지해 수정하고 관련 gateway148개·backend story45개 검사를 통과했다.
정상 브라우저의 후속 안내3개 묶음도 공통 timeline·양쪽 RGB 진행, 정확한 URL 복원·차단 경고 없음·
console error0을 확인했다. 초기 차단과 후속 해결 근거를 함께 보존한다. 근거는
`.runtime/object-beta-20261010/quick-{prepared,native,measured-acceptance,result-bindings}.json`이다.
브라우저 근거는 같은 폴더의 `browser-quick/cua-{authoring,retained,story}.json`이다.

최종 정상 브라우저에서 수정 개정의 실제 native1건·기준 RGB A/B·관찰 저장과 복원,
같은 개정 재실행 취소·재로그인을 포함한20항목을 통과했고 page error는0건이다.
취소 실행은 `outcome_uncertain=false`를 유지했다. 실행 중인 world의 소유 actor6개와 live lease를
확인한 preview도 정상 동작했다. 중간 GLB404는 모델·texture·CC0 고지의 gateway 제공을 수정해
해결했으며 관련131개 검사를 통과했다. 비교 설정의 경로 JSON은 요약과 접힌 원문으로 정리했다.
최종 보존 결과 브라우저4항목에서 설정 영역650px 미만·GLB/texture200·RGB A/B·북마크와
page error0을 확인했으며 새 실행은 제출하지 않았다.
관련 backend·SDK·adapter·frontend 검사와 build는 실제 엔진 근거와 구별한다.

첫 warm-up timeout 실행과 불확실한 결과는 보존했다. 소유 actor 정리·설정 복원 확인 후 정상
운영자 API로 reconcile했고, 사용자 승인으로 발표용2000을 일시 중지했다. loaded kernel595.84와
userspace595.99.02 불일치로 새 Vulkan이 llvmpipe만 인식하던 문제는 공식595.84의 사용자 폴더
추출·프로세스별 library/ICD 지정으로 우회했다. RTX2070SUPER의 Vulkan/NVML을 확인한 뒤
2160에서 native 인수를 수행했으며 시스템 설치·재부팅은 하지 않았다.
첫 베타 인수 후13:11:29UTC 복원에 이어, 추가6건을 위한 실행 시간대를 마치고13:38:55UTC에
**2160 종료·발표용2000 복원과 베타 API 작성/재생 모드 전환을 다시 확인**했다.
2000은 Town10HD_Opt·비동기·동적 객체0이며 GPU 사용량은4,982/8,192MiB였다.
현재 베타의 새 native 실행은 의도적으로 비활성화했다. 남은 저장 공간은 약4GiB이며 자료를
삭제하지 않았다. 다음 native 묶음의 저장 용량, 전용 실행 시간대 또는 별도 GPU,
외부 HTTPS 접속과 실제 사용자80% 업무 완료 인수는 후속이다. 최종 gateway 재시작 후
로그인 페이지는 정상이며 이전 세션은 다시 로그인해야 한다.
[범위·사용법](object-sim-beta.md) · [베타8건 근거](validation/object-sim-beta-20261010.json) ·
[추가 시연6건 근거](validation/object-sim-quick-20261010.json).

## 외부 발표 자료 · 오프라인 백업 녹화 · 2026-10-10

CARLA 기반 10개 대표 사례를 편집 가능한 발표자료 14장(10분 본편12장·부록2장),
고정 레이아웃 PDF, 3분36초 연속 MP4와 사례별10개 MP4로 묶었다. 무음 발표용이며
대본·Q&A·출처 안내와 로그인/인터넷 없이 여는 `index.html`을 포함한다.
전달 파일은 `outputs/axiom-demo-20261010/AXIOM-external-demo.zip`이다.

실제 제품 화면을 녹화했고 차로 진입 사례는 보존된 전방 카메라로 다시 촬영했다.
14장 렌더·11개 영상 디코딩/화면·오프라인 브라우저 재생/키보드/모바일/링크를 확인했다.
외부28파일의 문서·메타데이터·화면 OCR에서 내부 경로/실행 ID/인증 문자열 패턴이 없었고,
ZIP CRC와 해제본 hash가 일치했다. PDF는 슬라이드 렌더 기반이며 수정은 PPTX에서 한다.
PowerPoint 앱 직접 열기, 10분 라이브 실행 리허설, 외부 발송·공개는 수행하지 않았다.
새 native 실행이나 실차·센서·ECU 검증 범위를 추가한 작업은 아니다.
[산출물과 확인 근거](validation/external-demo-materials-20261010.json).

## 질문 중심 사례 데모 · 10개 실제 결과 연결 · 2026-10-10

시작 화면에서 질문 → 조건 차이 → 고정 실행의 관찰 기록 → 핵심 시점 A/B → 다음 조사로
이어지는 사례 목록을 추가했다. 정상 API `GET /api/v1/demo-cases`와 SDK `demo_cases()`가
같은 프로젝트 경계·입력/manifest hash·원본 존재 검사를 사용한다. 준비된 사례를 먼저 표시하고,
미연결·무효 근거를 숨기지 않으며 결과에서 돌아오면 해당 카드를 복원한다.

현재 Object/Log 3개, Sensor 3개, Software-DUT HIL 3개, CARLA Neural 1개를 실제 보존 결과에
연결했다. 10개 사례는 12개 고유 실행을 재사용하며 독립 도로 10개를 뜻하지 않는다.
뒤차 간격 사례는 속도와 간격이 함께 바뀌었고,
도착 시각은 둘 다 32.65초여서 단일 변수 개선 효과로 설명하지 않는다. nuScenes는 제외했다.

남아 있던 HIL 200ms를 정상 API의 새 native A/B 1작업으로 완료했다. 각 조건에 제어 응답
360개·RGB360장, 누락0·cleanup confirmed를 보존했다. 최대 입력 age200ms에서 A/B 모두
`tracking`을 유지했고 기존 400ms의 `degraded`와 달랐다. 단, 응답 시각5.00초 속도는
A 0.86/B 1.60m/s이므로 상태 이름이 같아도 차량 반응이 같다는 뜻은 아니다.

실제 저장 결과를 사용하는 일반 브라우저에서 10개 모두 로그인·핵심 시점/pose 탐색·재생 또는
시점 변경·새로고침·목록 복귀를 확인했다. HIL3개 최종 재확인에서 200ms 표시·정확한 실행
연결·5.00초 탐색도 통과했다. Sensor 20초 주행 A/B의 이전 미확인 항목도 닫았다.
기존 Backend/SDK/검토 서버32개, frontend 관련22개와 별도 UI fixture10개 검사·production build에
이어 200/400ms 연결·오연결 거부·시간 링크의 집중 검사2개를 통과했다.

사용자 승인으로 CARLA2000→2100을 잠시 전환해 실행한 뒤 2000을480×270·Town10HD_Opt·
비동기·차량/센서/보행자0 상태로 복원했고2100은 중지했다. 기존 API 서비스와 분리 DB는 유지했다.
8053/8054/8056/8060의 별도 loopback 검토 서버는 기존 API로 동작을 전달하고 새 사례 조회만
읽기 전용 DB 연결로 처리한다. 10분 연속 발표·라이브 생성 리허설과 외부 공개 배포는 후속이다.
[사용·발표 순서](demo-case-library.md) · [근거](validation/demo-case-library-20261010.json).

## 목표 변경 · 외부 데모 기능 구현 우선

사용자의 현재 목표를 **빠른 기능 연결과 임팩트 있는 외부 화면 공유 데모**로 플랫폼 전체에
적용했다. [공통 데모 목표](internal-platform.md)는 기존 Neural·Sensor·HIL·Object·Log의
대표 결과를 재사용하는 8–10분 시연 구성과 최소 확인 기준을 정한다. 상세 상관성·전체 회귀·
실제 장비·상용 출시는 후속으로 유지한다. 이전 계획의 Sensor 주행 브라우저 확인은 위 사례 데모에서 완료했으며, nuScenes 실도로의 현재 외부 공개 권한 부족은 기본 CARLA 시연으로 우회한다.
이번 변경은 지침·계획 반영이다. 새 기능·native 실행·브라우저 리허설 완료를 추가한 것은 아니다.

## Neural Sim · 실도로 새 시점 → 실제 모델 출력 비교 → Case 완료

nuScenes mini `scene-0655`의 CAM_FRONT 원본96프레임·8초를 정상 API로 가져와
Splatfacto10,000단계 학습을271.755초에 완료했다. 일반 UI에서 원본frame48·4.050016초를
고정한 ±0.5 m 카메라 위치61개를 실제 렌더했고(native14.767초), 같은 YOLOX-S ONNX 모델로
5개 시점의 원본·동일 pose 재구성·새 시점15입력을 실제 추론했다. 검출 개수는 원본/재구성
각각12개, 새 시점은11·10·12·10·9개다. 중앙 동일 pose의 PNG·입력 tensor·검출 결과는 동일했다.

frame60·+0.5 m 결과를 Case `case_9e1fd4350bf747ab87cc335ebb70399e` r1로 저장하고,
Case 열기→정확한 렌더/모델 비교 역링크→리로드 복원→전체 화면 재생을 확인했다.
모델을 실행하지 않은 중간 시점에서는 이전 박스를 숨기고 Case 저장을 비활성화한다.
Backend·SDK72개, adapter47개, frontend14개 집중 검사와 production build를 각각 통과했다.

이 결과는 **모델 출력 민감도 조사**다. 정답 기반 정확도·검출 누락·안전 판정은 하지 않는다.
전경 차선 늘어짐과 움직이는 차량·수목의 흐림이 남는다. 데이터는 로컬 비상업 연구 범위이며
외부 공개 권한을 확보한 상태가 아니다. 기존 CARLA 프로젝트와 기본값은 유지했다.
[사용 흐름](neural-sim-demo.md) · [실제 실행·브라우저·Case 근거](validation/neural-sim-real-road-20261010.json).

## Neural Sim · 시간 고정 카메라 이동 데모

원본/재구성의 작은 차이 위주였던 발표를 **5.00초 원본을 고정한 채 좌우 3 m를 이동하는
큰 새 시점 영상**으로 확장했다. 일반 UI에서 `sweep` 프리셋 선택→생성→완료 결과 열기→
대표 영상 저장을 실행했다. 기존 checkpoint로 61개 실제 Splatfacto 시점을 생성했으며
왕복 재생·카메라 위치 탐색·현재 pose 표시·고정 원본 작은 화면·비교·발표 모드를 제공한다.
새 결과 `job_fb3aabe95d254b6db65603ac4de50ca9`는 native 19.385초에 완료됐고,
모든 원본 PNG hash는 동일하며 새 시점 PNG hash는 61개다. 재학습이나 CARLA 재실행은 하지 않았다.

카메라 경로 재생 시간과 원본 시간을 `/2` 결과에서 분리하고, 기존 `/1` 영상은 유지한다.
backend·SDK·adapter 88개 및 frontend 9개 집중 검사와 production build를 통과했다.
대표 설정은 frame50·반경1.5 m다. 새로 드러나는 도로 가장자리의 splat 깨짐은 남아 있으며,
2 m 이동 또는 다른 원본 구간의 품질을 동일하게 보장하지 않는다. 실도로·동적 객체·물리
센서 검증 범위는 확장하지 않았다. [시연 흐름](neural-sim-demo.md) ·
[실행·브라우저 근거](validation/neural-sim-sweep-20261010.json).

## Neural Sim · 새 시점 발표 데모 구현 · 2026-10-10

20초 CARLA 주행 rig A의 전방 RGB를 학습하고 좌우/높이를 바꾼 새 시점을 비교하는
첫 데모를 구현했다. 일반 UI·Experiment의 `neural_reconstruction`/`neural_render`,
프로젝트별 세 프리셋·원본/checkpoint/대표 결과 binding, SDK·Agent API를 연결했다.
실제 Splatfacto10,000단계 학습1회, 20초 새 시점3회, 정상 브라우저의 편집 프레임1회를
완료했다. 원본/재구성/새 시점 비교·동기 재생·슬라이더·1080p전체 화면과 생성 중 기존
영상 유지·새 결과 전환을 확인했다. Backend·SDK·adapter58개와 frontend6개 집중 검사,
production build를 통과했다. 로컬8040 화면 공유용으로 준비했으며 공개 서버 배포는 후속이다.
CARLA2000은 복구했고,2100은 Neural GPU 메모리 확보를 위해 중지 상태다.
화면 표기는 **CARLA 합성 로그의 Neural 재구성**이며 동적 객체 편집·실도로·학습형
센서 폐루프·물리 상관성 검증은 후속이다. [범위·API·시연 순서](neural-sim-demo.md) ·
[실행·브라우저 근거](validation/neural-sim-demo-20261010.json).

## HIL Workbench · Software DUT 고장 A/B 데모 · 2026-10-10

일반 UI/API/SDK에서 객체 입력 지연·단절 프리셋을 편집하고, 별도 프로세스 DUT의 명령으로
실제 CARLA 차량을 움직이는 정상/고장 A/B를 실행할 수 있다. Town10HD_Opt·Prius2대·18초,
제어20Hz·전방/추적 RGB640×360/10Hz를 고정했다. 영상·입력 age·제동·속도·전달된 앞차 간격,
고장/회복 이벤트, 자동 가이드·배속·전체 화면을 연결했다. 새 실행 중 대표 결과를 유지하고
완료 결과를 명시적으로 열며, URL·새로고침으로 선택과 작업을 복원한다.

기본300ms 지연·1초 단절의2작업과 정상 브라우저에서 편집·제출한400ms 지연1작업이 완료됐다.
실제 native6회·제어 응답2,160개·RGB2,160장·누락0·cleanup confirmed다.300ms 지연 결과의
3.30초 응답에서 정상 A는 제동0%, 고장 B는 입력 age300ms·제동100%이고7.15초에 B가
주행 판단으로 복귀했다. 단절은3.80초 정지 상태,4.65초 복귀를 실제 Viewer에서 확인했다.

backend/SDK42·native adapter/process79·frontend30개 집중 검사와 production build를 통과했다.
1080p 인앱 브라우저의 편집·미리보기·실행·새 결과·새로고침·동기 재생·전체 화면을 확인했다.
초기2회 실패와 원본 진단을 보존했고, 호스트 간 float 마지막 비트 비교 및 HIL 명령의 동기
수신 확인 경로를 수정한 뒤 새 실행으로 확인했다. 관련 자원 복구 근거도 별도로 남겼다.

이는 **로컬 화면 공유용 Software DUT · CARLA Simulation** 범위다. 객체 입력은 CARLA GT,
RGB는 관측 영상이고 실제 ECU·센서 주입·wall-clock 실시간·물리 안전 검증·상용 release gate는
후속이다. HIL 검증 연결은 context_only/not_evaluated로 유지한다.
[범위·실행·시연 순서](hil-sim-demo.md) · [실행·검사 근거](validation/hil-sim-demo-20261010.json).


## Workspace 연결 후 남아 있던 인증 오류 수정 · 2026-10-10

유효한 토큰으로 Workspace 연결을 저장해도 열린 Object Sim 비교 화면에 이전401이 남는
문제를 수정했다. 연결 검증 성공 시 현재 URL을 유지하고 페이지의 조회를 다시 시작한다.
기존 토큰의 유효성을 확인했고, 새 브라우저 탭에서 잘못된 토큰 거절→올바른 토큰 저장→
별도 새로고침 없이 A/B 원본 표시를 확인했다. 타입 검사·production build 통과,
인증 범위·탭별 저장 정책·native 결과는 유지한다.
[사용 안내](object-sim-demo.md#브라우저-인증과-연결-복구) · [근거](validation/workspace-auth-refresh-20261010.json).

## Sensor Sim · 동일 주행 RGB·LiDAR·Radar 확장

일반 빠른 시작에20초 도심 주행 프리셋을 추가했다. 같은 주행에서 전후방 RGB·16-channel
LiDAR·radar를10Hz로 함께 기록하고, 상대 시간 A/B 화면에 시점별 속도·이동 거리를 연결한다.
기존 세 정지 프리셋과 저장 결과는 유지한다.

공개 API 큐의 실제 CARLA A/B 한 작업이 완료됐다. 각각200시점×4센서, 총1,600관측이며
이동거리56.854688m·최고속도3.002704m/s·drop0·cleanup confirmed다. 새 native 계약은
10개 정착 프레임 후 수집하고 각 센서 원본을 같은 응답 frame·clock·자차 pose에 연결한다.
프런트엔드 parser의 실제 sealed receipt/manifest 확인과 FoV90→70° readback도 통과했다.
관련 backend/SDK44, adapter22, native mocked19, 기존 capture27(+13subtests), frontend39
검사 및 build를 확인했다.

**브라우저 최종 확인은 Mac 잠금으로 대기 중**이다. 새 화면의 실제 클릭·재생·전체 화면을
확인했다고 보고하지 않는다. 지도/자차 기반 참조 주행이며 센서 입력 학습 정책·실측 센서
물리 검증은 후속이다. [범위·사용법](sensor-sim-demo.md) · [주행 근거](validation/sensor-sim-driving-20261009.json).

## Sensor Sim · 센서 구성 A/B 발표 데모 구현

일반 UI/API/SDK에 프로젝트별 프리셋 → 한 가지 센서 설정 변경 → native preview → A/B 실행 →
전방 RGB와 LiDAR/radar 동기 비교를 연결했다. 카메라 FoV90→70°, LiDAR32,000→8,000points/s,
radar80→30m의 대표 결과를 명시적으로 고정하며 최신 실행을 임의 선택하지 않는다.
새 실행 대기 중 대표 결과를 재생하고, 완료된 새 결과를 열어 URL·새로고침으로 복원한다.
전체 화면·0.5/1/2배속·시점 이동·마지막 프레임 정지·후방/원본 상세를 제공한다.

세 프리셋의 native3작업/6캡처와 브라우저에서 직접 변경·제출한 FoV80°의1작업/2캡처가
완료됐다. 총3,200센서 관측을 보존했고 모든 캡처100시점·4센서·drop0·cleanup confirmed다.
첫 시점의 LiDAR 반환점1,645→400, radar71→7을 실제 Viewer에서 확인했다.
새 backend/API/SDK41개와 관련 기존 capture27개(13subtests), frontend28개 검사 및 build를
확인했다. Safari 정상 업무·새로고침·기존 상세 Viewer·1920×1080 전체 화면을 확인했다.

기존 정지 프리셋 범위는 Town10HD_Opt 정지 자차의 순차 CARLA 센서 A/B이며 실측 센서 물리 검증은 후속이다.
Radar 첫 표본 속도에564–686m/s 이상치가 있어 원본 경고를 유지한다. 확인한 후속
index1/49/99는0m/s였으며 범위/반환점 시연과 물리 속도 검증을 구분한다.
분리된 로컬 발표자 환경(8034)을 준비했으며 외부 공개 접속 서비스는 배포하지 않았다.
[시연 범위·사용법](sensor-sim-demo.md) · [실행·브라우저 근거](validation/sensor-sim-demo-20261009.json).

## Log Sim · 여러 실도로 로그에서 우선 조사 후보 발견

일반 UI/API/SDK에 원본 장면 라이브러리 → 일괄 사건 탐색·동일 입력 A/B → 후보 순위·검색 →
원본 RGB/객체 BEV/제어 요청 동기 조사 → 선택 Case 묶음 저장·복원을 연결했다.
실제 nuScenes10개 장면의404프레임 중381프레임을15개 연속 구간으로 보존했고,
객체128개 초과14프레임·이전 CAN 부재9프레임은 제외 이유와 함께 표시한다.
기준 속도8m/s·정지 여유2m를 같게 두고 차간 시간만1.5→3초로 변경한 실제 큐 작업30개가
모두 완료됐다. 조사 후보89개, 가감속 요청이 반대인 구간의 원본 동기 재생,
선택3개 Case 묶음 저장·새로고침·기존 Case 화면 연결을 실제 브라우저에서 확인했다.
관련 backend/SDK41개와 입력 준비·변환22개 검사, production build를 확인했다.
참조 제어기 open-loop 요청 비교이며 학습 모델·센서 반응·실차 안전 검증은 후속이다.
[시연 흐름](log-sim-demo.md) · [원본·큐·화면 근거](validation/log-sim-batch-20261009.json).

## Log Sim · 사건 검색 → 동일 로그 A/B → 반복 시험

일반 UI/API/SDK에서 규칙 기반 사건 검색 → Case 저장 → 동일 원본 전체 입력의 참조 제어기
A/B → 요청 차이 차트/원본 시점 이동 → 진단 한도 저장 → 새 작업으로 반복 실행을 연결했다.
실제 nuScenes 보존 로그 10표본(선택 Case6표본)에 두 참조 제어기를 실행했고, 10/10표본의
요청 차이와 새 작업 두 개의 출력 재현을 확인했다. 감속 요청 최대0.75→2.75m/s²,
목표 속도8→6m/s이며 차량에 적용한 결과가 아니다. 규칙 후보는 CARLA5개/nuScenes2개다.
관련 backend/adapter/SDK76개 검사, production build, 원본 브라우저 업무와 새로고침 복원을 확인했다.
학습 모델은 현재 데모에 미등록이며, 고정 GT 입력의 `geometric-headway/1` open-loop 비교다.
회귀 결과는 사용자 요청 한도의 진단 판정이며 안전 합격·검토자 승인·실차/센서 검증이 아니다.
[사용법·범위](log-sim-demo.md) · [실행·화면 근거](validation/log-sim-workflow-20261009.json).

## Log Sim · 외부 발표용 로그 → 조건 변경 → 비교

일반 Log Sim 화면에서 원본 RGB·BEV/사건 조사 → 프로젝트 범위 Case 저장 → 앞차 정지 시간
변경 → 실제 CARLA 실행 → 공통 시간 A/B 비교를 연결했다. 기존 ScenarioV2·Experiment·큐와
버전형 카탈로그를 재사용하고 UI/API/SDK의 원본·Case·변경 실행 연결을 보존한다.
2초 변경안을 정상 UI로 실행해 도달 시각 21.45→17.45초, 누적 정지 3.8→0.6초를 관측했다.
480개 응답/RGB720장·누락0·정리 확인이며, 기존 제한 기준을 통과했다.
nuScenes 실도로 원본 10프레임·4.499초와 전방 영상/원본 객체 주석, Case 저장도 연결했다.
다음 프레임은 128객체 계약을 초과하므로 원본 객체를 버리지 않고 구간을 제한했다.
관련 56개 검사·production build와 실제 브라우저 업무를 확인했다. 로컬 발표자 화면 공유
데모이며, 임의 로그 장면 복원·센서 E2E 폐루프·실차/상용 gate는 별도다.
[시연 방법](log-sim-demo.md) · [실제 실행·화면 근거](validation/log-sim-demo-20261009.json).

## Object Sim · 재생 화면 흔들림과 객체 가림 수정

공통 재생의 대기 안내가 나타날 때 하단 화면을 26px 밀던 동작을 고쳤다. 안내 영역을
고정하고 짧은 프레임 전환은 안내하지 않는다. 3D 판단 설명·조작 버튼은 도식 아래로 옮기고,
차량 이름표를 작은 ID로 줄여 객체를 볼 수 있게 했다. 상세 설명도 고정된 정보 영역 안에서 연다.
실제 저장 A/B 재생의 344개 표본과 일반 창의 정지·재출발 277개 표본에서 배치 고정,
상세 펼침 시 도식 높이 유지를 확인했다. frontend 28개·production build 통과이며
기존 원본 준비·재생 시계·native 결과를 유지한다. 새 시뮬레이션 실행은 없다.
[범위](object-sim-urban-interaction.md#재생-배치와-객체-가림-수정) · [근거](validation/object-sim-layout-20261009.json).

## Object Sim · 도심 우회전과 앞차 정지·재출발

다섯 번째 **도심 우회전 · 앞차 반응**(`urban_right_turn`)에서 저장된 native A/B를 공통
시간으로 비교하고 앞차 대기를 0–6초로 편집한 뒤 기존 실행 흐름을 사용할 수 있다.
자차 설정은 같고 B 앞차에만 대기 행동이 있다. 관측한 앞차 강조·정지와 해제 근거·도착을
원본 RGB 및 사건 시연으로 연결했다. 기존 네 프리셋의 고정 입력과 저장 결과는 유지했다.

Town10HD_Opt의 최종 native A/B는 모두 기존 제한 기준 통과·소유 actor 정리 확인이며
RGB 합계 1,440장·보고된 누락 0, 두 차량의 native 접촉 callback 기록 없음이다.
초기 영상 없는 B의 크리프를 별도 보존하고 정지 유지 로직을 개선했다. 최종 B는
6.45–8.90초의 50개 응답 표본에서 실제 속도 0.000496m/s 이하를 유지했고,
앞차 이동 관측 뒤 8.95초에 주행 재개를 요청했다. 자차 도착 판단은 A 13.85초/B 21.45초다.
API에서 0초/6초 미리보기와 저장 A/B 입력 일치, 기존 네 binding 불변을 확인했다.
관련 시험은 209개 통과·의존성 deprecation 경고 2개다.

현재 데모에는 **추적 RGB**를 권장한다. 로컬 A/B 짧은 재생에서 추적은 약 0.945×,
전방은 약 0.678×로 진행했다. 전방 20Hz 수집은 UI 20fps를 뜻하지 않으며 고해상도 A/B
재생 최적화는 남아 있다. 지도·객체 GT 기반 제어와 CARLA 원본 영상의 제한된 시연으로,
RGB 입력 인지·실차 안전·센서 물리 검증이 아니다.
[기능·결과](object-sim-urban-interaction.md) · [실행·API·브라우저 근거](validation/object-sim-urban-interaction-20261009.json).

## Object Sim · RGB 프레임 전환 깜빡임 수정

다음 RGB 원본을 확인할 때 매번 canvas를 숨기던 동작을 고쳤다. 같은 실행·센서·영상 격자에서는
직전 검증 프레임을 유지하고 새 원본 준비 후 교체한다. 표시 시각·hash·다운로드는 실제 표시 중인
프레임을 따르며, 준비 신호는 현재 요청과 일치하는 원본을 표시했을 때만 전진한다.
무결성 실패나 실행/센서/격자 변경에서는 이전 영상을 숨기고 새 원본을 기다린다.

실제 React 컴포넌트에 작성한 BGRA fixture를 연결한 브라우저17단계에서 픽셀·출처 일치,
이전 프레임 유지·늦은 응답 무시·오류 숨김·출처 전환을 확인했다. 이는 UI 회귀 시험이며 native
실행 근거가 아니다. 새 빌드가 적용된 현재8033 탭에서 기존 도시 전방 RGB를5초 수동 재생했다.
DOM838개 표본·서로 다른 표시 프레임50개에서 숨김0·표시 frame ID/라벨/caption 불일치0을
확인했다. 이 중355개 표본은 이전 검증 영상 유지 상태였다. scoped40개와 production build를
확인했다. 모든 브라우저의 무깜빡임이나 센서 FPS 증가를 뜻하지 않으며 새 CARLA 실행·표본 보간은 없다.
[수정 범위](object-sim-urban.md#rgb-전환-깜빡임-수정) · [소스·실제 표시 근거](validation/object-rgb-display-20261009.json).

## Object Sim · 네이티브 도시 배경과 원본 A/B

네 번째 **도심 주행** 프리셋(`urban_city`)과 실제 CARLA Town 결과의 카메라 중심 자동 배치를
추가했다. 일반 빠른 시작에서 속도 수정·기존 저장/실행·A/B 경로를 사용하고, 전방/추적 RGB와
분석 중심 배치를 같은 보존 시간축에서 전환한다. 도시 배경은 native RGB, 웹 3D는 좌표 도식이다.

Town10HD_Opt·자차와 반대 차로 2대의 reactive 주행·명시적 2초 대기,
전방/추적 각960×540·10Hz로 **새 native A/B 2회 완료·RGB 960장·누락0**을 보존했다.
두 실행 모두 기존 기준 통과·소유 actor 정리 확인이며 각3대의 접촉 callback 관측은 없었다.
A를 도심 프리셋에 연결하고8033 전용 데모에 적용했다. 브라우저7개 업무와 실제 API 미리보기20개
조건을 확인했다. A/B 공통 시간·전방/추적·배치 전환·원본 해시를 유지하며 오류/새 제출은 없었다.
작성한 대기를 교통 양보로 설명하던 오류는 `object-story-rules/3`에서 **설정한 대기**로 고쳤다.
재기동한 API에서3.05초 원본 시각·양쪽 manifest를 유지한16초 A/B 시연 구성을 확인했다.
Apple M4·Chrome155·1080p의 첫 재생1회는10.0023초에 원본9.80초(약0.980×), 양쪽3D59.99fps였다.
로컬 측정이며 원격 네트워크/다른 기기의 성능 보장은 아니다.
첫6초 probe `job_168e4c2647a146ad9c8a732dc1ce776a`는 지도 로딩50초 timeout으로 실패했고
불확실 상태를 보존했다. 이전 소유 프로세스 부재·포트 닫힘 확인 후 같은 전용2100 환경을 복구했다.
기존 로그는 `Killed`로 끝났으며 서버 종료가 OOM 때문인지는 확정하지 않았다. backend/adapter/script91개,
frontend20개·SDK13개와 production build를 확인했으며 전체 시험의 통과로 집계하지 않는다.
기존 세 프리셋과 아래 완료 기록은 유지한다. [사용·현재 제한](object-sim-urban.md) ·
[실행·복구·소스·인수 근거](validation/object-sim-urban-20261009.json).

## Object Sim · 데모 재생 속도 수정

자동 시연과 A/B 공통 재생에서 프레임 준비 뒤50ms를 추가하던 대기를 절대 예정 시각 기준으로 바꿨다.
양쪽 원본 준비·해시 검증과 기존0.05초 단위 관측 선택은 유지한다. 설명 정지·구간 전환·일시정지·
배속 변경·탭 복귀는 시계를 새로 잡고100ms 넘게 늦어진 작업은 누적하지 않는다.

같은 Apple M4/Chrome155·1080p·8대 결과 두 화면의10초 비교에서 원본 진행은
**수정 전7.25/7.35초 → 수정 후9.95/9.95초**였다. 렌더링은 약60fps를 유지했다.
첫 재생과 같은 페이지의 반복 재생을 각각 측정했으며 최근8장 캐시이므로 완전한 사전 로딩 시험은 아니다.
이번 수정은 저장 결과의 웹 재생에 한정하고 새 CARLA 실행·원본 변경 없이8033 데모에 적용했다.
관련 frontend12개·production build와 실제 브라우저의 자동 시연 완료·중단/재개·카메라 전환·
기존 결과/취소/비호환 수동 재생을 확인했다. 브라우저 오류와 새 실행 제출은0건이다.
[전후 측정·소스·확인 근거](validation/object-replay-cadence-20261009.json).

## Object Sim · 관측 사건 자동 시연과 시각 표현

빠른 시작의 세 프리셋에서 **시연 시작 → 실제 판단 사건 전후 자동 재생 → 설명 2초 정지 →
수동 시간/카메라 조사 → 명시적 재개**를 완료할 수 있다. A/B도 같은 시뮬레이션 시각을 공유한다.
우회·간격 대기·복귀·도착/진행 제한은 봉인된 planner 기록에서만 고르며,
차량 모델·도로 대비·상황 카메라·현재 판단/관측 간격 표시를 개선했다.
1920×1080 발표 화면에서 두 장면·설정 차이·현재 판단·공통 재생을 함께 볼 수 있다.

- 전방/외부 추적 640×360·각5Hz의 **새 native 5회 완료, RGB 2,400장, 누락0**.
  기존 기준 4회 통과, 차로 변경 금지 B 1회 목표 미달을 보존했다. 6초 카메라 구도 probe는 별도다.
- 자동 시연은 회피 A/B 4구간30초, 간격 A/B 3구간27초, 교차로 2구간16초다.
  명령0초/첫 관측0.05초 차이를 보존하고 실제 관측 프레임에서 설명한다. 로딩 시간은 별도다.
- 실제 브라우저에서 단일 시연 완료, A/B 공통 시간·전방/추적 전환·수동 중단/재개,
  8대·기존320px 결과, 취소/조건 불일치의 수동 조사 유지와 local asset 로딩을 확인했다.
- Apple M4·Chrome155·1920×1080에서 같은 8대 결과를 두 창에 표시한10초 측정은
  양쪽 약 **60fps**로30fps 목표를 넘었다. 실제 WebGL render callback 기준이다.
  당시10초 동안 시뮬레이션 시각은7.4초 진행했다. 이후 위의 재생 예정 시각 수정으로 같은 장비에서 약1×를 확인했다.
- Backend/SDK/adapter 계약79개, 관련 frontend43개, scoped gateway6개 통과와 production build 완료.
  불필요한 GLB decoder 초기화의 CSP 오류를 제거했으며 보안 정책은 유지했다. 큰 bundle 경고는 남는다.

전용 데모는 **http://127.0.0.1:8033/#object-sim?project=project_bc937f94c7c94a7884993c074213a86c** 다.
같은 버전의 Story GET API·Python SDK를 제공한다. 기존 결과/공유 서비스는 보존했고 새 외부 URL은 발급하지 않았다.
CC0 차량은 설명용 외형이며 원본 CARLA mesh가 아니다. 배경은 절차적 도로이며 물리 센서·실차 검증은 별도다.
[시연/API/재현](object-sim-story.md) · [실행·소스·브라우저·성능 근거](validation/object-sim-story-20261009.json).

## Object Sim · 발표용 빠른 편집과 A/B 데모

일반 Object Sim의 기본 화면에서 **회피·양보·8대 교차로 선택 → 객체 드래그/차로 맞춤 →
속도·간격·차로 변경 수정 → 한 번에 저장·실행 → 3D/원본 RGB 동기 A/B 비교**를 할 수 있다.
설정 차이, 기존 주행 지표와 planner 사건을 함께 보고 발표 화면으로 확대한다.
저장된 실제 기준 A와 방금 요청한 실행을 구분하며, 취소·부분 결과·조건 불일치·짧은 관측 종료를 표시한다.
기존 고급 지도·시나리오 편집도 유지한다. 같은 API를 Python SDK에서 사용한다.

전용 데모 프로젝트는 **http://127.0.0.1:8033/#object-sim?project=project_bc937f94c7c94a7884993c074213a86c** 다.
별도 workspace와 고정 production UI, 프로젝트 Engineer 발표자 토큰을 사용한다.
기존 연구8032/5175와 공유8021/8787은 유지했고 새 외부 URL은 발급하지 않았다.

- 대표 native 5회와 UI에서 속도·장애물 위치를 바꾼 1회, 총 **6회 실행 완료·원본 RGB 1,425장·누락0**.
  기존 주행 기준은 5회 통과, 차로 변경을 금지한 회피 B는 목표 미달로 보존했다.
  교차로 8대는 모두 목표 도달/정지했다.
- 실제 브라우저의 한 번 실행 제출, 드래그, 원본 비교, 공통 시간·배속·발표 화면,
  URL 재진입, 서로 다른 관측 길이, 취소1회/부분 비교, 8대 저장 결과 재생을 확인했다.
- backend/SDK25, adapter 계약62, frontend31, gateway122 확인과 production build 완료.
  계약 fixture 검사와 실제 CARLA 실행은 별도 근거다. 빌드의 큰 bundle 경고는 남는다.

배경은 절차적 도로이고 3D는 저장 좌표의 도식이다. 사실적인 도시 환경, 센서 인지 정책,
실차·물리 센서 검증과 상용 release gate는 이 데모의 완료 범위에 포함하지 않는다.
[시연·API·시작/복구](object-sim-demo.md) · [실행·소스·브라우저 근거](validation/object-sim-demo-20261009.json).

## Object Sim · 지도 편집·행동·반응형 교통 확장

일반 Object Sim에서 **지도 작성/빌드 → GUI·YAML 행동 작성 → 실제 CARLA 실행 →
다중 차량·계획/관측 경로·RGB 조사 → 기존 검증 계획 평가**를 완료할 수 있다.
`object-map/1`의 직선·원호·완화곡선·일정 폭 차로·연결·교차로·우선권·폐쇄·표지/신호를
편집하고 2D/기본 3D로 확인한다. 고정 pyoscx 컴파일러로 OpenDRIVE를 발행하며,
미지원 외부 지도는 원문과 이유를 보존한다. 지형·건물·모든 표준 요소의 범용 편집은 아니다.

`scenario-v2/3`는 19종 행동, 7종 발동 조건, 순차 행동·시간 초과 정책·명시적 위험 모드,
고정 목적지와 교체 가능한 기본 planner를 제공한다. 0–12m/s의 지도/객체 GT 입력에서
차간 거리·양보·차로 변경·우회/복귀·경로 재탐색과 진행 불가 이유를 기록한다.
Viewer는 선택 actor의 원래/현재/실제 경로, 결정·양보 관계·이벤트와 원본 RGB를 같은
시간축에서 조사하며, 검증 계획 연결 후 해당 실행을 명시적으로 평가한다.
Python SDK와 AI 도구 목록도 같은 프로젝트 API를 사용한다. 기존 `/1`, `/2` 원본은 보존한다.

실제 원격 CARLA 인수에서 발견한 실패를 수정하고 새 소스별 결과를 보존했다.

- 고정 6종×3 seed 첫 실행: **15 통과·간격 대기 3 미달**. 차로 진입 간격의 교착 수정 후
  같은 시나리오·기준으로 해당 3건이 통과했다.
- 첫 교차로 3건은 자차 도착·8대 이동 기준은 충족했지만 일부 후속 차량은 미도착했다.
  모든 차량의 목표/정지 기준으로 재평가하면 **3건 모두 미달**이다. 도로 전체를 점유로 처리하던
  오류와 동일 접선 분기 선택을 고친 뒤, 같은 지도·8대·60초·seed에서 **3건 모두 전 차량 도착/정지**했다.
  목표 오차 약 0.91–0.93m, 차량 collision callback의 접촉 관측은 없었다.
- 명시적 목적지 차로 변경은 원래 경로를 보존하며 실제 최대 속도 4.754m/s, 목표 오차 0.918m로
  완료했다. 검증 평가기가 옛 안내 경로 끝점을 사용하던 오류를 수정하고 같은 원본을 새 평가로
  판정했다. 이전 409 거절 기록은 유지했다. 브라우저의 계획 선택→평가→보고서 다운로드도 확인했다.
- 최종 planner의 8대 주행·우회·간격 대기 회귀 **3건 통과**. backend/adapter/SDK **256개**와
  frontend **180개 통과·1개 skip**, production build 성공. 단위 검사는 실제 CARLA 근거와 구분한다.

현재 사용 주소는 **http://127.0.0.1:5175/#object-sim** / API8032다. 기존 공유8021과 이전
도시 인수8031 작업공간은 유지했고 외부 HTTPS에 자동 배포하지 않았다.
[사용법·계약·제한](object-sim-reactive.md) · [소스/원본/실패/브라우저 인수](validation/object-sim-reactive-20261009.json).

19종의 모든 지도·asset 조합, 학습형 교통 분포·V2X, 실제 카메라 정책 입력, 물리 센서·실차
상관성, 전체 Case/Campaign 자동화 및 Applied 동등/상용 출시 gate는 여전히 별도 과제다.
아래 이전 도시/직선 프로필의 결과와 미해결 문제는 해당 버전의 기록이며 이 확장 결과로 대체하지 않는다.

## CARLA 도시 시나리오 · YAML · 실제 경로 추종

새 `scenario-v2/2`에서 Town01/Town10HD_Opt의 native 지도 개정과 192개 blueprint
카탈로그를 고정하고, 지도 경로 선택 → YAML 검증/불변 저장 → 일반 Experiment 큐 →
native 도시 Viewer → 검증 계획 평가를 연결했다. 기존 `/1` 직선/esmini 결과는 보존한다.
schema19는 카탈로그 개정 테이블을 추가하며 과거 시나리오·결과 manifest를 다시 쓰지 않는다.

첫 Town01 Prius 참조 제어 실행 `job_ac6fe790af5b4103ba758f80cfce5b2e`는
20초/400 physics tick, RGB 200장, 이동 29.092m를 보존했다. 고정 기준에서
경로 오차 P95 0.000891m, 목표점 오차 0.9078m, 최종 속도 약 0m/s와 native contact 0건을
확인했다. 실제 브라우저 원본 BGRA/Range 재생·시간 이동·검증 계획 등록/평가까지 인수했다.
이는 CARLA GT 기반 참조 경로 추종 결과이며 실차·물리 센서 검증이나 Autoware 인수가 아니다.

Autoware Jazzy의 별도 CPU 프로필에서 지도/객체/신호 GT → native planner/MPC/PID →
Prius 제어 → 다음 물리 상태의 폐루프 직진/목표 정지를 같은 설정으로 3회 인수했다.
각각 600 physics 응답/RGB300장/누락0을 보존하며 목표 오차는 0.4298–0.4660m,
경로 오차 P95는 0.00117–0.00157m였다. 이는 원래 프로필 `5fe3…`의 직진 인수다.
곡선용 rear-axle 속도/가속도 변환과 현재 계획 출력 검사를 수정한 새 프로필 `57de…`의
Town10 첫 시험에서는 mission planner가 경로 처리 중 종료했다. 차량 출발 전 실패로 보존하고
반복/곡선 제출을 중단했다. 원래 직진3회 결과를 새 소스의 인수로 승격하지 않는다.
원본 Town01 Lanelet2에 신호 규칙이 없어 private 파생 지도에서36개 대응을 확인했다.
첫 제어 메시지 실패와 지도 전환 중 전용 CARLA 종료도 보존했다.
[직진3회와 실패 이력](validation/autoware-city-20261009.json) ·
[새 소스의 Town10 실패·정리 근거](validation/autoware-town10-driving-20261009.json).

첫 Town01 고정 suite는21회 실행을 모두 마쳤다(기준 통과13/미달8/실행 장애0).
우회전3회는 P95약0.705m로0.5m 기준을 넘었다. 횡단2회는 보행자 횡단은 완료했으나
고정하지 않은 신호 주기와 자차 도착/정지 조건이 충돌했다. 적색3회는 처음부터 신호 영역에
정지하여 최소2m 접근 조건을 충족하지 못했다. 원래 결과·기준을 유지하고 원인별 새 개정으로
수정/재시험했다. 새 개정에서 보행자·적색 접근은 각각3회 통과했지만, 우회전3회는
rear-axle 기준점 수정 후에도 P95약0.678m로 같은0.5m 기준에 미달했다. 기준 완화나
이전 실패 삭제 없이 제어 성능 공백으로 남겼다. 최종 소스의 직진·좌회전 회귀2건은 통과했다.
실행 완료와 SUT 기준 미달은 Viewer와 검증 계획에서 따로 표시한다.

실제 actor 생성 후 취소에서 정리·자원 해제를 확인했다. 별도 timed replay는
60개 native 상태와96×64 RGB15장을 보존하고, 실제 브라우저의 위치/원본 픽셀 변화를
확인했다. 물리 경로 추종 근거가 아니므로 `not_evaluated`를 유지한다.
Town01의 곡선·cut-in 두 슬롯은 이번 bounded 경로 생성 범위의 미지원으로 남는다.
상용 동등성·학습형 인지/E2E·실측 상관성·전체 9종/반복 통과 gate는 완료로 계산하지 않는다.

Town10의 9종은 최종 주행 완료27회 중 **24 통과·3 SUT 기준 미달**이다.
직진·곡선·좌/우회전·선행차 정지·장애물 대응·적색 접근·보행자 횡단은 각각3회 통과했다.
끼어들기3회는 상대 차량이 실제 차로 변경·목표 정지를 완료했으나 자차가 그 뒤에 정지해
목표 오차 약7.14m로2m 기준에 미달했다. 브라우저에서 두 차량·실제 횡변위 약3.51m·
원본 RGB·시간축·실패 표시를 확인했다. 우회/추월까지 해결하는 일반 주행 planner는 아니다.

위27회와 별도로 원래 보행자 생성 실패1회를 보존한다. 첫 실패에서 남은 소유 actor는
정확한 ID와 소유 근거로 제거하고 독립 snapshot의 부재 확인 후 자원을 reconcile했다.
도로 높이 ray만으로 생성 실패 원인은 확정하지 못했다. 새 보행자 개정은 시작 Z만
0→1m로 명시적으로 바꾸고 경로·속도·seed·기준을 유지했다. 고정 setup20 tick 뒤
보행자 원점 Z약0.952m·정지 상태와 실제 횡단을 보존했다. 이 새 개정의3회 통과를
원래 실패의 성공 재분류로 계산하지 않는다. [단계별 실행·원본 해시·브라우저 근거](validation/carla-city-20261009.json).

최종 UI는 키 순서만 바뀐 YAML/JSON을 실제 변경으로 오인하지 않으며, 저장된 카메라의
ID·해상도·주기·mount를 표시하고 명시적인 추가/제거에서 기존 설정을 보존한다.
API capabilities는 schema1/2의 범위와 실제 어댑터 실행 전 제약을 분리한다.
신호 변경 경계 시각의 평가 허용오차를 native 실행과 같은1ns로 맞췄고 새 관측에는
별도 일정 평가 버전 표식을 보존한다. 이전 고정 신호 결과는 다시 쓰지 않는다.

[계약·API·SDK·지원 범위](scenario-v2-city.md). 위 신규 업무의 인수 환경은
`http://127.0.0.1:5174/#object-sim` / API8031이며 기존 공유8021 환경은 별도로 유지한다.
이번 도시/YAML 확장은 기존 외부 HTTPS 환경에 자동 배포하지 않았다.

## Sensor Sim · 지도/카메라·LiDAR·radar 조사

정상 Sensor Sim에서 큰 전/후방 영상과 native 지도·계획·실제 자차를 공통 시간축의
2D/3D·추적·표본 이동·속도·전체 화면으로 조사한다. 새 1.1.0 주행 실행은
원본 XODR·차로 구간78개/표본1,004개·native bbox와40개 응답/RGB80장을 보존했다.
기존 정지 A/B는 RGB·LiDAR·radar·raw tuple·등록 좌표·원본 다운로드를 연결한다.
첫 radar 이상 속도와 순차 A/B의 다른 원래 clock을 숨기지 않는다.
실제 지도에서 발견한 unwrapped yaw 허용 범위 불일치는 원본을 유지하고 UI/native 계약을 맞춰 수정했다.
과거 지도/bbox 미보존 결과는 궤적·위치 기호만 표시한다. 차로 ribbon·frustum은 native 표본의
계산 표시이며 renderer mesh·실측 센서·학습형 카메라 정책·안전 판정은 아니다.
새 native 한 건과 local/외부 HTTPS 각각 정상 업무4개·SDK 원본 다운로드를 인수했다.
기존 결과·권한·주소·비밀번호·고정 만료를 보존했다.
실행/원본/브라우저 인수와 남은 범위는 [Sensor workbench](sensor-scene-workbench.md)에 기록한다.

## Object Sim · 지도 기반 다중 actor 조사

정상 Object Sim에서 실제 지도 길이·차선 폭과 1–8 actor·행동·시간 지정 경로를 작성하고,
개정 저장→명시적 native 제출→2D/3D 전체 궤적·actor 선택/추적·표본 이동·이벤트 조사를 완료한다.
새 `object-scene/1`은 원래 지도·전체 trace·native bbox offset·출처 해시와 관측 이벤트를 보존한다.
실제 5 actor CPU 시험에서 102표본, 차로 변경·lead 정지·보행자 횡단을 확인했다.
다른 actor 접촉 2구간도 보존했다. ego 기준 `passed`를 전체 무충돌/안전 판정으로 확대하지 않는다.
과거 job725의 62표본·봉인 결과, 외부 접속·권한·만료는 유지한다.
지원 지도는 직선 2차로이며 절차적 3D·authored object kinematics다. 반응형 traffic·
외부 곡선/교차로 지도·사진식 sensor scene·실측 검증·Applied 전체 동등 수준은 미완료다.
[사용 업무·계약·경계](object-scene-workbench.md).

## CARLA planning / feedback 주행 · 연속 센서 영상

정상 Sensor Sim에서 **Feedback 주행 · 카메라 실행**으로 지도 기준 계획·제어·카메라 구성을
작성하고 preview → 불변 저장 → 큐 제출 → 같은 시간축의 원본 RGB·계획/실제 궤적·속도·
요청/관측 제어값 재생과 다운로드를 완료한다. 실제 CARLA 4초 실행은 9.65m 이동,
40개 제어 응답·640×360 전/후방 RGB 80개를 보존했다. 기준 제어기는 실제 자차 상태를
되먹임하며 카메라는 새로 렌더링한 관측 출력이다. 학습형 카메라 E2E·RAW/ISP·안전 판정은 아니다.

첫 실제 실행은 삭제 ACK 직후의 이전 actor snapshot 때문에 정리 확인이 실패했다.
그 실패/불확실성·봉인 영수증은 유지하고, 정확한 소유 reservation·새 snapshot의 actor 부재·
원래 설정·dispatcher 종료를 확인한 operator reconciliation을 별도 기록했다.
`carla-feedback-sensor/1.0.1`은 별도 정리 tick의 새 snapshot에서 소유 actor가 사라진 것까지
확인한다. 두 번째 실제 실행과 정상 local/HTTPS 업무를 인수했으며 주행 품질 평가는 `not_evaluated`다.
기존 외부 URL·로그인·Engineer 권한·2026-10-10 03:45:21 KST 만료는 유지한다.
원격 공유 lock은 legacy worker와 새 feedback 실행에 적용한다. 기존 8020 정지 capture와
임의 CARLA client의 최초 동시 변경을 모두 예방하는 전역 조정은 남은 운영 gate다.
[지원 업무·운영 경계](carla-feedback-sensor-viewer.md) · [인수 근거](validation/carla-feedback-sensor-viewer-20261008.json).

## 기존 CARLA 도로영상 · 공유 작업공간 연결

사용자 요청으로 기존 CARLA 실행 3건의 전방/후방 **640×360, 총 1,200 RGB 프레임**을
공유 `default`에 원래 입력·ID·봉인 hash로 연결했다. 시작 화면에서 **CARLA 도로영상 재생**을
열고 재생·일시 정지·시간축 이동·센서 A/B 나란히 표시·현재 원본 프레임 다운로드를 완료한다.
원래 ZIP의 bounded Range와 프레임 hash를 확인하므로 첫/마지막 PNG만 반복하지 않는다.
현재 원본은 **정지 차량의 CARLA 후처리 RGB**이며 실제 주행·물리 센서 검증으로 판정하지 않는다.
기존 외부 URL·로그인 cookie·비밀번호·권한·만료를 유지했고 신규 GPU/job은 제출하지 않았다.
정상 local/HTTPS 업무와 원본 픽셀·모바일을 인수했다.
[지원 업무와 경계](carla-road-viewer.md) · [근거](validation/carla-road-viewer-20261008.json).

## 작업 진입점과 Object / Sensor Viewer

**시작 · 가능한 작업**, **Object Sim · 작성 / 재생**, **Sensor Sim · 영상 / RAW**, **결과 Viewer**를
프로젝트 메뉴에 연결했다. 저장 결과의 영상·궤적을 먼저 표시하고 일반 실험 입력 폼과 원본 JSON을
상세 설정으로 분리한다. 정상 UI에서 시나리오 r2 저장→실제 esmini 1건→62개 표본 재생·원본 다운로드를
확인했다. 실행 완료와 충돌 기준 실패를 별도 표시한다. 센서 쪽은 기존 계산·실패 기록을 조회했으며
새 GPU/native 센서 작업을 제출하지 않았다. HTTPS와 네 화면 너비에서 확인했다.

CameraE2E 센서 영상은 32×32 중앙 진단용이고 매우 어두운 원본을 유지한다.
이후 위의 명시적 CARLA 연결을 추가했으며 물리 센서·실제 이동 주행 평가 확장은 남아 있다.
[사용 업무와 실제 범위](viewer-workspace.md) · [검증 근거](validation/viewer-workspace-20261008.json).

## 외부 프로젝트 접속 · 수정과 실행

2026-10-08 사용자의 2일 운영 요청으로 종료 시각을 **2026-10-10 03:45:21 KST**로 설정했다.
기존 foreground 접속 프로세스의 종료를 확인하고 독립 supervisor·터널·기간 제한 잠자기
방지로 복구했다. 개별 로그인 세션은 12시간이며 기존 비밀번호로 다시 로그인할 수 있다.
[기간 제한 운영 근거](external-access-48h-20261008.md).

사용자가 외부 수정·실행을 허용했다. 별도 임시 HTTPS 비밀번호 세션에서 공유 `default`
프로젝트의 Engineer 권한으로 시나리오·실험·검증 계획을 편집하고 지원 시험을 제출한다.
정상 외부 화면에서 시나리오 개정 저장과 합성 CPU reference planning 시험 1건의 완료·
봉인 영수증 다운로드를 확인했다. 관리자·다른 프로젝트·Reviewer 판정·legacy API는
공개하지 않는다. 작성용 native 렌즈 목록 서비스도 원래 state·결과를 보존하며 복구했다.
상시 배포나 실제 모델·센서·안전성 검증의 완료를 의미하지 않는다.
[접속 범위](external-access.md) · [실제 업무 인수](external-access-acceptance-20261007.md).

## 현재 기능 단위 · 기록 입력 점검과 프로젝트 원본 연결

정상 **Replay specifications**에서 승인된 원본을 검색·선택하고 exact ego/object 채널과 원래
ordinal을 조사한 뒤 입력 점검→같은 preview hash의 freeze→ReplaySpec→Experiment로 진행한다.
채널·표본·offset·설정을 바꾸면 이전 점검은 무효다. 지원 v1 planning 입력의 capture timestamp는
원래 MCAP log timestamp와 정확히 같아야 하며, clock offset은 이후 정렬에만 적용한다.
미래·stale·누락 객체를 빈 공간으로 채우지 않고 drop·미평가로 유지한다. Original/open-loop 구간 조사와
완결된 causal 객체를 요구하는 planning closed loop의 준비 상태를 구분하며, 없는 크기·좌표·clock 계약은 추정하지 않는다.

새 project recording grant는 운영자가 원본 SHA-256을 지정해 부여한다. Viewer도 허용된 catalog·
channel/sample과 인증된 원본 MCAP 다운로드를 사용할 수 있지만 다른 프로젝트나 알려진 ungranted ID는
조회할 수 없다. default Team token도 grant가 필요하다. 기존 전역 `/recordings` API는 operator만 사용하며
기존 데이터 전체의 소유권을 이관하지 않는다. 원본 provenance·nuScenes 비상업 연구 제한·annotation-derived
GT와 선언한 ego geometry를 snapshot에 보존하고, grant로 상용 권한을 추가하지 않는다.
[업무·API·SDK·제한](recorded-input-onboarding.md).

현재 코드의 **schema18**은 grant와 재시도 intent 테이블을 추가한다. 원본 schema17의 냉복사본을
이관하는 계약 검사에서 과거 행·FK·원본 bytes를 유지했다. 신규 grant/SDK 검사 16개와 authored MCAP의
브라우저 원본 다운로드·SHA 확인→점검→freeze→reference planning queue 흐름을 확인했다.
이 검사는 실제 도로 품질·센서 보정·안전 검증이 아니다.

실제 nuScenes mini 원본 3프레임을 같은 origin8021의 정상 source picker·인증된 원본 다운로드·clock/pose
점검·freeze·ReplaySpec·공통 큐·궤적/봉인 receipt UI로 연결하는 브라우저 인수를 완료했다.
객체 78/86/94개와 과거 관측만 사용한 파생 속도 67/77/85개·누락 속도 11/9/9개를 보존했다.
실행 `job_e202b09f98b248d5beda5096404ba180`의 receipt SHA는 원본 근거에 연결했다.
같은 실행의 저장된 TTC 계획·평가를 정상 Validation 화면에서 read-only로 확인하고 평가 receipt의 SHA도 검증했다.
TTC는 **evaluated0/denominator3**, 이유는 `object_velocity_missing`이며 계획 verdict는 `not_evaluated`,
실행 분모1의 counts는 `unevaluated1 / passed0 / sut_failure0`다. 실행 완료를 성능 통과나 실패로 승격하지 않는다.
이 읽기 검사는 기존 operator profile을 사용했고 새 job·plan·token을 만들지 않았다.
[실제 원본·정상 UI 근거](validation/nuscenes-planning-product-20261007.json) ·
[TTC 계획 UI·다운로드 근거](../.runtime/road-log-onboarding-20261007/typed-plan-road-readonly-browser-acceptance.json).

객체 입력은 원본 annotation GT이고 ego footprint는 선언한 참조 geometry다. 실제 detection·실측 차량 크기·
reactive 주변 객체·카메라 폐루프·안전 통과 또는 상용 권한으로 확대하지 않는다.
전체 입력·신규 CameraScene 범위는 [입력·렌즈 확장 인수 기록](input-and-lens-expansion-acceptance-20261007.md)에 연결한다.

CameraE2E에서는 default Project의 정상 Experiments에서 렌즈 두 개×저장 센서 두 개의 같은 작성 입력을
고정해 API seed42와 정상 화면 seed43의 제품 실험 두 건을 완료했다. 각 RAW4·고정 ISP quality1,
합계 RAW8·quality2이며 API 인수에서 원본/파생 artifact28개 크기·SHA와 봉인 receipt를 확인했다.
정상 화면의 인증·카탈로그/DTO 작성·네 계획 preview·불변 저장/실행·영상/좌표/픽셀 조사·원본/receipt
다운로드의 여섯 업무를 인수했다. 초기 parent RAW closure 실패와 후속 derivative 다운로드404 실패는
원래 receipt·native 결과로 유지하며, 완료한 후속 실험으로 덮어쓰지 않는다.

동일 네 실행을 연결한 기존 센서 검증 계획을 정상 Validation 화면에서 read-only로 확인했다.
요청 분모4·`infrastructure_failure2 / context_only2 / passed0 / sut_failure0`, verdict는 `not_evaluated`다.
평가 receipt SHA를 검증하고 두 원래 실패의 `outcome_uncertain`도 그대로 확인했다.
[렌즈·센서 업무·근거](camera-lens-sensor-experiments.md) ·
[계획 UI·다운로드 근거](../.runtime/road-log-onboarding-20261007/typed-plan-lens-readonly-browser-acceptance.json).

이 계산은 중앙32×32 geometric lens/estimated sensor 모델 진단이다. 물리 PSF·MTF·실측 sensor 정확도·
도로 장면/라벨 대응·perception AP·제품 순위를 인수한 결과가 아니다. 원래 L2S2 고정 ISP 보기가 매우
어두운 상태도 자동 gain으로 바꾸지 않고 원본 float RGB·RAW와 함께 조사한다. 두 seed를 독립 장면 다양성으로 세지 않는다.
카탈로그는 명시적 default Project 범위이며 다른 Project의 연결은 지원하지 않는다.

이번 새 격리 작업공간은 `.runtime/road-log-onboarding-20261007/platform`, 동일 origin 주소는
**http://127.0.0.1:8021**이다. 새 launcher는 실행별 private UI snapshot·파일 manifest를 보존하므로
뒤의 build가 실행 중 UI를 바꾸지 않는다. 기존 **8020/schema17** 환경과 아래 인수 기록은 그대로 유지한다.
운영 DB·외부 공유 서비스는 자동 이관/재시작/배포하지 않는다. [실행 경계](experiment-contract.md).

**남은 gate:** 승인된 실제 입력의 반복 업무수용, lens/sensor 픽셀 정합과 실측 상관성, 두 실제 정책 A/B,
반응형 카메라 폐루프, 의미 검색/사건 구간, 전역 데이터의 프로젝트 소유권, 실제 팀 사용·장시간/부하/장애,
상용 데이터/모델/엔진 권한과 출시 인수. Applied 동등/우위 또는 상용 완료를 주장하지 않는다.

## 앞선 인수 기록 · schema17 ADP / Object / Sensor / Log 연결

일반 **Experiments**에서 Project·정확한 입력·adapter·seed를 고정하고 preview→기존 영속 큐→
원본 근거·시각화→Validation의 **Experiment plans**까지 진행할 수 있다. Engineer 작성/실행,
Reviewer 판정, Viewer 조회·다운로드를 분리했고, 역할별 같은 계획 흐름과 프로젝트 차단을 브라우저에서 확인했다.
API/SDK와 agent catalog도 실제 OpenAPI 계약을 사용한다. 역할 경계는 신규 모듈 범위이며 기존 전역 리소스는
operator 영역으로 유지한다. [공통 계약·실행점](experiment-contract.md) · [확장 범위](four-module-expansion.md).

계획의 통과는 선택한 기준에만 적용한다. 통합 작업공간의 객체 실행은 `maximum_speed ≤20 m/s`를
충족했지만 같은 native 실행의 충돌 observer와 전체 판정은 실패로 유지된다. 센서·ISP·VAD 계획은
`context_only / not_evaluated`이며 실행 완료를 검증 통과로 바꾸지 않는다.

- **Object Sim:** Scenario v2의 여섯 작성 템플릿·액션/트리거·sweep preview·제한 XML 입출력과 실제 esmini.
  실제 36회 완료, 판정 27 실패·6 미평가·3 통과를 보존했다. [사용법·근거](object-sim-v2.md).
- **Sensor Sim:** 실제 CARLA RGB 두 대·LiDAR·radar의 10/30초 캡처와 순차 rig A/B. 30초의 300 tick·
  1,200 관측을 누락 없이 보존하고 자세·시각·설정 readback·전체 파일 해시·정리를 확인했다.
  CameraE2E에서는 작성한 센서 모델이 생성한 동일 RAW의 ISP 후보 둘·네 프레임·60개 native 근거를 일반 UI로 연결했다.
  [센서·ISP 범위](sensor-capture-expansion.md).
- **Log Sim:** 고정 SceneSequence/ReplaySpec, 기록 원본·시각·좌표 점검과 기준 계획/자전거 모델 피드백·
  타임라인·native trace. 기록 주변 객체는 비반응형이며 카메라가 재생성되는 폐루프는 지원하지 않는다.
  실제 VAD의 3프레임×6카메라 GPU 추론도 일반 큐·근거·세 모드 궤적 UI로 연결했다.
  [Log 범위](planning-replays.md) · [실제 모델 근거](validation/vad-product-queue-20261007.json).

새 **schema17**은 임시 schema16 복원본에서 원본 queue ID/순서/이벤트를 유지하며 인수했고,
실제 새 팀 계획/실행 데이터의 냉백업·복원에서 원본 bytes와 전체 복원 inventory를 확인했다.
전체 backend 검사에서 발견한 구 백업 schema 허용 문제를 수정하고 관련 99개를 재검증했다.
공통/SDK/ISP/기록 관련 396개와 센서/객체 32개·subtest13개, 내부 launcher4개,
역할/호환/Log/실제 VAD/ISP 브라우저 흐름과 최종 frontend 타입/build를 구분해 기록한다.
중복된 검사 수를 합산한 전체 통과 수는 주장하지 않는다.

별도 내부 개발 주소는 **http://127.0.0.1:8020**이다. `.runtime/internal-platform/data`는
실제 센서 인수 DB의 검증된 새 복원본이며, 기준 Log·ISP·VAD와 형식별 계획 근거를 추가했다.
`Workspace settings`에 `.runtime/internal-platform/access/api-token`의 로컬 토큰을 입력한다.
이것은 SSH 또는 Applied 로그인 비밀번호가 아니다. 운영 DB·기존 서버·외부 공유는 자동 이관/재시작/배포하지 않았다.

[네 모듈 통합 인수 기록](four-module-expansion-acceptance-20261007.md)에서 실제 실행,
브라우저 업무, 복원 검사와 남은 출시 조건을 확인할 수 있다.

**남은 핵심 공백:** frozen lens/sensor 계획·픽셀 정합, 실제 승인 도로 object 로그, 두 실제 정책의 A/B,
반응형 카메라 폐루프, 학습형 의미 검색/사건 구간, 기존 전역 데이터의 프로젝트 소유권, 실제 팀원의 반복 인수,
물리/실도로 상관성·장시간/부하/장애·상용 권한과 출시 gate. VAD/nuScenes는 승인된 비상업 연구만 허용된다.
작성/기준 결과·실제 엔진 실행·실측 정확도를 구분하며 Applied 동등/우위나 상용 완료로 계산하지 않는다.

## 이전 단계 · 2026-10-05

## 현재 후속 · 실제 E2E 기록 영상 추론 첫 인수

사용자가 승인한 비상업 연구 범위에서 실제 nuScenes mini·causal CAN·공식 VAD-Tiny 가중치를
격리 GPU 환경에 연결했다. 원본 테이블 채널 join 결함을 수정했고, 3개 연속 프레임의 세 mode를
두 개의 독립적인 reset 실행에서 동일하게 재현했다. 기존 Driving policies UI/API에 실제 input/result를
업로드해 시점별 궤적·해시·제한을 살펴보는 업무도 인수했다. [범위·결과](driving-policies.md) ·
[실제 근거](validation/vad-native-acceptance-20261005.json).

서버 GPU 작업 제출·영속 정책 실행 DB·정책 A/B·검증 계획 판정·CameraE2E/ISP는 미완료다.
기록 영상의 실제 모델 추론은 정확도 판정·폐루프 안전성·실측 센서 인수가 아니다.
운영 서버·외부 공유 서비스는 배포/재시작하지 않았으며 데이터·가중치 상용 권한도 확정하지 않았다.

## 최신 기능 · Verification Plan v1

Validation → Plans에서 검증 목표·고정 Dataset/모델 개정·계획별 조건·기준을 작성하고,
기존 근거 연결 → 실행 preview → 묶음 A/B → 전체/조건별 실제 평가 → 공백·악화 조사 → 개정/재실행을 제공한다.
UI/API/SDK와 기존 queue/worker를 사용한다. [인수 근거](verification-plans-acceptance-20261004.md)에서
검사 범위·독립 패키지·재실행 명령을 확인할 수 있다. 데이터·라벨·조건 공백, 미평가/비교 불가와 성능 악화를 분리하고,
완료 추론의 명시적 재사용·취소·새 평가·불변 결과 export를 보존한다. [범위와 사용법](verification-plans.md).

Schema16은 추가 방식이다. 기존 운영 서버/외부 공유/원본 작업공간은 자동 재시작·배포하지 않았다.
검증은 분리된 임시 DB와 브라우저 환경에서 수행한다. 작성 예제의 실제 ONNX/COCO 실행은 제품 연결 근거이며
실도로 정확도·학습형 주행 모델·물리 센서 검증이 아니다. 임의 객체 크기 bin·시간 구간 조건·자동 taxonomy 변환·
개별 사용자 권한은 남아 있다. 다음 핵심은 VAD 실행 DB/queue·정책 A/B·계획 근거 연결과 CameraE2E ISP 연결이다.
상용 목표는 해당 데이터/모델·운영·출시 gate가 충족되기 전까지 열린 상태다.

## 이전 단계 기록

### 개발 상태 · 2026-09-28

## 최신 기능 · 장면 특성 검토·시각 유사 검색·Collection 환류

Scene discovery UI/API/SDK를 연결했다. 고정 Collection의 원본RGB통계/배치/히스토그램을 계산하고,
특성제안과 사람의 조명/날씨선언을 분리한다. Similarity는 학습형semantic 검색이 아닌 색상/배치 기준선이다.
동일픽셀·미캡처/모호입력·실패·필터제외를 보존하고 선택Collection과전체검색receipt를 함께 저장한다.
기존 라벨·Case조건·평가·coverage는 바꾸지 않는다. Schema15에index/review/selection 세 테이블을 추가한다.
[범위](scene-discovery.md). 최종 검증 결과는 이 단계의 인수 문서에 별도로 기록한다.
전체Python2915개·최종관련120개(중복), 개발Chrome3개·고정패키지Chrome5개와타입/build통과.
새사본14→15이관에서57업무테이블440행유지·61payload복원API두번재시작을확인했다.
원본data749파일·Gitindex불변 및 소유테스트서비스종료를확인했다. 검증된패키지는`package-pilot`이다.
[인수와제한](scene-discovery-acceptance-20260928.md) · [검증기록](validation/scene-discovery-20260928.json).

## 최신 내재화 · 자동 라벨 초안과 사람 검토

사용자가 선택한 후속 순서는 자동 라벨링→장면 태깅·유사 검색→검증 계획→실제 E2E·센서 모델 연결이다.
첫 단위는 Collection에서 만든 dataset에도 적용하는 **Automatic label drafts**다. 등록 모델·정확한
원본으로 기존 inference queue를 실행하고, 빈 미검토 이미지만 새 미검토 dataset 개정에 제안한다.
기존 검토/박스·실패·검출0·전체 분모를 유지하고 사람의 박스 수정·개별 검토는 기존 editor로 연결한다.
known model origin은 후속 개정과 Collection 라벨 복사에 남고, 생성 모델의 자기평가는 거절한다.
출처 snapshot은 신규 evaluation export에도 포함한다. 독립정답·실차정확도 인증이나 임의외부복사 추적은 아니다.
Schema14는 두 evidence 테이블만 추가한다. 원본 .data를 직접 이관하거나 운영 배포하지 않는다.
[사용법·지원범위](automatic-labels.md) · [인수기록](automatic-labels-acceptance-20260928.md).
전체Python2896개와그후최종변경관련231개(중복),개발Chrome3개·고정패키지4개·타입/build를확인했다.
13→14새사본이관에서기존55업무테이블410행유지,69payload복원API두번재시작에서원본·draft·
검토개정·모델출처를확인했다. 원본data749파일과Gitindex불변,테스트API/worker종료를확인했다.
장면 태깅/유사 검색/검증계획/학습형E2E 연결은 아직 미구현 후속이다.

## 최신 데이터 선별 · Collection→고정 Dataset→기존 평가

Data collections UI/API/SDK에서 여러 Case/dataset revision/캡처된 image를 고정 원본 집합으로
묶고, 포함·제외·라벨 출처·split을 검토해 기존 dataset과 전체 선택 근거를 원자적으로 발행한다.
정확한 동일 원본은 모든 선택 이유를 남기고 합치며 동일PNG·비영상/미캡처·미검토를 숨기지 않는다.
선택하지 않은 annotation은unreviewed이고동일labelvocabulary가아닌review를자동이관하지않는다.
두authored recording의9source참조→7원본메시지→명시적제외1/포함6과reviewed-negative2를
일반화면·고정패키지에서인수했고,새dataset의실제COCO결과는기존동일입력평가와일치했다.
실측/고객정확도·자동labeling·학습·ODD·benchmark대표성인수가아니다.
Schema13은Collection테이블3개를추가한다. 과거11백업의새복원본에서기존48업무테이블3422행
불변과schema13복원API두번재시작을확인했다. 전체Python2869개·마지막기능25개(중복),
개발Chrome3개·고정패키지3개와typecheck/build통과. 기존의존성경고2개를유지한다.
원본data749파일·Gitstaging불변,시험서비스종료를확인했다. [사용법](collections.md) ·
[인수기록](collections-acceptance-20260928.md) · [기계판독근거](validation/collections-20260928.json).

## 최신 내재화 · Case의 기록 ego/LiDAR 시계열 스택 개정 비교

일반Case UI/API/SDK와패키지에전체recorded baseline prefix의stack재생·요청비교·결과capture를연결했다.
실제authored MCAP의8tick(해당Case1tick)을재생해독립후보계산과일치했고1tick의요청변경을확인했다.
no-past/empty/stale입력의unknown_stop3tick과전체분모를유지한다. 명령은차량에적용하지않는다.
개발Chrome3업무와고정패키지2업무,새복원본API두차례재시작에서결과와원본hash유지를확인했다.
전체Python2,844개·최종관련159개(중복)및frontend타입/build통과,기존의존성경고2개유지.
DBschema12유지·Case/5확장,운영배포/커밋/원격CARLA실험은수행하지않았다.
[사용법](case-stack-replays.md) · [인수범위](case-stack-replays-acceptance-20260928.md).
학습형멀티카메라E2E·실차정합성·내부실측로그의업무수용은별도범위다.

## 최신 내재화 · 원본 이미지의 등록 모델 재평가와 Case 환류

Case에서 전체baseline split을preview하고등록모델의실제ONNX추론→동일COCO평가→A/B→
Case capture를 일반UI/API/SDK/패키지에 연결했다. 추론/평가queue와Case발행은원자적이며,
실패/취소를baselineprediction으로채우지않는다. Case/조건/기존참조재시험은함께보존된다.
실제authored6이미지의후보재평가가독립후보평가와일치했고오류증가2이미지를유지했다.
고정패키지에서는동일모델반복의delta0과Case r8까지인수했다. 내부직접작성데이터의
연결검사이며외부고객·시계열E2E·실차인지정확도인수가아니다. DBschema12를유지한다.
[사용법](case-model-replays.md) · [범위와근거](case-model-replays-acceptance-20260928.md).
다음은실제사용할모델/데이터와시간·다중센서계약확장이며예제detector/차량의품질튜닝은분리한다.

## 최신 내재화 I3 첫 단위 · Case→파생 시험→일반 Campaign→결과 capture

Case에서 수동 객체참조 template·변형·기준을 작성해 Scenario/Suite와 함께 저장하고,
명시적인 제출 시 기존 campaign/queue와 Case 링크를 원자적으로 발행한다. 완료·통과·미평가를
분리하고 종료 결과를 원래 Case의 새 개정에 capture한다. 미제출/반복시도도 activity에 남긴다.
원본 조건은 출처일 뿐 실제 sensor/ODD coverage로 승격하지 않는다. 원래 인지 실패 자동해결도 없다.
전체Python2803·최종관련162(중복), 개발Chrome3개+I3최종재검사1개, 고정패키지1개와build통과.
별도57파일복원API두번재시작에서capture·원본평가유지. 패키지API/worker종료0·원본data749파일유지.
DBschema12·기존Case/1/2는유지하고새개정에Case/3만추가했다. CARLA/운영배포/커밋은하지않았다.
[사용범위](case-retests.md) · [인수와최초패키지검사실패](case-retests-acceptance-20260928.md).
다음은 실제 사용할 모델/실행adapter 연결이며 참조모델로 실제 센서검증을 완료한 것으로 취급하지 않는다.

## 최신 내재화 I2 · 조건 분류·사례 검색·평가 연결 현황

일반 Case UI/API/SDK와 고정 패키지에 내장 taxonomy r1(3축/36셀), 수동 조건 검토 개정,
고정 평가 근거의 FP/FN·불완전 태그, 조건 검색과 사례 coverage를 연결했다.
미분류/초안·중복source와미평가를보존하고,서로다른모델의부분평가를합쳐완결로만들지않는다.
사례재고의분류표이며ODD/안전/시험통과율이나자동장면인식이아니다. DBschema12·기존원본은유지한다.
전체Python2777개와최종관련106개(중복), 개발Chrome3개/고정패키지1개 및build를확인했다.
별도백업55payload·복원API두번재시작에서현재조건·과거개정·coverage export불변을확인했다.
[사용법](case-conditions.md) · [인수와최초검사실패원인](condition-workspace-acceptance-20260928.md).
그 후속의 첫 수동객체참조시험 경로는 위 I3로 연결했다. 실제 센서조건 실행·coverage는 별도범위다.

## 최신 내재화 I1 · 로그 구간과 평가를 공유 Case로 연결

일반 UI/API/SDK에 참조형 Segment·Investigation case·불변 개정·평가 링크를 추가했다.
로그 시간 구간 또는 평가 이미지에서 만들고, 정확한 원본 ordinal·평가 이미지로 왕복한다.
같은 원본의 다른 평가를 추가하며 조사 상태/메모를 개정한다. 상태는 시험 판정이나 승인과 다르다.
Schema12는 네 테이블만 추가하며, 별도 복원본의48업무테이블3,422행 불변과API재시작을 확인했다.
전체Python2,755개·최종관련80개(중복별도), Chrome개발4개/고정패키지1개 업무와build를 확인했다.
실제ONNX/COCO를 사용한 authored 데이터 인수이며 고객정확도/4제품연동/coverage 완료가 아니다.
[사용법·경계](cases.md) · [인수기록](internal-cases-acceptance-20260928.md).
당시 다음 I2의 첫 단위는 위 조건·검색·사례 coverage로 연결했다. 남은 전체 범위는 내부 로드맵을 따른다.

## 목표 재정렬 · 2026-09-28 · 통합플랫폼 내재화

사용자는 외부 OEM 판매보다 네 기능 영역의 내부 통합을 우선한다.
[통합플랫폼 내재화](internal-platform.md)에 Data/Log, Dataset/Model Evaluation,
Experiment/Simulation, Coverage/Verification과 공통 Segment/Case 연결의 단계를 정리했다.
기존 로컬 평가·A/B·백업·패키지는 첫 하위 모듈로 유지하며, 전체 목표를 대체하지 않는다.
이번 변경은 목표·개발 순서 문서 갱신이다. 새 기능·connector·coverage 구현 또는 실제 네 제품
연동 완료를 뜻하지 않으며, 실행 코드·원본 데이터·엔진·Git staging은 변경하지 않았다.

## 최신 제품화 · 빌드된 화면의 단일 주소 실행·필수 인증·복원 재시작

178개 파일과30개 dependency 버전을 고정한 내부 로컬 패키지를 만들었다. Node/Vite/CARLA
없이 UI+API 단일 loopback 주소와 평가 worker를 실행하며, 필수 token·Host/Origin 제한·
파일/환경 preflight·소유 process 종료를 제공한다. 실제 packaged Chrome ONNX/COCO A/B
업무와 복원본Run74/Artifact349/Evaluation3의 시작·종료·재시작을 확인했다.
최종 관련Python254개통과, 원본data749파일과Gitindex불변이다. 원격연구실행은하지않았다.
신규장비cleaninstall·cleanrelease·실제고객수용·다중사용자격리는여전히미완료다.
[로컬패키지인수와실패수정기록](local-pilot-acceptance-20260928.md) · [사용법](local-pilot.md).

## 제품화 전환 · 2026-09-28

당시 첫 하위 모듈의 범위는 [첫 제품화 범위](product-pilot.md)에 보존한다. CARLA 연구의 기술 후속을 자동으로
다음 작업으로 선택하지 않는다. 일반 Detection datasets 화면/API/SDK의 평가 A/B 검토와
전체 분모 JSON/HTML export, cold workspace backup/verify/새 디렉터리 restore를 구현했다.
검사 범위와 실제 사용 인수는 [제품화 기록](product-pilot-acceptance-20260928.md)에 별도로 남긴다.
아래 결과는 보존된 연구 이력이며 일반 worker 배포나 상용 release 완료를 뜻하지 않는다.

**전체 목표는 진행 중입니다. 현재 결과는 AXIOM v0.1 개발 기반이며 상용 출시 완료가 아닙니다.** 첫 실제 엔진의 우선순위는 사용자 선택에 따라 CARLA 주행·인지 시험입니다.

## 최신 · 모델 revision 연결과 추가 3조건 A/B, 후보 인수 1/3

모델 ID·revision·실행 코드/설정 hash를 private 실행에 연결하고 사전 선언한6건을 완료했다.
초기1.5m/s close-lead 후보는 jerk28.278125→3.469571로 기존 지표와 계획정지/hold 검사를 통과했다.
초기2.5m/s는66.359845→49.170111로 실패이며, cruise는48.786212→56.434983으로 악화됐다.
두 실패 조건의 관측 prefix 변동과 긴급정지 경로가 남아 있고 후보를 기본 모델로 승격하지 않았다.
480명령·486관측을 모두 검증하고 기존 지표 판정과 추가 행동 판정을 분리했다.
같은 모델 hash를 세 조건에 고정하며 원래 제어기·인지·정책·센서와 평가 기준은 유지했다.
경사 시험은 미구현·미실행이고 GDB-only 설정은 일반 worker/API 배포가 아니다.
다음 우선 과제는 원본 LiDAR/rig/grid에서 지면 관측 prefix 변동을 해결하는 것이다.
[모델 식별·추가 조건·실패 해석·결과 화면](carla-model-validation-20260927.md).

## 이전 단계 · 종방향 타이어 제약 감쇠 후보, 지정 close-lead 기준 통과

별도 엔진에서 제약 활성 규칙·제어기·평가 기준을 유지하고, 생성된 종방향 sticky 행의 감쇠만 1000에서 4로 변경했다.
새 A/B의 jerk는 **29.438903 → 3.730783m/s³**, 최대 감속은 1.989264m/s²로 기존 기준을 통과했다.
이동 거리는 약 6.6mm 증가했고, 2.15초에 속도 0.005489m/s로 기존 정지 판정 조건을 만족해 hold로 전환했다.
두 조건 모두 긴급정지는 없었다. 160명령·162관측을 재계산하고 후보 감쇠 필드 24,812개의 실제 변경 및 허용 범위 밖 bytes 불변을 검증했다.
이는 단일 close-lead 조건의 실험적 차량 모델 통과이며 실차 보정·반복 신뢰성·운영 배포 완료가 아니다.
첫 계측 중단 1건은 별도로 보존했다. 시험용 프로세스 종료, 운영 소스 92파일·데이터 749파일 불변, 선택 회귀 106개 통과와 비교 화면 검사를 확인했다.
[실험 조건·구현 범위·한계·비교 화면](carla-sticky-damping-20260927.md).

## 이전 단계 · 별도 엔진 native 정지 상태와 실제 업데이트 시간 계측

기존 제어기를 바꾸지 않고 별도CARLA2300에서 native 상태를 실제로 읽었다.
1.95초 급정지 관측에 네 바퀴 종방향 sticky-tire 제약이 활성이고 sleep은false였다.
sleep은2.35초로0.40초 후행하며 내부brake도0.073602에서full-brake로 바뀌지 않았다.
Ego81/81 frame·actor를 정확히 연결했고 API wheel omega는native corrected speeds와 일치한다.
차량 Update는103frame/5,150호출,frame당50회·약1ms로 직접 확인했다.
client의max_substeps1–16범위 경고는 별도로 남으며 다른 빌드나0.5ms의 실제 동작까지 보장하지 않는다.
Jerk29.439052는 여전히실패이고 실제brake torque/constraint impulse는미관측이다.
최종선택회귀77통과,offline뷰어81행 확인,private엔진종료와production/data불변을 확인했다.
[직접 관측·실패 이력·한계·비교 화면](carla-native-stop-observer-20260927.md).

## 이전 단계 · 저속 brake 해제 원인 분리, 추가 후보는 미채택

실제 close-lead3건에서 cap/1 기준 jerk29.439052, 지속 해제35.292332,
50ms 한 구간 해제28.953878m/s³로 모두 기존10 기준에 실패했다.
해제 시 약0.088m/s에서 회전이 회복되지만, 지속하면 plan expiry로 긴급정지하고
재제동하면 약0.072m/s 급정지가 재발한다. 두 진단 controller는 기본 미등록·미채택이다.
설치 binary/debug Build ID 일치와 정적 brake/PhysX 코드 대조까지 진행했다.
실제 sticky/torque/sleep 플래그는 미관측이므로 특정 엔진 경로를 원인으로 확정하지 않는다.
다음은 native 정지 상태의 동일 physics-clock 관측이며 단순 이득·해제 탐색을 반복하지 않는다.
[3건 원본·한계·검사·비교 화면](carla-brake-transition-20260927.md).

## 2026-09-27 · close-lead 저속 제동 후보, 개선됐지만 jerk gate 미통과

실제 close-lead7건에서5ms 기준7.751580/154.921008m/s²·m/s³ 대비,
1ms·탐색 응답 gain4.029874·속도 비례 말단 brake 상한은 **1.989260/29.439052**였다.
최대 감속74.34%·jerk81.00% 감소지만 원래 jerk≤10 기준은 실패다.
gain 보정 단독과 가속도 피드백의5ms 결과는 악화돼 미채택으로 보존했다.
1ms/0.5ms의 기존 제어기는 plan expiry에서 긴급제동했고, 응답 보정 후에는 hold로 종료됐다.
최종에도0.073602m/s에서 한 frame 안에 거의0으로 되는 현상이 남았다.
실제560명령/567관측과 원본·설정581회 확인, 소스6archive, 계약시험603개+별도terminal10개를 구분한다.
제어 후보는 기본registry 미등록, production10ms·기존Run/판정·데이터749파일 유지다.
[조건·실패 후보·남은 원인·비교 화면](carla-brake-tail-20260927.md).

## 후속 완료 · 5ms를 기존 실제 LiDAR 주행 경로에 적용

Cruise와 close-lead를 각각 새 10ms 기준군/5ms 후보로 실행했다. 네 건 모두
320명령·324관측을 보존했으며, **5ms cruise의 최대 감속만 8.800123→5.495618m/s²로
원래6 기준을 통과**했다. Jerk는173.610865→78.806097m/s³로 여전히10 기준 실패다.
Close-lead는 새 기준군7.971694/157.032756에서 후보7.751580/154.921008로 두 항목 실패다.
Cruise는 관측 지면 범위6→5→4m 뒤 emergency_stop, close-lead는 controlled_stop 중
brake0.151393에서도0.387862→0.000283m/s로 급감하는 정지 말단 응답이 남았다.
후보는 생성·rest 이전부터 적용하지만 기존 target-velocity 초기화는 유지하며 PI 준비 통과가 아니다.
Production 기본값·과거 Run/판정은 그대로다. 다음은 저속 brake 응답·말단 제어와 관측 범위 변동의 분리 개선이다.
[실제 결과·구현·검증·비교 화면](carla-live-physics-candidate-20260926.md).

## 2026-09-26 · 실제 substep 기준군/변경군 비교

동일 225개 준비 요청과 직접 비교한 전환 상태에서, 고정 throttle의 추가 2초를
0.01초 기준군과 0.005초 변경군으로 실제 실행했다. 본 비교 두 건은 각각 40명령·41관측,
`collected`·`cleanup=confirmed`이며 가속도 부호 교대는 **39/39→0/39**,
속도 차분 기반 최대 절대 jerk는 **8.592635→0.547525m/s³**였다.
두 군 모두 설정 API를 호출하고 그 내부 1tick을 첫 계측 구간으로 포함한다.
분석은 2/2 verified·comparable이며 최종 관련 회귀 135개와 실제 보고서 Chrome 탐색이 통과했다.
운영 DB SHA는 같고 production 기본 설정·기존 주행 실패 판정은 변경하지 않았다.
이는 제한된 조건의 substep 민감도 근거이며 정속·제동·센서 모델 또는 상용 gate의 통과가 아니다.
[실험 조건·결과·원본 경로](carla-substep-observed-pair-20260926.md).
[최종 검증 JSON](validation/carla-substep-comparison-2026-09-26.json)은 원격 원본 bytes,
release 92파일, 로컬 데이터 749파일 보존과 별도 잠금 거절 요청을 구분한다.

## 현재 진행률과 사용 범위

2026-09-26 검증 마감: [중복 제외 실행 집계와 최신 재검증](carla-substep-closeout-20260926.md).
완료 native 진단 8개 ID·두 준비 기준을 구분해 원본을 확인했고 새 엔진 실행은 없었다.
이번 별도 선택 회귀는 183개 통과이며 앞선 135/167개 검사와 합산하지 않는다.
다음 인수는 원래 cruise/close-lead의 감속·jerk 재평가이며 상용 진행률은 그대로다.

2026-09-26 고정 소스 추가 근거: [실제 substep 비교 2건과 독립 수치 재계산 · edc6be0b](carla-substep-pair-edc6be0b-20260926.md).
각 원본 실험 ID와 해시를 별도로 유지하며, 다른 보고서의 겹치는 시험 수와 합산하지 않는다.

기능 범위에 대한 개발 판단으로 **전체 상용 목표 약25%(20–30% 범위), 첫 CARLA 개발 파일럿 약75%(70–80% 범위)**입니다. 시험 통과율·남은 시간·출시 준비도를 계산한 수치가 아닙니다. 아래 개별 gate의 미완료를 진행률로 대체하지 않습니다.

| 현재 사용할 수 있는 기능 | 현재 범위 |
| --- | --- |
| 시험 작성과 실행 | 시나리오·개정·suite/campaign·조건 변경, 영속 큐·별도 worker·취소·원격 결과 회수 |
| 원격 CARLA | Linux build1ae5356·Town10HD_Opt, adapter0.8.0의 불변 센서 설정·rig mount 적용, 기본 GT RGB/LiDAR capture와 별도의 live LiDAR stack |
| 결과 조사 | 기록 궤적의 도식3D, 원본 RGB/점군·MCAP 재생, 관측→판단→요청/ACK→다음 physics와 실패 진단 |
| 모듈 교체 | 관측·정책·제어의 정확한 version 선택, 불변 stack revision, kernel1/2 병행, 기록 재계산 |
| 인지와 평가 | 선택형 CPU ONNX/YOLOX 추론, 이미지 bbox 라벨·split·검토 선언, 실제 COCO 계산. 실차 정확도는 미인수 |
| DB·근거·Agent | 요구사항/시험/결과 관계, revision·seed·fidelity·파일 hash, 동일 API·OpenAPI·Python SDK·CLI·문서 |

보정된 광학·RAW/ISP·센서/차량 모델, 실차 로그 pilot, Neural Reconstruction, HIL, 지도 제작, 학습형 인지 폐루프와 조직 권한·분산 실행·고객 인수는 남아 있습니다. 현재 UI는 로컬 개발판이며 고충실도 센서 영상과 도식3D를 구분합니다.

## 2026-09-14 당시 작업 · 입력을 고정해도 남는 실제 저속 진동

외부 접속 추가(2026-09-14): 사용자 선택에 따라 비밀번호로 보호된 임시 HTTPS
**조회 전용** gateway를 제공했습니다. production 화면·감사한 GET API 90개만
연결하고 수정/실행·원격 상태 probe는 차단합니다. 관련 시험38개와 실제 HTTPS 검사54개,
Safari·Codex 내장 브라우저 로그인/기존 결과 표시를 확인했습니다. 기존6개 업무 테이블의
내용 해시를 보존했으며 새 시뮬레이션은 실행하지 않았습니다. 조직 권한·상시 운영 배포나
상용 gate 완료가 아닙니다. [접속 범위·종료 방법](external-access.md).

새 standalone 입력 분리 도구로 원본 225개 준비 요청을 재생한 뒤 PI 유지와 마지막
요청 throttle 고정을 각각 2초 실행했습니다. 두 건 모두 225/226 준비와 40/41 연장,
원본·두 분기 사이 전환 상태 비교를 완료했습니다. 선언한 전환 상태 차이는 모두 0이며,
고정 throttle에서도 구간 가속도 부호가 39/39번 교대했습니다. **제어 입력의 교대만으로
현재 진동을 설명할 수 없습니다.** 다음은 동일한 입력에서 physics substep의 영향을
분리하는 실험입니다. 타이어·접촉·solver 원인은 아직 확정하지 않았습니다.

기존 worker·엔진·원본과 49개 테이블 3,394행·관리 파일 745개를 보존했습니다.
두 실제 실행은 별도 개발자 진단이며 플랫폼 Job/Run, 제동 성능 통과 또는 모델 보정이
아닙니다. 전체 약25%·제한된 CARLA 개발 파일럿 약75% 판단은 유지합니다.
[사용법과 실제 결과](carla-throttle-isolation.md) · [검증 기록](validation/carla-throttle-isolation-2026-09-14.json).

## 고정 위치 준비의 실제 실패 확인

새 standalone protocol3의 PI 준비·최초 12m 진입 검사와 원본 재계산을 구현했습니다.
실제 commissioning 1건에서 준비 225명령·226관측을 수집했지만, 마지막 1초의 속도
1.861397–1.943290m/s와 교대하는 가속도·raw 바퀴 상태가 고정 조건에 실패했습니다.
예정된 계측 80명령은 모두 `not_attempted`이며 제동 pulse는 0건입니다.
실패 원본 3,155 event와 정리·예약 해제를 보존했고, 별도 원격 조회에서 기존 23actor와
world/settings/weather 및 worker/engine 상태를 확인했습니다.
[조건·실제 결과](carla-brake-road-entry.md) · [검증 기록](validation/carla-brake-road-entry-2026-09-14.json).

**단순 PI 추가로 정속 준비가 해결되지 않았습니다.** 제어 입력과 엔진 응답이 함께
변하는 현재 기록만으로 원인을 분리할 수 없습니다. 성공한 새 coast 기준과 제동 비교가
없으므로 제어기 보정·주행 품질 개선 또는 전체 진행률 증가로 세지 않습니다.

## 독립 차량 응답 진단 도구 · 실제 coast 네 건

**개발자용 standalone native 진단 도구를 구현하고 실제 CARLA에서 초기화 비교 네 건을 완료했습니다.** 플랫폼의 job/API/Run 기능을 추가한 작업은 아닙니다. 입력 작성기·한 번만 실행하는 bounded runner·원본 hash 검증 분석기로 구성하며, 단일 Tesla와 collision observer를 사용합니다. LiDAR·선행차·production controller가 없는 명시적 실험 조건입니다. [도구 사용법](carla-brake-diagnostic.md) · [원래 실험 제안과 현재 범위](carla-brake-response-design.md) · [실제 근거](validation/carla-brake-initialization-2026-09-14.json).

Protocol2로 injection1→throttle1→injection2→throttle2를 교차 실행했습니다. 각4초의 **80개 coast 명령·81개 관측**, 합계320명령·324관측을 보존했습니다. 네 건 모두 rest26tick 뒤 안정 조건을 만족했고 cleanup은 confirmed입니다. 요청 throttle/brake/steer0과 다음 frame의 float32 readback이320/320 일치합니다. 준비 마지막 명령이 남아 있는 첫 관측은 그 다음80개 명령의 응답과 구분합니다.

| 실제 초기화 조건 · 각2회 | 시작 속도 m/s | 0.25초 속도 m/s | 4초 속도 m/s | 원본 reported gear |
| --- | ---: | ---: | ---: | --- |
| Rest 뒤 target velocity 요청 | 1.957952 | 1.954409 | 1.925977 | 0 |
| 명시적 throttle 준비 | 1.948817 | 1.754365 | 0.368334 | 1 |

두 반복은 각 방식의 수치를 재현했지만 통계적 독립성을 입증하지 않습니다. Throttle 준비의 마지막5표본은1.9–2.1m/s 안에서도 계속 감속했고, 계측 시작 위치는 injection보다1.142824m 앞입니다. 자세·입력 이력·구동계 상태도 달라 **초기화 방법만의 순수 원인 효과로 단정할 수 없습니다.** Raw gear0/1과 RPM 차이는 후속 진단의 단서이며 엔진 제동·타이어 힘·토크·단위의 검증이 아닙니다.

앞선 protocol1은 rest 낙하의 수직속도−3.43m/s에 전 단계 XYZ 속도 상한이 적용되어 초기화·계측 전에 실패했습니다. 실패 원본·80개 not_attempted 분모·정리 근거와 당시 소스를 보존했습니다. Protocol2는 rest/목록 갱신 단계에 명시적인 낙하 중단 경계를 적용하며, rest 안정 판정과 준비·계측의 속도 제한은 유지합니다. 과거 protocol1은 읽을 수 있지만 새 runner는 그 입력을 실행하지 않습니다.

**원래12개 screening 제안 중 첫 초기화 비교4개만 수행했으며 brake pulse는0건입니다.** Throttle coast의 step5 속도1.754365m/s가 pulse 진입 조건1.9–2.1m/s 밖이므로, 나머지8개는 새로운 초기·진입 상태 계약을 정하기 전까지 미실행으로 둡니다. 제어기·차량 모델 보정이나 주행 품질 gate를 통과한 결과로 세지 않습니다.

읽기 감사에서 기존49개 테이블의3,394개 행·모든 원래 열·관리 파일745개를 보존했고 FK오류는0입니다. 운영 worker heartbeat는 행의 byte 일치 대상에서 제외했습니다. 원격 기존source92개와 worker/engine PID를 유지했으며 서비스·엔진을 재시작하지 않았습니다. 새 protocol·입력 작성·runner·분석기 관련 **236개 시험이3.98초에 통과**했고, 기존 native/helper **77개 시험의9.36초 통과**는 별도 범위입니다. 작성한 fake world와 실제 완료4건·계측 전 실패1건을 구분합니다.

독립 읽기 검사에서5,367개 event와320명령·324관측을 대조했습니다. 실제 비교 HTML에서 구간·실행 선택, 계측324행, 실패한 정착6행, throttle 준비38행과 원본 hash 표시를 확인했고, 원본·분석 파일42개의 HTTP 응답 bytes가 저장본과 일치합니다. 네 완료 결과는 모두4초 내 정지 미관측입니다. 전체 상용 약25%·CARLA 개발 파일럿 약75% 판단은 그대로입니다.

## 센서·스택을 고정한 campaign · 실제 두 조건 연결

**Schema11의 campaign control을 로컬 API·worker·UI에 적용했습니다.** 개별 실행과 같은 context/1·context/2 선택으로 stack·rig·센서 설정의 정확한 개정을 전체 시험 묶음에 고정합니다. 선택 자산을 같은 DB snapshot에서 한 번 읽고 case마다 seed와 입력 변경을 적용합니다. 한 case라도 준비할 수 없으면 campaign/job 전체를 저장하지 않습니다. [사용법](campaign-control.md) · [계약](suites-contract.md) · [인수 기록](validation/campaign-control-2026-09-14.json).

Campaign 자체에 저장한 계획·멤버 ID·예상 input/context/request hash를 실제 job/run과 대조합니다. 계획 유실·추가 멤버·입력 변조를 정상 평가로 숨기지 않으며 취소 전에 다른 job으로의 치환을 차단합니다. 정상적인 누락 case는 원래 분모와 고정 계획을 유지합니다. UI·OpenAPI·SDK·CLI에서 같은 계약을 사용하고, 응답 유실 때 같은 본문·키로 재요청합니다.

통합 과정에서 CARLA의 `observed contacts`와 suite의 `count`가 달라 충돌 관측 항목이 미평가되는 누락을 확인했습니다. 새 controlled plan은 `carla-observed-contact-count/1`을 명시적으로 고정하고, 소스를 확인한 adapter0.8의 유한·비음수 정수 관측값만 그 기준에 적용합니다. **원본 값·단위·기준은 유지하며 접촉 수0은 무충돌 보장이 아닙니다.** 다른 단위·버전·미지원 값은 계속 미평가입니다. 과거 campaign에는 이 해석을 소급 적용하지 않고, 측정 계약이 바뀌면 비교 불가로 표시합니다.

실제 `campaign_0b932a7fe844433fafe9213f466f187a`는 suite r1의 두 case를 같은 기존 sensor configuration r1·rig r1·stack r1로 한 번 제출했습니다. 두 case의 입력·context hash는 앞선 개별0.8 시험과 정확히 같습니다. 각4초·80명령/81관측을 완료했고 **2/2 case·8/8 기준이 평가되어4통과·4실패, 미평가0**입니다.

| 실제 campaign case | Run | 최대 감속 m/s² | 최대 jerk m/s³ | 판정 | 실행 wall |
| --- | --- | ---: | ---: | --- | ---: |
| 이동 선행차 · gap20m | `f9f49091` | 8.800123 | 173.610865 | failed | 55.247초 |
| 정지 선행차 · gap8m | `16a761a5` | 7.870616 | 157.169649 | failed | 55.686초 |

접촉 수·TTC는 기준을 충족했고 감속6m/s²·jerk10m/s³는 두 case 모두 실패했습니다. 실패를 포함한 원본 보고서를 baseline `baseline_4f9d2f11a97e435184d0b03b897bc2a6`으로 저장했습니다. 화면에서 완료/판정의 구분, 기대·관측 hash 일치, 관측 접촉 수 한계, baseline 원본과 실제 run으로의 이동을 확인했습니다. 새 기준 저장은 승인이 아닙니다.

독립 원본 조회에서 archive2개·native artifact18개의 bytes와 게시 artifact20개의 hash/크기, metrics·trajectory를 대조했습니다. 160명령 ACK·162physics·162장착 관측이 보존됐고 원본27파일은 그대로입니다. 상태 조회·archive 읽기에는 재실행·회수 작업·cleanup 요청이 없습니다. 원격0.8 worker와 CARLA 엔진은 재시작하지 않았고, 별도 실행 전후 조회에서 같은 world/map/settings/weather·원래23actor·격리 없음과 원격source92개를 확인했습니다. 이 로컬 변경의 source와 기존 원격 release의 source 범위를 구분합니다.

Schema10→11은 nullable plan 열 하나만 추가했습니다. **기존46개 업무 테이블의3,340개 행·모든 원래 열·중복 수·관리 파일725개를 보존**했고 과거3campaign의 plan은 NULL입니다. 기존3campaign/25case의 원래 입력·근거 연결을 API·SDK로 확인했고 baseline35,093bytes·comparison82,980bytes의 export는 원본 bytes와 같습니다. 과거 campaign 보고서2개는 조회 시각을 제외하고 봉인 원본과 대조했고, 나머지1개는 과거 HTTP 응답이 없어 원래 DB 근거와 현재 API·SDK의 동등성을 확인했습니다. 현재 snapshot은73runs·80jobs·348artifacts·11recordings, FK오류0·활성 job0입니다. 자동화 시험의 정확한 실행 범위·실패 수정 이력은 위 인수 기록에 구분합니다.

최종 전체 Python **1,979개가314.55초에 통과**했고 기존 dependency 경고2개는 남습니다. 첫 전체 실행의1973통과/5실패는 시험의 schema/sequence 기대값4건과 COCO 취소 시점의 시간 의존1건을 수정한 뒤 재검증했습니다. 생산 코드는 실제 실행 이후 바뀌지 않았습니다. Chrome 관련22개 통과 후 측정 계약·누락 멤버 표시를 보완한 최종7개(51.1초)와 build가 통과했습니다. 두 브라우저 범위는 중복이며 합산하지 않습니다. 마지막7개의 첫 실행에서 발견한 HTTP fixture 종료 경합도 시험 수명 동기화로 수정했고 원래 로그를 보존합니다. 작성한 입력·가짜 world/HTTP의 계약시험과 실제2건의 엔진 인수는 별도 근거입니다.

주행 품질과 전체 상용 gate는 여전히 미완료입니다. 최신 close-lead 80구간에서 도출한 [제동 응답 실험 제안](carla-brake-response-design.md)의 초기화 비교4건은 위 standalone 도구로 수행했고, pulse8건은 진입 상태 계약 재정의 전까지 미실행입니다. 새 campaign 기능이나 단위 계약을 제동 개선·실측 센서 정확도 인수로 세지 않습니다.

## CARLA 0.8 · 재사용 센서 설정과 실제 장착 적용

**CARLA adapter0.8.0·context/2·schema10을 구현·배포했습니다.** `Simulation sensors`에서 광선 설정을 불변 개정으로 작성하고, 기존 rig의 위치·회전·frame 개정과 stack 개정을 정확히 선택합니다. UI·REST/OpenAPI·Python SDK·CLI가 같은 계약을 사용하며, 지원하지 않는 엔진·capability나 조회하지 못한 개정은 제출을 막습니다. 전체 기본값·설정/rig/capability snapshot·seed·hash를 제출 시 고정하고, 기존 context 없는 실행과 context/1의 지붕 profile3개·원본 archive는 유지합니다. [사용법](carla-configurable-sensors.md) · [실제 인수 근거](validation/carla-configurable-sensors-2026-09-14.json) · [당시 설계와 gate](carla-configurable-sensors-design.md).

새 경로는 요청 속성, detached blueprint, 실제 actor 속성을 구분하고, startup 및 같은 world frame의 ego/sensor/API/measurement pose에서 선언한 mount의 적용 여부를 확인합니다. 원본 XYZI·MCAP·전체 소비 XYZ·80명령/81관측과 별도 `carla-sensor-configuration.json`을 보존합니다. UI는 run 문맥·크기·SHA와 행렬/parent/frame 관계를 검사한 뒤 표시하며, 누락·오류·다른 선택의 조회 실패에 이전 근거를 남기지 않습니다. 원본 점을 삭제하거나 관측값으로 선언한 변환을 몰래 교체하지 않습니다. Actor 속성의 seed readback은 내부 RNG 적용·물리 calibration의 증명이 아닙니다.

| 실제0.8 조건 | Run | 저장 판정 | 최대 감속 m/s² | 최대 jerk m/s³ | 실행 wall |
| --- | --- | --- | ---: | ---: | ---: |
| 정차 · rig r1 `[2.5,0,1.5]` | `288ee6ab` | passed | 0.017588 | 0.396583 | 54.274초 |
| 같은 rig r1 · 이동 선행차 cruise | `dff76e13` | failed | 8.800123 | 173.610865 | 55.149초 |
| 회전 정차 · rig r2 `[2.5,0.4,1.5]`, roll5°/pitch−8°/yaw20° | `8b161951` | passed | 0.017588 | 0.396583 | 53.786초 |
| rig r1 · 가까운 정지 선행차 | `1006c7a9` | failed | 7.870616 | 157.169649 | 55.442초 |

실제4건 모두 실행·저장은 완료됐고 실제 actor 속성·81/81 pose가 선언과 수치 허용오차 내 일치했습니다. 두 정차 시험은 **80구간 hold·throttle0의 장착 진단**이며 주행·정지 성능 통과가 아닙니다. r2의 최대 위치/회전 잔차는 각각4.355e−6m/1.796e−7이고 r1 원본도 유지했습니다. Cruise는 pilot_cruise1·controlled_stop1·emergency_stop78이며 감속6m/s²·jerk10m/s³ 기준을 그대로 실패했습니다. Close-lead는 pilot_cruise1·controlled_stop42·hold37이고80/80구간에서 양의 높이 표면을 관측했지만, 최대 감속·jerk 기준은 실패했습니다. 표면의 semantic ID를 확인하거나 측면·박형 장애물의 가시성을 인수한 시험은 아닙니다. 이 실행들은 미보정 ray-cast LiDAR와 ego/map oracle을 사용하는 제한된 진단입니다.

Cruise의 첫 관측 구간은6→5→4m로 줄었고 reserve2m를 뺀 가용2m가 정지거리 추정2.816m보다 짧아져 급정지로 전환됐습니다. 이후77구간은 관측이 회복돼도 급정지 상태를 유지했습니다. 이번80구간에는 양의 높이 표면이 없었으므로 이전 자차 반사와 같은 원인으로 분류하지 않습니다. Close-lead의 최대 감속·jerk는1.55/1.60초의 `controlled_stop` 중 발생했고 첫 `hold`는2.15초였습니다. 당시 계획 감속과 실제 속도 변화가 달라 차량 제동 응답의 미보정 가정을 검토해야 합니다. 어느 시험도 실제 완만 정지 품질을 인수하지 못했습니다.

별도 재계산에서4건의 stateful 출력320개(처리 시간만 제외), 관측324개·ACK320개·MCAP648개가 일치했습니다. 모든 원본549,669점에 독립 quaternion 회전행렬을 적용한 float32 XYZ도 저장 bytes와 정확히 같았고 원본52파일을 보존했습니다. 새0.8 reader로 과거0.5 완료/부분·0.6 완료·0.7 kernel1/2 완료 archive6개와 artifact49개를 실제 조회해 원본 bytes/hash 일치를 확인했습니다. 과거0.6/0.7의 실제 부분 archive는 없어 그 범위는 검증하지 않았습니다. 현재 UI에서도 회전 rig r2·선택된 frame·81개 실제 pose 근거, 설정 r1과 rig r1/r2의 개정 목록을 확인했습니다. 재계산·읽기 검증에는 추가 시뮬레이션이 없습니다.

배포·4건 실행 후 **기존 업무43개 테이블의3,260개 행·모든 열·중복 수, 관리 파일685개**를 보존했고 고정 source92개의 hash 일치도 확인했습니다. Schema9→10은 설정 head/revision/멱등 기록3개 테이블을 추가했으며, 최종 snapshot은71runs·78jobs·328artifacts·11recordings, FK오류0·활성 job0입니다. 운영 heartbeat·예약 상태와 schema version/sequence는 원본 행 일치 대상에서 분리해 확인했습니다. 각 실제 실행 뒤 원래23actor·동일 world/settings/weather를 별도 비교했으며, 회전 rig r2를 저장한 뒤에도 r1 원본은 그대로 읽었습니다. 이 보존 검사는 센서·주행 성능 보장이 아닙니다.

전체 Python **1,934개가325.28초에 통과**했고 기존 경고2개는 남습니다. Chrome 전체 실행은 **63개 통과·기존 version 기대값1개 실패**였으며, 그 기대값을 수정한 뒤 관련 **13개가 통과**했습니다. 후자는 신규 설정6개와 차량 응답7개의 겹치는 범위이고 전체64개 재실행으로 표시하지 않습니다. 최종 TypeScript/Vite build도 통과했습니다. 작성한 fake world·browser 계약시험과 위 실제 엔진 실행을 구분합니다.

**남은 인수:** 설계 gate5의 근접·측면·박형/낮은 외부 장애물과 semantic observer를 통한 전체 시야 검증, context/2 자체의 실제 응답 유실·after-ACK 종료·초기 mount 실패 주입은 아직 미완료입니다. 주행 진단을 이 gate나 실제 S-curve 완만 정지의 통과로 바꾸지 않습니다. 이전0.5 이후의 장애 회수와 과거 archive 읽기 검증은 보존하되 새 context/2 장애 인수로 세지 않습니다. 전체 상용 gate와 위 진행률 판단도 유지합니다.

## 근접 LiDAR의 자기 차체 반사 확인 · 0.8 이전 진단 기록

수정한 정적 진단으로 실제 CARLA의 같은 지붕 장착 조건에서 **8/8프레임·일반 LiDAR54,448점 전체가 같은 채널의 semantic XYZ와12바이트 단위로 일치**했습니다. 그중 근접 장애물 후보8,853점은 모두 현재 자차 actor252의 표면에 대응했습니다. 근사·모호·미대응0, parent·실측 pose·원본16파일·192event를 확인했고 별도 구현으로 재검증했습니다. Semantic ID는 진단 관측이며 제어 입력으로 넣지 않았습니다. [분석 방법·도구](carla-self-hit-diagnostic.md) · [실제 근거](validation/carla-self-hit-confirmed-2026-09-14.json).

진단 도구의 반환 센서 참조 보존·소유권 충돌 검사·센서 우선 정리·callback 종료를 수정했습니다. Repository의 관련 pytest19개와 별도의 실제 정차 수집1건을 구분합니다. 실제 프로세스는 exit0, callback16개 종료·활성0, 원래23actor·map/settings/weather 복원이며 당시 CARLA0.7 ready/idle이었습니다. 당시67runs·74jobs·288artifacts 및 기존 업무 행·열·관리 파일658개를 보존했습니다. **원인 확인이며 급제동 해결이나 완만 정지 인수는 아직 아닙니다.** 후속 센서 설정 개정과 rig 위치·회전 적용 경로는 위0.8 항목에 구분합니다.

앞선 정적 진단은 유효0/8쌍에서 실패하고 센서2개를 남겼으며 별도 정리했습니다. 그 실패 보고서와 cleanup uncertainty는 [이전 근거](validation/carla-self-hit-diagnostic-2026-09-13.json)에 그대로 보존했습니다. 과거 ID 없는 XYZI에 이번 actor ID를 소급 부여하지 않으며 아래 주행 결과도 변경하지 않습니다.

## Stack kernel2 · CARLA0.7 실제 연결과 남은 제동 문제

별도 kernel2에 실제 지면 점의 격자/관측 구간, 양의 높이 표면, 보수적 정지거리, jerk 제한 S-curve 계획과 정지 상태 유지를 구현했습니다. UI/API/기록 replay/실제 CARLA가 같은 저장 계약을 사용합니다. Kernel1의 기본값·이전 manifest/output bytes를 보존했고, 당시 near recipe와 kernel2 live는0.7에서 도입했습니다. 현재0.8도 이 기존 경로를 유지합니다. [사용법](stacks.md) · [인수 기록](validation/stack-v2-2026-09-13.json).

새 near recipe는64채널·명목256,000광선/s·하향FOV−65°이며 기존 baseline/dense를 자동 교체하지 않습니다. 같은 서버에서 각4초·80명령/81관측을 정상 큐로 실행했습니다.

| 실제0.7 시험 | 저장 판정 | 최대 감속 m/s² | 최대 jerk m/s³ | 동작 |
| --- | --- | ---: | ---: | --- |
| Near + kernel1 · 이동 선행차 gap20m | failed | 8.633477 | 170.319628 | 80구간 obstacle stop |
| Near + kernel2 · 이동 선행차 gap20m | failed | 8.633477 | 170.319628 | 80구간 emergency stop |
| Near + kernel2 · 정지 선행차 gap8m | failed | 6.219846 | 73.066306 | 80구간 emergency stop |

**세 작업의 실행·저장은 완료됐지만 주행 품질은 모두 실패했습니다.** Near는 앞쪽 첫 격자에 지면 표본을 추가했으나 차량 원점 주변의 양의 높이 표면도 잡았습니다. 첫 cruise 입력에는 해당 점1,130개와 범퍼 기준 거리0m가 보존됐고 kernel2는 현재 정지거리가 관측 한계를 넘는 것으로 판단했습니다. 당시 자기 차체 반사가 후보였지만 원본 XYZI에 hit actor ID가 없어 해당 원본의 출처를 확정하지 않았습니다. 점 삭제·조건 완화로 통과시키지 않았으며 **실제 S-curve 완만 정지 효과는 미인수**입니다. 4초의 실제 wall은 약50.3/57.6/56.5초로 realtime도 아닙니다.

전체 Python1,798개(기존 경고2개)가 통과했습니다. 이후 이전 replay의 optional field/출력 hash 보존과 v2 제어 상한을 보완하고 관련 API142개 및 core/native117개를 각각 통과했습니다. Chrome28개와 마지막 kernel identity5개(중복4개), production build가 통과했으며 작성한 입력/가짜 world의 계약시험을 위 실제 엔진3건과 구분합니다. 실제 UI에서도 저장된 v2의 격자·unknown cell·정지 이유·전체 점군·마지막80번째 ACK/다음 frame을 확인했습니다.

새 실제3건을 원본으로 다시 계산해 출력240개(처리 시간만 제외), 관측243개·MCAP 메시지486개·ACK→다음 physics240개와 지표15개가 일치했습니다. 새0.7 reader로 원래0.5/0.6의 archive4개·artifact33개도 정확한 원본 bytes로 조회했습니다. 이 재계산·조회에는 재시뮬레이션이 없습니다. 배포 전 **64runs·71jobs·261artifacts를 포함한 모든 업무 행·열과 관리 파일658개**를 보존했고, 현재는 **67runs·74jobs·288artifacts**, FK오류0·활성 job0·CARLA ready/idle입니다. 배포 source86개 hash가 일치하고 각 실행 뒤 원래23actor·settings/weather 복원을 별도로 확인했습니다.

당시 후속 과제는 근접 표면의 actor 출처 확인과 센서 장착/자차 취급 계약, 실제 완만 정지 인수였습니다. 별도 진단과0.8 장착 적용은 위에 구분하며 완만 정지 품질은 아직 미인수입니다. 엔진 RNG seed source patch는 아직 미빌드·미배포이며 전체 상용 gate도 열려 있습니다. 아래 항목은 이전 인수 시점의 기록입니다.

## 모듈 버전 병행·CARLA 실패 원인 조사 당시 기록

동일 component ID의 여러 version을 registry에 함께 등록하고 정확한 버전으로 조회합니다. 새 버전 설치가 이전 버전을 덮지 않으며, version을 생략했을 때 후보가 여러 개면 거절합니다. UI는 정확한 버전을 선택하고 저장된 버전이 없거나 unavailable이면 Publish를 막습니다. 두 stack workflow 예제도 ID/version/role/status를 고정하여 catalog 순서 때문에 다른 알고리즘을 선택하지 않습니다. [사용법](stacks.md#모듈-버전을-나란히-설치하기) · [검증·배포 근거](validation/stack-component-versions-2026-09-13.json).

- Backend/API에서 두 controller version의 공존·명시적인 새 stack revision·이전 manifest bytes/hash·기존 대기 replay의 v1 실행을 검증했습니다. 추가 version은 작성한 시험용 component이며 새 주행 controller 인수가 아닙니다.
- Python **1,665개(209.72초, 기존 경고2개)**가 통과했습니다. 이번 명령은 backend·SDK·CARLA·ONNX·COCO·scripts를 포함합니다. Chrome **9개(31.80초)**와 TypeScript/Vite build가 통과했습니다. 신규 browser3개는 catalog/저장 응답 fixture와 실제 UI 요청을 검사한 계약시험이고, 기존 stack workflow는 격리 DB의 MCAP·큐·kernel을 실행합니다. 실제 workspace에서도 기존 stack/r2와 세 모듈/1·개정 dialog를 확인한 뒤 저장 없이 닫았습니다.
- 로컬 API·worker·UI를 정상 재시작했고 **64runs·71jobs·261artifacts를 포함한 모든 기존 업무 행·열과 관리 파일658개**를 백업과 비교해 보존했습니다. Schema9·FK오류0·CARLA0.6 ready/idle이며, 원격 worker/엔진 변경과 추가 simulator 실행은0건입니다.

**Seed:** 실행 중인 Shipping ELF와 matching debug symbol의 전체 configuration 함수를 조사하여, 새 LiDAR description을 적용할 때 reseed하지 않는 구현을 확인했습니다. 생성자의 인라인 seed 갱신과도 비교했습니다. 소스 patch는 정확한 적용·이중 적용 거절·역적용을 검사했지만 **미컴파일·미배포**이며 실제 센서 RNG 상태 readback은 없습니다. [바이너리 근거·수정 경계](carla-lidar-seed-diagnosis.md).

**급제동:** 두 기존 close-lead의160개 구간을 재계산했습니다. 장애물은 정지거리 밖에 있었지만 마지막 지면 bin 소실 때문에 최대 제동이 발생했고, dense에서는 corridor 경계의 점 하나로 잠깐 제동을 해제하는 현상도 확인했습니다. 최원점 하나를 연속 관측 공간으로 취급하거나 coverage 기준을 완화해서 통과시키지 않습니다. [전체 구간 근거](validation/carla-close-lead-policy-diagnosis-2026-09-13.json) · [별도 v2 관측·정지 계약 제안](carla-observed-horizon-design.md). 새 kernel/policy는 이 조사 당시 미구현이었으며 이후 구현·실제0.7 결과는 위에 구분했습니다. 기존0.6 주행 결과와 기준은 그대로입니다.

다음은 별도 엔진 build의 seed 적용 인수와, 독립 공간·시간 반례를 먼저 고정한 versioned 관측/정지 stack 구현입니다. 팀 운영·정밀 센서·실차 로그·지도·Neural/HIL과 전체 상용 gate도 계속 열려 있습니다.

## CARLA 0.6 · 선택형 LiDAR 설정과 실제 비교

기존 `carla-lidar-ego/1`과 새 `carla-lidar-ego-dense/1`을 UI·API·SDK·CLI에서 선택합니다. 새 recipe는 명목 sampling만32,000→128,000광선/s로 변경하며 mount·채널·FOV·노이즈·dropout·관측/정책/제어·판정 기준은 그대로입니다. 원래 profile/context/hash를 보존하고, 새 실행은0.6.0으로 고정합니다. [설정·사용법](carla-sensor-profiles.md) · [실제 인수 근거](validation/carla-sensor-profiles-2026-09-13.json).

| 실제4초 조건 | baseline | dense | 해석 |
| --- | --- | --- | --- |
| 2m/s · 이동 선행차 · gap20m | jerk11.362139 · failed | jerk1.285987 · passed | baseline관측 부족 정지1구간→dense0구간; 명목 설정만 바꾼 한 조건의 효과 |
| 2m/s · 정지 선행차 · gap8m | 감속7.965472·jerk152.474544 · failed | 감속8.948179·jerk176.453677 · failed | 새 설정의 급제동 수치가 더 큼; 전반적인 주행 품질 개선으로 일반화 불가 |

실제 엔진 실행은 cruise의요청seed42/43/44×2profile=6건, close-lead seed42×2profile=2건으로 **총8건**입니다. 각80명령/81관측을 원본 MCAP/XYZI/XYZ·native journal·차량 응답과 보존했습니다. **Cruise의 세 seed는 profile별 원본점군·궤적이 정확히 같으므로 독립 stochastic trial3건으로 계산하지 않습니다.** 요청 seed의 detached blueprint readback과 실제 sensor RNG 적용을 구분합니다. 공식 서버 표시commit1ae5356의 LiDAR 경로에서 description의 noise_seed를 읽은 후 RNG를 다시 seed하지 않는 코드를 확인했으나, 실행 바이너리의 수정 여부·RNG 내부 상태는 미검증입니다. UI에도 seed 적용 미확인을 표시합니다.

Cruise wall은 baseline약28.2–28.3초, dense약35.9–37.0초입니다. 실시간·fleet처리량·보정 센서 정확도 증거가 아닙니다. 새0.6 reader로 기존0.5의 GT/live완료와 after-ACK부분 archive3개를 실제 원격 조회·회수하여 이전 bytes/hash가 정확히 같음을 확인했습니다. 이 조회는 추가 simulation이 아니며, 새로운 버전간 장애 주입 회수 인수로 세지 않습니다.

배포 source82개 hash와 기존 store를 유지했고, 각 실행 뒤 원래23actor·world settings/weather·소유 actor0을 별도로 확인했습니다. 원래56runs·63jobs·189artifacts를 포함한 업무 행·열과 관리 파일586개를 보존했습니다. 최종 DB는64runs·71jobs·261artifacts·11recordings/schema9, FK오류0·active job0·CARLA idle/ready입니다. 전체 Python1599개(경고2개)가222.94초에 통과했습니다. 관련 browser 시험·build 및 실제UI에서 dense설정·80/81분모·전체점군/재생을 확인했으며, mocked/browser계약과 실제engine8건을 구분합니다.

**다음 품질 과제:** 실제 센서 RNG seed 적용 확인·수정, 가까운 장애물의 관측 한계와 급제동 응답. 새 profile 지원을 상용 출시·물리 보정·차량 안전성 통과로 표시하지 않습니다. 아래0.5이하 숫자는 각 당시의 인수 기록입니다.

## CARLA checkpoint 성능 개선 · 실제 인수·일반 worker 적용

같은 host/build/map·seed42·4초 조건에서 중복 복사·MCAP 직렬화·checkpoint blob 저장을 줄였습니다. 공개 adapter/wire는0.5.0을 유지하며 배포 source `checkpoint-performance-0.5.0-20260913`의82개 파일은 별도 archive SHA로 고정합니다. [구현·호환성·프로파일링 방법](carla-performance.md) · [실제 인수 기록](validation/carla-checkpoint-performance-2026-09-13.json).

| 실제 모드 | 이전 source D 계측 wall | 개선 source 계측 wall | 일반 큐의 최종 실행 시간 | 판정 |
| --- | ---: | ---: | ---: | --- |
| Live LiDAR stack | 55.214초 | 28.362초 | 28.254초 | failed · jerk11.362139 |
| 기본 GT 제어 | 19.414초 | 13.443초 | 13.180초 | passed |

계측 비교는 각각 한 번의 같은 조건 시험이며 일반 큐의 시간과는 측정 경계가 다릅니다. 두 모드의 전체81개 궤적과 지표, live 원본/소비 점군 bytes와80개 판단·제어가 baseline과 정확히 같습니다. checkpoint430/351회와 journal913/1004회도 유지했습니다. 실시간·fleet 처리량이나 센서 정확도를 입증한 결과는 아닙니다.

- 이번 인수의 실제 엔진 실행은 **계측5건 + 새 저장 형식 after-ACK SIGKILL1건 + 일반 큐2건 =8건**입니다. 새 부분 checkpoint의 원본 SQLite·CAS bytes를 먼저 보존하고 구 source D reader의503·DB 불변을 확인한 뒤, 새 reader가 같은 공개 payload를 복원했습니다. 전체80구간 중 완료7·실패1·미시도72, ACK8·다음 frame7·physics8과 빈 metrics/trajectory를 유지했습니다. 회수 중 시뮬레이션 POST는0회입니다. 명시적 정리 후 원래23개 actor·world settings/weather와 소유 actor0을 확인했습니다. 원본 archive의 cleanup unknown은 그대로입니다.
- 현재 일반 Linux worker는 계측/오류 주입 없는 새 source이며 기존 CARLA 서버·store UUID를 유지합니다. 일반 큐의 GT `fd7f5f7e`와 live `3afd9887`을 실제 UI에서 선택해 provenance·판정·재생을 확인했습니다. Live 마지막 구간의789개 전체 점군과 SHA,80ACK/80next/81관측도 표시됐습니다. 브라우저 파일 저장 인수와 API/SDK 원본 bytes 검증은 구분합니다.
- 변경 전 **54runs·61jobs·170artifacts를 포함한 모든 원래 업무 행·열 및 관리 파일567개**를 보존했습니다. 최종 관측에서56runs·63jobs·189artifacts·11recordings/schema9, FK 오류0, 활성 작업0, CARLA idle/ready, API/UI HTTP200입니다.
- 새 source의 **관련 Python446개**가72.48초에 통과했고 경고2개는 남습니다. 이는 전체 native adapter와 core/SDK 회수 관련 범위이며 아래 과거 전체1460개 결과와 합산하지 않습니다. 별도의 계측 wrapper 자체 검사는 작성한 fake world·실제 임시 SQLite에서 결과/전체 artifact bytes 불변을 확인했습니다. 인수 보조 프로그램의 초기 PID 관측 경합과 다운로드 폴더 누락은 원본 기록을 남기고 동일 실행 조사로 해결했으며 새 시뮬레이션으로 대체하지 않았습니다.

**다음 품질 과제는 live 주행의 관측 부족 정지와 jerk 실패 원인, 초기 속도 부여 후 잔여 차량 진동입니다.** 추가 ODD·학습 인지 제어·실측 상관성·분산/팀 운영과 전체 상용 release gate는 계속 열려 있습니다. 아래 항목의 source·수치·건수는 각 인수 시점의 역사입니다.

## 실행 엔진·시나리오 식별 개선

실제 workspace에서 `CARLA live…`라는 이름의 시나리오를 Reference 엔진으로 실행한 passed 결과와, 같은 이름·revision을 가진 서로 다른 시나리오 두 개를 확인했습니다. 목록·검색·선택 결과에 저장된 adapter/version/fidelity를 표시하고, 제출 요약에 engine·controller·정확한 scenario ID/revision·seed·live stack/rig 개정을 표시합니다. 전역 sidebar는 실행별 엔진 선택을 안내하며 특정 엔진이 항상 활성이라고 표시하지 않습니다. 엔진·제어 모드 변경 시 이전 live 입력을 지우고, adapter 또는 고정 revision 조회에 실패하면 제출을 막습니다.

관련 Chrome12개, 이후 마지막 engine-selection3개, scenario-ID 후속을 포함한 engine-selection/workspace6개가 각각 통과했습니다. 서로 겹치는 실행이며 전체 suite 통과 수로 합산하지 않습니다. TypeScript/Vite build도 통과했습니다. 별도로 실제 UI의 Reference 결과 `6ecf70b2`, CARLA 결과 `2b95ee58`, 동일 이름의 두 scenario ID 전환, GT/live 설명 및 미설정 live 제출 차단을 확인했습니다. 이 UI 확인에서 새 시뮬레이션은 제출하지 않았습니다. [검증 기록](validation/engine-selection-2026-09-13.json).

## CARLA adapter 0.5 · 실제 원격 결과 영속 저장·회수 인수 완료

- 원격 실행 ID·store UUID·요청/입력/context hash를 고정하고, 실행 중 commit된 진단과 봉인된 최종 archive를 저장합니다. UI/API/SDK/CLI의 회수 작업은 같은 큐의 별도 `recovery` kind이며 **시뮬레이션을 다시 실행하지 않습니다.** 원래 실패 Job·Run은 그대로 두고 원격 실행 ID와 archive hash를 연결한 별도 Run을 게시합니다. 회수 결과는 원래 source job의 제출 순서를 따르므로 이전 시도의 늦은 회수가 더 새로운 실행 근거를 덮지 않습니다. [복구 계약·사용법](carla-recovery.md) · [실제 인수 근거](validation/carla-durable-recovery-2026-09-13.json).
- 실제 Linux **CARLA build1ae5356 · Town10HD_Opt · RTX 2070 SUPER**에서 **독립 native 시험10건(정상2·프로세스 종료8) + 플랫폼 회수 시험2건 + 최종 정상 launcher 실행1건 = 실제 엔진 실행13건**을 구분해 검증했습니다. 조회·회수·동일 키 재요청은 추가 엔진 실행으로 세지 않습니다. Native10건 중 봉인된 complete archive는3건, interrupted partial은7건이며, 실제 종료 경계에는 startup·명령 전/ACK 후·physics/원본 sensor 수집·legacy capture·cleanup·최종 seal 후가 포함됩니다. 남은 명령을 이어 실행하지 않았습니다. 초기 after-ACK 시험의 source A/B 귀속 불일치는 인수 JSON에 그대로 남기며 모든 시험을 최종 source D로 재분류하지 않습니다.

| 실제 플랫폼 인수 | 원래 실행 | 별도 회수 결과 | 확인한 범위 |
| --- | --- | --- | --- |
| 완전한 응답을 EOF까지 읽은 직후 오류 주입 | 로컬 failed/error·원격 completed | 새 completed Run, driving verdict **failed** | 원래 Job/Run/이벤트/파일 불변, 버린 응답과 회수 archive의 bytes 동일, 동일 키·lineage·SHA·export, 재실행0 |
| 실제 native worker가 ACK 후 종료 | 로컬 failed/error·원격 interrupted/partial | 새 error Run, **diagnostics_only** | 원격 commit prefix의 native 진단8개+core manifest, metrics/trajectory 빈 상태, 전체80tick 분모·완료7/실패1/미시도72, 원래 근거 불변·재실행0 |
| 최종 source D·정상 launcher/background worker | 정상 기본 GT 제어 completed | 회수 없이 원래 Run, verdict **passed** | 실제4초·81관측·RGB/LiDAR 각3개, 정상 큐→원격 저장→Run/artifact 경로 |

- 정상 live와 완전 응답 회수의 저장 지표는 감속 **0.606410m/s²**, jerk **11.362139m/s³**로 변경하지 않았으며 jerk10 기준 때문에 **failed**입니다. 최종 기본 GT 실행은 감속 **0.051685m/s²**, jerk **1.169115m/s³**로 passed입니다. 기본 GT 제어는 캡처 센서를 입력으로 쓰지 않습니다. 영속 저장·회수의 통과와 센서 기반 주행 성능의 통과를 구분합니다. 4초 simulation의 실제 wall time은 live 약55초, 기본 GT 약19.65초이며 realtime 인수가 아닙니다.
- **Schema8→9** 이관과 인수 후 비교에서 기존 **48runs·55jobs·139artifacts를 포함한 모든 원래 업무 행·열 및 관리 파일534개의 SHA**를 보존했습니다. 현재 **53runs·60jobs·169artifacts·11recordings**, schema9이고 foreign_key_check는 비어 있습니다. 현재 자원 소유권·worker heartbeat는 운영 상태로 구분합니다. 원본 archive의 cleanup `unknown`은 사후 정리 때문에 수정하지 않습니다.
- 최종 상태 관측(**2026-09-13 11:02:33 UTC**)에서 정상 source D의82개 파일 hash를 확인했습니다. 원래 actor23개의 ID/종류를 모두 보존했고 소유한 잔여 actor는0, world settings/weather는 원래 값, 비동기·fixed delta None, 원격 quarantine 해제·로컬 CARLA resource idle·active job0·API/UI HTTP200을 확인했습니다. 이는 별도 world 재관측과 명시적 정리 확인의 근거이며, archive의 cleanup ACK만으로 추론한 결론이 아닙니다. [최종 상태·보존 보고](validation/carla-durable-recovery-2026-09-13.json).
- **전체 Python1460개**가211.85초에 통과했고 경고2개는 남습니다. 이후 source D의 원래 actor 보존 guard3개를 추가한 **관련28개**가1.53초에 통과했습니다. 이를1463개 전체 통과로 합산하지 않습니다. TypeScript/Vite build와 **전체 Chrome32개**가 통과했으며, Chrome 회수 계약시험은 authored lifecycle·native fake-world artifact를 사용합니다. 두 실제 플랫폼 회수와 최종 source D의 정상 GT 실행도 별도로 브라우저에서 조사했습니다. 브라우저 파일 저장은 download-event 관측이 실패한 경우 미검증으로 남기고, API/SDK의 원본 bytes·SHA 다운로드 검증과 구분합니다.
- **상용 목표는 열려 있습니다.** 이번 인수는 제한된 프로세스 종료·응답 유실 조건이며 host power/disk loss, 임의 네트워크 장애, 분산 소유권, 조직/프로젝트 권한, 대용량·장시간 운영을 입증하지 않습니다. 물리적으로 불안정한 startup timeout, 초기 속도 주입 후 잔여 진동·정밀 센서/차량 상관성, 학습 인지 제어와 실제 고객 pilot도 후속입니다. 아래0.4.1 이하 기록은 당시 source·지표·판정을 보존한 역사입니다.

## 앞선 CARLA adapter 0.4.1 · 차량 설정 유실 수정·응답 근거

- 실제 두 차량 시험에서 요청한 tire friction scale **0.8이 먼저 생성한 ego에서는3.5로 되돌아가는 설정 유실**을 확인했습니다. 기본 GT·선택적 live 양쪽의 순서를 **모든 차량 생성 → 두 차량 physics 설정 → rest gate → 두 차량 readback 검사 → 초기 속도 부여**로 수정했습니다. 정착 후 네 wheel의 값이 요청한 float32 표현과 일치하지 않거나 읽을 수 없으면 초기 속도를 부여하기 전에 실패하며, 다른 설정으로 조용히 재적용하지 않습니다. 이 값은 tire configuration scale이며 실측 노면 마찰계수가 아닙니다.
- Live의 새 `carla-vehicle-response.json`은 마지막 rest snapshot·비동기 초기 속도 요청·81개 측정 snapshot의 원래 XYZ 속도/가속도/자세와 frame으로 구간을 연결합니다. 요청 control·동기 ACK·다음 frame readback은 실제 움직임과 구분합니다. UI는 원본 SHA와 실행 문맥을 검증해 signed horizontal-heading speed·vz·3D speed·가속도와 선택적 wheel/RPM/gear를 표시합니다. unavailable/nonfinite는 gap과 상태로 유지합니다. 기본 GT 경로는 physics readback gate를 `carla-startup.json`의 context에 보존하며 live response artifact를 생성했다고 표시하지 않습니다. [응답 근거의 의미](carla-vehicle-response.md).
- 같은 Linux **build1ae5356 · Town10HD_Opt · seed42 · 4초 · 초기2m/s · gap20m**에서 저장한 stack r1/r2로 실제 플랫폼 실행 두 건을 완료했습니다. 각 **80명령/81관측**, 양쪽 차량 tire scale **0.800000011920929 일치**, 원본 MCAP에서 **각80tick 재계산 일치**를 확인했습니다. 이 두 live 실행의 source archive는72개 파일이며 SHA는 `86d9b166b81a4624933301e8102ad3ed7a0c94c1339d4bf975c95bb1b5678d25`입니다. [실제 인수 근거](validation/carla-vehicle-response-2026-09-13.json).

| 실제 0.4.1 개정 | 판단 | 이동 거리 m | 최대 감속 m/s² | 최대 jerk m/s³ | 실행 / 판정 |
| --- | --- | ---: | ---: | ---: | --- |
| r1 · headway1.5초 | pilot_cruise79 · unknown_stop1 | 7.724732 | 0.606410 | 11.362139 | completed / failed |
| r2 · headway8초 | unknown_stop80 | 0.564826 | 8.633477 | 170.319628 | completed / failed |

- 별도의 **기본 GT 제어**도 최종 source-b에서 lane-follow4m/s·gap10m·seed42·4초로 인수했습니다. 81개 physics 표본·RGB3장·LiDAR3개·MCAP와 startup context의 양쪽 physics `matched`를 보존했습니다. 저장 metric은 감속 **0.051685m/s²**, jerk **1.169115m/s³**, 거리 **15.519266m**이며 이 기본 실행의 verdict는 **passed**입니다. 같은 조건의 첫 source-a 실행도 별도 run으로 남습니다. GT 제어가 캡처 센서를 사용한 것은 아니며 live 두 실행의 실패 판정을 대체하지 않습니다.
- **판정 기준은 그대로이며 두 live verdict 모두 failed입니다.** r1의 unknown-stop 명령은 **sequence63(3.15초)**, 다음 관측64(3.20초)에서 최대 jerk가 발생했습니다. 이전0.4 r1의 sequence79와 구분합니다. 관측 범위가 부족하면 정지하는 정책을 유지했고, 측정 표본을 버리거나 jerk10/감속6의 기준을 낮추지 않았습니다. r2는 요구 정지거리18m를 지원하지 못해 전 구간 정지 명령을 냅니다. 설정 유실 수정과 전체 차량 성능 통과는 다른 결과입니다.
- 원래0.4.0 환경에서 **baseline·rest40·native_physics·reapply_native·configure_after_spawn**의 독립 진단5조건도 실행했습니다. 각80명령/81관측·strict JSON·source hash를 확인했지만 플랫폼 job/검증 판정으로 등록한 시험이 아닙니다. 추가 rest는 기존 진동을 없애지 못했고, 생성 후 설정 조건은 양쪽 scale0.8을 유지하며 ego 진동을 줄였습니다. 기존4조건의 unknown_stop은 sequence79, 변경 조건은63이므로 전체 최대 jerk 차이를 타이어 동역학 하나의 효과로 단정하지 않습니다. 초기 속도 주입 이후 residual oscillation·gear/wheel 상태·정밀 차량 상관성은 후속입니다.
- 배포 source `vehicle-response-0.4.1-20260913-b`는 기본 GT의 정착 후 추가 snapshot RPC 오류에도 이미 확보한 startup artifact를 보존하도록 `runner.py` 한 파일의 오류 경로를 수정했습니다. 72개 파일의 archive SHA는 `0d5d74aa8f22420abd918bc1e37f53d2825fefc0954a8b85f1232e94e2e809ec`입니다. 위 실제 live 인수의 bridge·core bytes는 같으며, 이를 새 live 실행 두 건으로 중복 집계하지 않습니다.
- 배포 전 backup과 비교해 기존 **43runs/48jobs를 포함한 모든 업무 행·열과 관리 파일331개의 SHA**를 보존했습니다. 당시 DB는 **47runs·54jobs·138artifacts·11recordings·6stack replays/336ticks**, schema8이며 foreign_key_check는 비어 있습니다. 엔진 예약과 worker heartbeat는 운영 상태로 별도 취급합니다. 배포된72개 source의 SHA와 실행 후 actor0·원래 비동기 world·fixed delta None을 확인했습니다.
- **전체 Python1355개**가209.32초에 통과했으며 알려진 경고2개는 남습니다. 마지막 오류 근거 보존 수정 뒤에는 새 시험1개를 포함한 **native/legacy 관련22개**가9.88초에 통과했습니다. **전체 Chrome27개**는약2.5분에 통과했고, 그 뒤 음수 축 레이블 잘림 수정·새 좌표 regression을 포함한 **response 관련6개**가41.8초에 통과했습니다. 최종 TypeScript/Vite build도3.12초에 통과했습니다. 전체 실행과 이후 관련 회귀의 범위를 합산해 새로운 전체 시험 수로 표시하지 않습니다. 실제 서버 인수와 authored/mock 계약시험도 구분합니다.
- 기존0.4/0.3 실행의 원본 bytes·지표·판정을 새 값으로 대체하지 않았습니다. 당시 Schema8과 adapter0.4.1을 사용했으며 원격 프로세스 사망 후 영속 결과 회수는 후속 과제였습니다. 이후0.5의 구현·실제 인수는 위에 구분했습니다. 실제 불안정 startup timeout·분산 소유권·실측 센서/차량 상관성·상용 release gate는 계속 열려 있습니다.

## 앞선 CARLA adapter 0.4 실제 센서→공통 스택→제어 연결

- UI/API/SDK/CLI에서 저장한 stack·rig 개정을 선택하여 **실제 LiDAR·simulator ego/map → 공통 StackSession → 동기 batch 응답 → 다음 physics frame**을 연결했습니다. 같은 `carla` 큐 자원을 사용하며 기본 GT 제어와 선택적 센서 제어를 구분합니다. 고정 profile은 20Hz·2–4초·roof LiDAR이며 RGB를 소비하거나 생성하지 않습니다. [사용법](carla-live-stack.md).
- 실제 Linux **build1ae5356 · Town10HD_Opt · seed42 · 4초 · 초기2m/s · gap20m**에서 headway1.5초/8초 개정 두 건을 완료했습니다. 각각 **80개 명령·성공 RPC ACK·다음 관측, 81개 physics/LiDAR/ego 관측**을 저장했습니다. 전체 원본 XYZI·MCAP·소비 XYZ·trace·manifest와 다운로드 크기/SHA를 검증했습니다. [기계 판독 가능한 실제 인수 근거](validation/carla-live-stack-2026-09-13.json).
- 각 원본 MCAP를 DB recording으로 가져오고 같은 rig·stack으로 **각80개, 총160개 tick을 재계산**했습니다. 정확한 packet·전체 소비 점군 bytes·계산시간을 제외한 관측/판단/명령이 모두 같습니다. 재생 명령은 `not_applied_open_loop`이며 차량에 다시 적용하지 않습니다.

| 실제 개정 | 판단 | 이동 거리 | 최대 감속 m/s² | 최대 jerk m/s³ | 실행 / 판정 |
| --- | --- | ---: | ---: | ---: | --- |
| r1 · headway1.5초 | pilot_cruise79 · unknown_stop1 | 7.762m | 2.040 | 58.743 | completed / failed |
| r2 · headway8초 | unknown_stop80 | 0.233m | 24.903 | 359.696 | completed / failed |

- **두 주행의 판정은 모두 failed입니다.** r1의 마지막 tick은 지면 관측 bin이 부족해 정지했고 그 이전에도 jerk36.203과 두 tick 속도 교번이 남습니다. r2는 요구 정지거리18m가 설정된15m 관측 구간의 전방 유효 범위를 넘는, 의도적인 비주행 설정입니다. 장애물 제동 성공이나 주행 품질 개선으로 표시하지 않습니다. 두 실행은 첫 입력만 같고 이후 센서 입력이 달라지므로 전체 지표 차이를 동일 입력 회귀나 통계로 해석하지 않습니다.
- 첫 실제 시도는 원격200 응답 뒤 좌표 바이트 검증에서 거절되어 error Run과 자원 격리를 남겼습니다. 검증기의 단순식이 signed zero를 다르게 처리하는 문제를 재현해 native·기록 재생과 같은 연산 순서로 수정했습니다. byte/SHA 검사는 유지했습니다. 원래 거절 응답은 보존되지 않았으므로 해당 원본 점의 사후 입증은 불가능합니다. 이전 dispatcher/worker 종료와 actor0·world 복원을 확인한 뒤 명시적으로 reconcile하고 새 시험 의도로 위 두 실행을 수행했습니다.
- 배포 전 backup과 비교해 기존 **40runs/43jobs 및 모든 원래 업무 행·열, 관리 파일150개의 SHA**를 보존했습니다. 당시 schema는8이며43runs/48jobs에는 새 실제 실행3건(오류1 포함)과 기록 재계산2건이 추가됐습니다. 시험 후 actor0·비동기 world·fixed_delta=None·같은 지도 SHA를 확인했습니다. 이전0.3 release와 최초0.4 source archive도 보존했습니다.
- 전체 **Python1300개 · Chrome22개 · TypeScript/Vite build**가 통과했습니다. native fake-world·전송/브라우저 계약시험과 위 실제 서버 실행을 구분합니다. 실제 화면에서 80/81 연결·전체834점·판단/요청/응답을 확인했습니다. 실행 전환 시 startup/live inspector의 중복 React key로 이전 DOM이 남던 문제를 수정하고, 실제 두 실행의 왕복 전환과 지연/실패 조회 회귀를 확인했습니다. [인수 자료](validation/carla-live-stack-2026-09-13.json).
- **정밀 센서/차량 상관성, 실제 startup timeout·프로세스 사망 복구, 원격 결과 영속 회수, 임의 rig/지도·추가 센서·학습 인지 제어·분산/팀 운영은 남습니다.** 초기 속도 부여 뒤 transient·제동 응답 문제도 해결 완료가 아닙니다. 상용 release gate는 계속 열려 있습니다.

## 앞선 CARLA adapter 0.3 초기화·실패 근거 단계의 인수 결과

- CARLA adapter를 **0.3.0**으로 구분하고 `carla-startup-stability/1` 정책을 추가했습니다. 고정 8 tick 대기 대신 ego/lead의 같은 world snapshot에서 속도·각속도·가속도·위치/회전 변화와 연속 안정 샘플을 검사합니다. 최대 100 tick의 준비 구간과 성공·실패 원본 trace를 `carla-startup.json`에 보존하며 측정 주행 표본을 잘라내지 않습니다.
- 실제 Linux CARLA server/client build **1ae5356**, `Town10HD_Opt`, seed42에서 lane-follow 요청 속도4/6m/s와 emergency-brake 요청 속도4m/s의 **4초 작업 3건을 완료**했습니다. 각 run은 실제 physics 궤적81개·RGB3장·LiDAR3개·MCAP·startup/capture/evidence manifest를 저장했습니다. 세 startup 원본의 크기·SHA가 해당 run의 artifact descriptor와 일치하는 것을 확인했습니다. [실제 인수 근거](validation/carla-startup-acceptance-2026-09-13.json).
- 배포 전 backup과 대조하여 기존 **37runs/40jobs를 포함한 업무 근거의 모든 원래 행·열**, 관리 파일 **120개 SHA**를 보존했습니다. schema는 **8 그대로**이며 새3runs/3jobs/30artifacts가 추가됐고 foreign_key_check는 비어 있습니다. 시험 뒤 actor0·비동기 world·fixed_delta_seconds=null·map SHA 보존을 확인했습니다. 당시0.3 Ready와 실제 workspace의 startup 그래프·23tick/streak5·실패 metric·원본 다운로드도 확인했습니다. worker heartbeat·현재 자원 예약 등 운영 상태는 이 불변 업무 근거 비교에서 구분합니다.
- **세 job/run의 status는 completed이며, 세 run의 verdict는 모두 failed입니다.** 완료는 요청 구간의 실행과 근거 저장을 뜻합니다. 아래 수치는 실제 저장 metric이며 주행 통과로 바꾸지 않았습니다.

| 실제 0.3 trial | 최대 감속 (m/s²) | 최대 jerk (m/s³) | 저장 판정 |
|---|---:|---:|---|
| lane-follow · 요청4m/s | 0.660 | 25.726 | failed · jerk 기준 초과 |
| lane-follow · 요청6m/s | 26.963 | 580.344 | failed · 감속·jerk 기준 초과 |
| emergency-brake · 요청4m/s | 26.273 | 446.259 | failed · 감속·jerk 기준 초과 |

- 세 run 모두 startup **23 tick(약1.15 simulation초), stable streak5**에서 rest criteria를 만족했습니다. 이는 **초기 목표 속도를 강제로 설정하기 전의 정착 판정**입니다. 이후 wheel/speed convergence 또는 측정 주행의 통과를 입증하지 않습니다. 4m/s의 속도에는 두 tick 주기의 교번이 남습니다. 6m/s의 큰 감속은 저장된 actor 상태와 기존 hazard/headway 정책에서 full-brake 분기를 재구성하여 조사했지만 **실제 요청/적용 control을 기록한 근거는 없으므로 인과관계의 확정이나 전체 transient 수정 완료를 주장하지 않습니다.**
- 실행이 실패해도 bounded diagnostic artifacts를 error run에 보존합니다. 원격 0.3 wire는 worker version·성공/실패 envelope·JSON·base64·artifact 수/개별/누적 byte 한도를 엄격히 검사하며, 불명 응답 뒤 reference 대체나 자동 재실행을 하지 않습니다. 실제 불안정 상태에서의 startup timeout·원격 중단·복구 인수는 아직 하지 않았습니다.
- Run explorer의 **CARLA startup diagnostics**는 선택 run·artifact ID·크기·SHA와 schema/policy/시나리오 개정·seed·adapter 문맥을 검증한 뒤 ego/lead vz·선속도·stable streak를 표시합니다. 원본 invalid/부분 관측은 gap으로 유지하고, 다른 run 선택·실패·늦은 응답에서 이전 trace를 숨깁니다. 검증한 원본 JSON을 다운로드합니다. 정착 판정과 측정 주행 판정을 화면에서도 구분합니다.
- 최종 전체 **Python1171개**, 실제 Chrome workflow **16개**, TypeScript/Vite production build가 통과했습니다. 이 중 startup UI의4개 workflow는 **직접 작성한 진단 fixture를 사용한 브라우저/전송 계약시험**이며, 실제 CARLA 인수는 위3개 run 자료와 구분합니다. 앞선 단계의 시험 수와 실행 이력은 아래에 그대로 보존합니다.
- **0.3 인수 당시 0.4 연결은 설계 단계였으며, 이후 구현과 실제 인수 결과는 위에 구분했습니다.** 동일 `carla` 자원, 고정 stack/rig 입력, 원본 LiDAR·ego MCAP, 센서 frame→shared kernel→요청/적용 명령→다음 physics frame을 연결하는 제안입니다. 기존 주변 actor ground-truth controller를 센서 기반 제어로 표시하지 않습니다. [0.4 설계 기록](carla-stack-design.md).

## 앞선 교체형 스택·기록 재생 단계의 인수 결과

- UI/API/SDK/CLI에 **Component stacks**를 추가했습니다. 관측·정책·제어 component를 version으로 선택하고 실제 validator가 해석한 전체 파라미터를 불변 개정으로 저장합니다. 과거 개정 조회·export는 현재 설치된 plugin의 기본값이나 가용성에 의해 바뀌지 않습니다.
- 정확한 MCAP ego ordinal과 최신 과거 LiDAR, rig/binding 개정을 고정하여 같은 영속 큐의 별도 `stack_replay` 작업으로 계산합니다. 전체 선택 tick의 완료·실패·미시도를 유지하며 원본 message hash·서로 다른 clock·선언 transform·소비한 전체 XYZ bytes·관측·정책·제어 명령을 연결합니다. 실제 빈 점군의 0-byte 근거와 입력 부재를 구분합니다.
- 기본 지면은 unknown이며 명시적인 지면 높이·출처 없이는 정지합니다. 관측 age와 ego/LiDAR 시간 차이를 따로 검사하고, 실제 관측된 지면 범위를 넘어서는 정지거리가 필요하면 정지합니다. 입력 누락·empty·stale·불명 좌표는 unknown-stop으로 남깁니다. 이는 제한된 평지 기하 알고리즘이며 물체 추적·학습 인지·빈 공간·차량 안전성의 증명이 아닙니다.
- 실제 workspace를 **schema 7→8**로 이관했습니다. 정상 종료·SQLite backup 후 기존 모든 열·행을 비교하여 실행 **37개**, 작업 **38개**(simulation29/perception6/evaluation3), recording6개·message488개, 실제 CARLA/YOLOX·dataset 평가 근거와 관리 파일 **106개**의 SHA가 보존됐습니다. queue sequence도 유지됐고 foreign_key_check는 비어 있습니다. 현재 코드의 API·worker를 재시작하고 기존 CARLA 연결 Ready를 확인했습니다. [이관 근거](validation/stacks-migration-2026-09-13.json).
- 빈 격리 환경과 실제 workspace HTTP API/별도 worker에서 `examples/stack_replay_workflow.py`를 실행했습니다. 독립 작성한 MCAP의 8 tick을 stack r1/r2로 각각 완료했습니다. 동일한 5,562점의 SHA와 ego 입력에서 headway1.5초는 throttle0.2/approach, 3초는 brake1/obstacle_stop으로 달라졌습니다. 과거 입력 없음·빈 점군·stale은 정지하며, 이전 개정 bytes·manifest·소비 XYZ 다운로드 SHA를 검증했습니다. **직접 작성한 입력의 open-loop 계산이며 출력 명령은 차량이나 다음 관측에 적용되지 않습니다.** [실행 근거](validation/stacks-local-2026-09-13.json).
- Python 전체 회귀1080개 실행에서1078개가 통과했고, 나머지2개의 과거 migration 시험은 예상 schema 숫자만7→8로 수정해 재통과했습니다. 이후 authoring normalization5개를 추가한 관련 stack93개도 통과해 총 **1085개 시험 범위**를 검증했습니다. 원본 입력 변조·source/clock/좌표·전체 분모·취소/worker kill·fenced publish·파일 rollback·schema 이관과 SDK를 포함합니다. 기존 Starlette/httpx·AnyIO deprecation 경고2개는 남습니다.
- TypeScript/Vite build와 전체 Chrome workflow **12개(약1.4분)**가 통과했습니다. 새 흐름은 stack 작성/개정→응답 유실 후 동일 키 복구→고정 기록 재생→점군·판단·명령→SHA 내보내기, 실패 시 오래된 근거 숨김과390px 화면을 확인합니다. 실제 workspace에서도 저장된5,562점과9.5m 표면 거리·r2 정지 명령의 표시를 확인했습니다.
- 현재 shared kernel은 **기록 재생에 연결**됐습니다. 기존 실제 CARLA RGB/LiDAR capture에는 새 replay가 요구하는 ego telemetry가 없으며 이를 만들어 연결하지 않았습니다. CARLA live sensor→shared kernel→실제 actuator 적용·부분 trace·실패 정리는 다음 기능입니다. [사용법](stacks.md) · [API·교체 계약](stacks-contract.md).

## 앞선 실제 원격 CARLA 0.2 연결 인수·독립 진단

- 사용자의 Linux RTX 2070 SUPER 8GB PC에 SSH로 연결했습니다. 기존 CARLA runtime에 동봉된 wheel을 독립 worker venv에 설치해 server/client build **1ae5356** 일치를 확인했습니다. 공개 PyPI0.9.16 wheel은 build 문자열이 달라 실제 preflight에서 거절됐고 검사를 우회하지 않았습니다.
- 실제 `Town10HD_Opt`에서 seed42·4초 주행 2건을 플랫폼 영속 큐로 완료했습니다. 요청 속도4/6m/s에서 이동 거리12.711/19.382m, 각81개 궤적 표본·RGB3장·LiDAR3개·MCAP와 manifest를 저장했습니다. 다운로드 SHA, 원본 recording lineage, 실제 decoder의 영상·점군을 확인했습니다. 시험 후 생성 actor 정리·원래 비동기 world 설정·지도 hash 보존을 확인했습니다.
- **두 주행의 verdict는 failed입니다.** 첫0.05초의 약25m/s² 감속으로 감속·jerk 기준을 넘었습니다. 초기 속도 설정/제어/physics 전환이 의심되지만 근본 원인은 미확정이며 표본·실패 판정을 보존했습니다.
- 이 CARLA RGB ordinal0/2/4를 기존 YOLOX-S r1에 실제 CPU 추론해3/4/3개 예측을 저장하고 브라우저 overlay를 확인했습니다. 인지 정확도 평가나 센서 기반 폐루프 제어는 아닙니다. 연결 UI의 실제 Ready도 확인했습니다.
- CARLA adapter 독립 계약시험82개와 저장된 endpoint/token 분리 launcher 시험15개가 통과했습니다. 실서버 결과는 위 actual run 근거이며 mock 계약시험과 구분합니다. [실제 근거](validation/carla-remote-2026-09-13.json) · [연결 운영과 한계](carla-connection.md).
- 후속 독립 진단3회에서 ego는8 settle tick 뒤에도 수직속도−3.92m/s로 낙하 중이었으며, XYZ speed의 수직 성분이 최초 미세 brake 요청에 영향을 줌을 관측했습니다. 계측 stock8의 peak 감속6.628m/s²·jerk132.567m/s³가 settle40에서0.07659/2.38615로 줄었습니다. 기존 platform 실행의 약25m/s² 정확한 크기는 재현하지 못했으므로 완전한 원인 입증이나 수정 완료로 제시하지 않습니다. 이 진단 단계에서는 실제 runner·기존 판정을 변경하지 않았고 actor0·world 설정 복원을 확인했습니다. 이후 bounded stability gate·초기화 trace를 별도0.3 adapter로 구현한 결과는 위에 구분했습니다. [진단 근거](validation/carla-startup-diagnostics-2026-09-13.json).

## 이번 데이터셋·검출 평가 단계의 인수 결과

- 모델 실행과 독립적인 원본 이미지 캡처, bbox 라벨·taxonomy·split·검토 선언의 불변 개정, 같은 큐의 `evaluation` 작업, 실제 COCO bbox 계산·PR·이미지별 TP/FP/FN/crowd 진단을 UI/API/SDK/CLI에 연결했습니다. 미검토 빈 라벨과 검토자가 선언한 negative를 구분합니다. Review는 사용자 선언이며 인증된 독립 검토 승인이 아닙니다.
- 실제 workspace를 **schema 6→7**로 이관했습니다. 정상 종료·SQLite backup 후 모든 기존 열·행을 비교했습니다. 시나리오 12개·실행 35개·작업 30개(simulation27/perception3)·기록 3개/메시지 470개·추론 frame15개·모델 개정·기존 시험 근거와 관리 파일 **59개**가 그대로 남고 foreign_key_check는 비어 있습니다. [이관 근거](validation/datasets-migration-2026-09-13.json).
- `examples/dataset_evaluation_workflow.py`를 빈 격리 환경과 실제 workspace HTTP API/별도 worker에서 각각 실행했습니다. 추론 전에 원본 이미지 6개를 고정하고 원래 그림 생성 규칙에서 라벨을 독립적으로 작성했습니다. 첫 개정은 1개가 미검토이므로 전체 6개 분모·미검토1을 유지하고 모든 aggregate와 class AP/PR을 보류합니다. 다음 개정은 검토 선언을 완료했습니다.
- 실제 ONNX rule A/B의 저장 예측을 실제 `pycocotools2.0.11`로 계산했습니다. IoU0.5에서 A는 TP4/FP0/FN0, B는 TP2/FP0/FN2이며 AP는 각각 1.0/0.504950495입니다. 원래 score floor0.6·NMS0.45·cap100과 COCO/진단 정책을 고정합니다. 이 수치는 직접 작성한 pixel rule/이미지/라벨의 pipeline 산술 검증이며 실차·학습 detector 정확도가 아닙니다. 모든 PNG·dataset/evaluation export SHA, 이전 dataset r1/평가 bytes 보존도 확인했습니다. [실행 환경·입력·정책·결과](validation/datasets-local-2026-09-13.json).
- 최종 Python **935개(77.34초)**가 통과했습니다. 기존 simulation·CARLA mock·recording·sensor·suite·ONNX·SDK를 포함하고, 실제 COCO child 산술/취소와 평가 worker kill·lease·원자적 게시·전체 분모·source mismatch·native report·schema6→7/실패 rollback을 검증했습니다. 기존 Starlette/httpx·AnyIO deprecation 경고 2개는 남습니다.
- 독립 검토에서 정상 라벨 개정/worker 완료와 GET가 겹치면 서로 다른 DB 시점이 섞여 거짓 무결성409를 반환하는 문제를 재현했습니다. Compound read에 명시적 SQLite snapshot transaction을 적용했고, 정상 revision API 호출과 실제 worker 완료를 SELECT 사이에 끼워 넣는 회귀시험으로 수정했습니다. AP와 class AP의 평균·정답 없는 class의 null 일관성도 검증합니다.
- TypeScript/Vite production build와 전체 실제 Chrome 브라우저 workflow **11개(약1.1분)**가 통과했습니다. 새 dataset 흐름은 원본 이미지 작성→pointer/정밀 box·검토 초기화→negative/crowd→실제 ONNX/COCO·PR→불변 개정/평가·SHA 다운로드, 제출 응답 유실 후 같은 키 복구, PNG 실패/재조회와 다른 선택 실패 시 오래된 근거 숨김, keyboard·390px 화면을 확인했습니다. 실제 workspace 브라우저에서도 원본 PNG 위의 라벨, 6개 전체 분모, PR 곡선, 특정 ordinal의 FN 근거를 직접 확인했습니다.
- [라벨·평가 사용법](datasets.md), [정확한 API](datasets-contract.md), [공식 COCO 계산과 권리 출처](detection-evaluation-notes.md), 선택형 adapter·SDK/CLI·실행 예제를 추가했습니다. CARLA 실서버·실차 pilot·학습/평가 독립성·인지→planning→control·분산 실행·검토 권한과 상용 release gate는 여전히 열려 있습니다.

## 앞선 인지 추론 단계의 인수 결과

- 모델 artifact 업로드·불변 개정과 명시적 전후처리 profile, 같은 영속 큐의 simulation/perception 분리, 실제 CPU ONNX 추론·프레임별 running/완료/실패·부분 결과 보존·검출 overlay·저장된 예측 비교를 UI/API/SDK/CLI에 연결했습니다. 실제 인지 추론이 주행 run/verdict나 요구사항 통계로 기록되지 않습니다.
- 실제 workspace를 **schema 5→6**으로 이관했습니다. 정상 종료 후 SQLite backup과 이관 DB의 모든 기존 열·행을 비교했습니다. 기존 시나리오 12개·실행 35개·작업 27개·기록 2개/메시지 464개·시험/rig/binding 근거와 관리 파일 **39개**가 유지됐으며 foreign_key_check는 비어 있습니다. [이관 근거](validation/perception-migration-2026-09-13.json).
- `examples/perception_workflow.py`가 실제 HTTP API에 모델 r1/r2와 6개 입력씩을 제출했고 별도 worker의 실제 ONNX graph가 처리했습니다. 검출 수는 r1 `[1,1,0,1,1,0]`, r2 `[1,0,0,1,0,0]`이며 PNG bytes·고정 model r1·작업/비교 manifest의 SHA와 다운로드를 확인했습니다. 직접 작성한 pixel rule과 이미지이므로 학습된 semantic detector 시험이나 정확도 측정이 아닙니다.
- 공식 OpenCV Zoo의 SHA가 일치하는 **실제 학습된 YOLOX-S FP32**도 같은 worker/API로 입력 3개를 완료했습니다. 직접 작성한 이미지에서 검출은 각각 0개였습니다. native CPU inference는 약 78.1/51.0/48.2ms였으며 모델 초기화·queue·전후처리·HTTP를 제외한 이 입력의 측정입니다. 실차 정확도나 throughput 측정이 아닙니다. [실행 환경·파일 해시·저장 결과](validation/perception-local-2026-09-13.json), [모델 출처와 RGB 규약](perception-model-notes.md).
- Python 시험은 전체 **727개 통과** 후 독립 검토로 찾은 송신 차단 문제를 수정해 blocked-pipe timeout/취소/lease 시험 3개를 포함한 adapter 44개를 재통과했습니다. model fetch의 short-write와 저장 파일 SHA 검사를 추가해 5개도 통과했습니다. 현재 **735개**의 시험 범위를 검증했습니다. 설치된 Starlette/httpx·AnyIO의 기존 deprecation 경고 2개가 남습니다.
- 모델 다운로드의 잘못된 파일 게시, migration commit 실패 후 의도치 않은 확정, 프레임 시도 상태의 오분류, 큰 이미지 송신 중 취소 불가 문제를 독립 검토/실패 주입으로 발견해 수정했습니다. 지원 profile만 실행하며 unknown 형식·출력 불일치는 실패로 남깁니다.
- TypeScript와 production build, 전체 브라우저 workflow **10개(54.3초)**가 통과했습니다. 새 인지 흐름을 기존 시나리오·큐·recording·센서·suite 회귀와 함께 실행했습니다. 실제 브라우저에서 모델 등록·ordinal 선택·응답 유실 후 중복 없는 복구·입력별 bbox/score 변화·검출 0개·모델 개정·비교 SHA·실패 시 오래된 이미지 제거·390px 넘침 방지를 확인했습니다. 실제 workspace도 별도로 열어 정확한 PNG 위의 검출 좌표를 눈으로 확인했습니다.
- [사용법](perception.md)과 선택형 adapter·SDK 예제·고정 model fetch·원본 license notices를 추가했습니다. 모델 가중치와 COCO 이미지는 Git에 포함하지 않습니다. 당시 정답 라벨 평가는 후속 범위였으며 현재 위의 데이터셋 단계에 구현했습니다. CARLA 실서버, perception→planning→control, GPU 실행과 상용 출시 gate는 미완료입니다.

## 앞선 모듈의 인수 기록

- 로컬 API와 UI 실행, 실제 계산한 초기 시나리오 8개와 결과 8개. 초기 통과 5개/실패 3개이며 임의 성과 수치가 아닙니다.
- 브라우저에서 생성 → 실행 → 3D 재생 → 개정 저장 → 요구사항 생성 → JSON 근거 다운로드까지 확인했습니다.
- 브라우저 회귀 시험 **9개 통과**: 생성→큐 제출→별도 worker 실행→근거 저장, 9개 화면·390 px 폭·연결 확인, 실패한 실행 조회, MCAP 업로드→정밀 탐색→신호/opaque 확인→원본 다운로드, 시각 0의 중복 메시지 150개 전체 페이지 탐색, 제출 응답 유실 후 동일 작업 재조회와 실제 결과 연결을 검사했습니다. Test suites에서는 작성→campaign 제출 응답 유실·동일 요청 복구→baseline 저장→개정→조건 비교→비교 근거 다운로드·해시 검증과 다른 비교 조회 실패 시 이전 근거 숨김을 확인했습니다. 별도 키보드 시험으로 연속 입력·Tab 순환·Escape·원래 버튼 포커스 복귀를 검사했습니다. 센서 시험은 표준 MCAP 업로드→실제 이미지 픽셀/XYZ→rig/binding 작성→고정 개정·좌표 변환→stale 숨김→정확한 ordinal/원본 hash 다운로드→잘못된 조회에서 이전 이미지 숨김을 확인했습니다. 기존 8개 workflow 통과 후 새 센서 시험의 오류 응답 기대값을 실제 API 계약에 맞춰 수정하여 별도로 재통과했습니다.
- 로컬 Python 계약/동작 시험 **536개 통과**: 실행/근거/CARLA, MCAP framing·CRC·압축 제한, DB migration, 정밀 시각 탐색, 파일 rollback, SDK 전송·무결성, 작업 소유권·취소·lease·근거 선택을 검사했습니다. Suite의 입력·판정 기준 고정, 일괄 제출 원자성, 개정 충돌, 전체 분모 보존, 취소·중단·손상 근거, 엄격한 비교 조건과 저장된 baseline/비교 파일의 무결성을 추가 검증했습니다. 센서 추가 111개 시험은 bounded ROS2/Foxglove 해석·rig/binding 무결성·동시 개정/rollback·시간/좌표 범위·run artifact origin·SDK 내보내기·mock CARLA MCAP를 검사합니다. 설치된 Starlette/httpx·AnyIO의 deprecation 경고 2개가 남습니다. 새 Pillow 시험의 deprecated 호출은 수정 후 해당 35개 시험을 재통과했습니다.
- TypeScript 검사와 production build가 성공했습니다. frontend 설치 시 npm audit는 알려진 취약점 0개를 보고했습니다. 이는 제품 전체 보안 감사나 원격 CI 실행 결과가 아닙니다.
- 실제 CARLA 서버 실행·센서 물리·인지 모델 성능·외부 CI 실행은 검증하지 않았습니다.
- API 재시작 전후 기존 실행 8개의 ID·입력 해시를 비교해 보존을 확인했습니다. Python SDK 예제도 빈 별도 DB에서 시나리오·요구사항·3개 실행·근거 파일을 실제 생성했습니다.
- 고정 의존성을 이용한 설치 스크립트가 성공했습니다. Finder와 같은 제한된 PATH에서 시작 파일의 API/UI 기동과 정상 종료를 확인했습니다. 설치 당시 Node/npm 경로를 로컬 실행 설정에 보존합니다.
- 독립 검토로 발견한 CARLA 실행 후 실패 기록 누락, 마지막 측정 구간의 늦은 collision 집계, SDK 부분 다운로드 보존 오류를 수정했습니다. 실패 실행은 이전 통과 근거를 무효화하며, 같은 멱등 키 재요청으로 실제 엔진을 중복 실행하지 않습니다. collision 관측 수가 전체 event stream의 완전성을 증명하지 않는다는 한계도 결과에 남깁니다.
- 실제 workspace DB를 schema 1에서 2로 이관하고 기존 실행 8개의 전체 payload를 백업과 비교해 그대로 보존된 것을 확인했습니다. 로컬 합성 MCAP 예제 374개 메시지/4개 채널을 SDK로 등록하고, 정확한 8.05초 조회·301개 telemetry 전체 순회·중복 등록 ID·원본 다운로드를 검증했습니다.
- schema 2에서 **3으로 추가 이관**한 뒤 기존 시나리오 8개·실행 8개·아티팩트 8개·요구사항 4개·기록 1개의 전체 payload를 백업과 비교해 보존을 확인했습니다. 이후 `examples/queued_workflow.py`가 실제 로컬 API에 제출한 작업을 별도 worker가 완료하여 실행 1개를 추가했습니다. 같은 키 재요청의 동일 ID·고정 스냅샷·입력 해시와 브라우저의 큐→저장된 결과 연결을 확인했습니다.
- 시험 묶음 단계에서 workspace DB를 **schema 4**로 이관했습니다. API·worker를 정상 종료하고 만든 SQLite 백업과 이관 직후 DB의 모든 기존 행을 비교했습니다. 시나리오 8개·실행 9개·요구사항 4개·MCAP 메시지 374개·작업 1개와 관련 아티팩트·개정·채널·이벤트·worker 이력이 보존됐습니다. Suite·campaign·baseline·comparison 테이블을 추가하며 기존 데이터의 의미를 바꾸지 않습니다.
- `examples/suite_workflow.py`를 빈 격리 DB와 실제 workspace API에서 각각 실행했습니다. 각 실행은 별도 worker로 합성 참조 시험 **25건**을 계산합니다. 최초 8개 case는 통과 4개/실패 4개, 16개 기준은 통과 10개/실패 6개였습니다. 라이브러리 개정 후에도 고정한 8개 case 재실행 결과가 일치했으며, suite 개정 비교의 전체 9개 key는 추가 1개·조건 불일치 1개·통과 유지 3개·실패 유지 4개로 분류됐습니다. Baseline 원본은 바이트 단위로 유지됐고 저장된 비교 파일의 SHA-256도 확인했습니다. [실제 workspace 결과 요약](validation/suites-local-2026-09-13.json).
- 센서 단계에서 workspace DB를 **schema 5**로 이관했습니다. API·worker를 정상 종료한 뒤 SQLite backup과 비교하여 기존 시나리오 12개·실행 35개·요구사항 6개·작업 27개·campaign 3개·baseline/비교 근거와 기존 MCAP 374개 메시지의 모든 행이 보존됐음을 확인했습니다. 새 센서 rig/binding 개정 및 run-recording origin 관계를 추가했습니다.
- 지원되는 ROS2 CDR1 및 Foxglove JSON의 실제 이미지·point-cloud bytes를 해석합니다. 독립 작성기의 합성 MCAP 예제로 실 HTTP 등록→rig 작성/개정→binding 고정→정밀 cursor→PNG/XYZ/manifest 내보내기를 검증했습니다. RGB 640×360, LiDAR 18,164점과 90개 메시지를 사용하며 미래/stale 프레임 제외, decoder hash·version, 이전 개정 bytes 보존을 확인했습니다. 이 예제는 직접 작성한 데이터이며 센서 물리·실측 결과가 아닙니다. [실제 workspace 결과](validation/sensors-local-2026-09-13.json), [사용법](sensor-replay.md).
- CARLA adapter 0.2.0에 캡처 MCAP 산출물을 추가하고, 실행 artifact를 기록으로 등록할 때 고정된 origin 관계에 scenario revision·seed·adapter version·fidelity·입출력 hash를 보존합니다. mock world 계약 시험과 표준 MCAP 해석 시험을 통과했으며, 실제 CARLA 서버 시험은 미완료입니다.
- 별도 Python 프로세스로 API 재시작 후 대기 작업 보존, 두 worker의 단일 작업 경쟁, 실행 중 API 쓰기, lease 갱신, dispatch 후 worker 강제 종료를 시험했습니다. 마지막 경우 결과를 `interrupted`/불명으로 남기고 해당 엔진 자원을 격리합니다. 시험 프로세스의 종료를 확인한 명시적 정리 선언 후 다음 작업만 실행되며, 불명 작업은 자동 재실행하지 않습니다.
- 독립적인 로컬 HTTP benchmark: 합성 1시간/44,107개 메시지/2,278,726B Zstd 원본을 0.3061초에 등록. 이후 50회 조회 중앙값 2.186ms, p95 3.733ms. 단일 로컬 합성 workload의 측정이며 fleet 성능이 아닙니다. [장비·조건·원시 결과](validation/recordings-local-2026-09-13.json), 재현 도구 `scripts/benchmark_recordings.py`.

최종 검사 명령:

```bash
bash scripts/check.sh
npm --prefix e2e ci
# 로컬에 Google Chrome이 있으면:
PLAYWRIGHT_CHANNEL=chrome npm --prefix e2e test
# CI 또는 Playwright Chromium을 쓰면:
npm --prefix e2e exec -- playwright install chromium
npm --prefix e2e test
```

브라우저 테스트는 별도 포트 8010/5174, 별도 worker와 `e2e/.runtime/workflow-db`를 사용합니다. 기본 사용자의 `.data`를 시험 데이터로 바꾸지 않습니다. `e2e/playwright-report`와 실패 시 `e2e/test-results`에 로컬 검사 자료를 남깁니다.

## 원래 목표 대비 범위

아래 세 절은 초기 개발 단계의 범위와 당시 다음 작업을 보존한 기록이다. 표의 '현재'·
'미구현'·'다음'을 최신 상태로 사용하지 않는다. 2026-10-10의 Neural 재구성·새 시점,
Software-DUT HIL, 도시 지도·Object/Log/Sensor 데모 상태는 이 문서 첫머리와 각 데모 문서를 따른다.

| 요구 영역 | 현재 근거 | 아직 필요한 완료 근거 |
|---|---|---|
| 기능이 풍부한 통합 플랫폼 | 시나리오·실행 큐·재생·데이터·검증·시험 묶음·연결·에이전트 화면의 한 업무 흐름 | 실제 고객 pilot, 아래 모듈들의 동작·통합 인수시험 |
| 다채로운 visualization | 실행 궤적의 Three.js 뷰, 카메라 모드, 시간 탐색, 이벤트·지표 plot, coverage, 로그 채널 span·실제 샘플 신호·정밀 시간 inspector, case 비교 행렬·상태 필터·지표 차이, 실제 이미지/XYZ preview·공유 cursor·native/선언 rig 좌표, 실제 PNG 위 검출 box·동일 입력 예측 비교·원본 라벨 편집·PR와 TP/FP/FN/crowd 진단, stack 입력·판단·명령 trace, CARLA startup·live 명령/다음 frame·원래 차량 XYZ/응답 그래프·원격 원래 실행/회수 연결·부분 진단 inspector, 센서 요청/실제 속성·coherent mount readback | 실제 clock/calibration pilot, 지도 형상, 대규모 포인트클라우드, 동기 비교·디버깅 UX |
| E2E DB 구성 | 시나리오 개정·실행 스냅샷·요구사항·아티팩트·멱등 키, MCAP index, 작업·이벤트·worker·엔진 예약, suite 개정·campaign·고정 baseline·저장된 비교, rig/binding 불변 개정·run-recording origin, 모델 파일/개정·프레임별 추론·저장된 비교, dataset/라벨/평가·component stack 개정·recorded replay와 remote attempt/store binding·별도 회수·출처 근거, schema10 센서 설정 head/불변 revision/멱등 키·context/2의 설정/rig/capability snapshot | 물리 calibration 검증·대용량 dataset import, Postgres migration, 원격 object store, 전체 lineage graph |
| AI Agent 사용성 | 동일 REST/OpenAPI, 도구 발견, llms.txt, Python SDK/CLI·bounded sweep·지속 저장되는 작업 제출/관찰/취소, suite→campaign→baseline→비교 저장·검증된 다운로드, 센서 rig/binding·bundle·원본/preview 내보내기, 모델 개정·추론·예측 비교·라벨 dataset·COCO 평가·검증된 다운로드, 원격 상태 조회·별도 회수/취소/원본 archive export, 센서 capability·설정 작성/개정/정확한 선택·검증된 manifest export | 실제 agent client 인수시험, 권한/자원별 실행 정책, 다단계 workflow, 선택형 LLM/MCP 연결 |
| 모듈 교체 | Protocol/entry point와 HTTP worker 결과 계약, unsupported 거절; 실제 CARLA lane-follow·emergency-brake·센서·기록 영상 추론, 0.4 live LiDAR shared stack·원본 재계산, 0.4.1 physics readback gate·차량 응답과 bounded 실패 진단, 0.5 engine-neutral attempt/inspect/retrieve와 실제 원격 회수, kernel1/2·정확한 component version 병행, 0.8 capability 기반 센서 설정/rig 적용 | 실제 불안정 startup/timeout 복구, 학습 인지 제어, 다른 engine/stack/sensor의 독립 계약과 동일 UX 검증 |
| 오픈소스 재사용 | React/Three/FastAPI/SQLAlchemy 사용, 후보별 공식 license 등록부 | 배포 조합 전체 SBOM·NOTICE·자산/model/dataset 권리 확인 |
| 상용 제품 수준 | 로컬 버전/무결성/회귀시험 기반 | 고객 pilot, OIDC/RBAC·조직 격리·감사·성능·장애 복구·설치/업그레이드·지원 gate |

## 첨부 제품군의 기능 경계

| 모듈 | 현 단계 |
|---|---|
| Object Sim | 참조 모델4 category; CARLA 기본 lane-follow/emergency-brake와 선택적 live LiDAR 제어, 설정 gate·차량 응답·원격 회수. 0.6 dense gap20m cruise는 passed, gap8m 정지 선행차는 두 profile 모두 failed. 0.8의 설정/장착 진단과 cruise failed를 별도로 보존. 물리 불안정 startup timeout·추가 ODD 후속 |
| Sensor Sim | 실제 CARLA RGB/LiDAR·MCAP 수집, 선언 rig/binding, 기록 영상 YOLOX CPU 추론 확인; 선택적 live LiDAR 제어와0.8 설정/rig 개정·actor 속성/81 pose readback; 외부 장애물 시야·물리 calibration·실측 상관성·추가 센서/인지 제어 후속 |
| Vehicle dynamics | CARLA0.4.1의 생성/설정 순서 유실 수정·rest 후 설정 gate·frame별 원본 속도/가속도·요청/응답 근거 구현; 초기 속도 부여 뒤 잔여 진동·gear/wheel 상태와 정밀 차량 모델 상관성 미완료 |
| Cloud execution | SQLite 큐·lease·취소·격리와 원격 영속 store/partial/final archive·별도 회수·fenced publish 구현 및 제한된 실제 인수; 분산 scheduler·Postgres·host/storage 장애·tenancy·retry 정책·고아 파일 GC 미구현 |
| Validation / Test suites | 고정된 시나리오·seed·입력·판정 기준의 suite 개정, campaign 일괄 제출, 전체 case/check 판정, 불변 baseline·비교 근거와 UI/API/SDK/CLI 구현; 교체형 observer·통계·impact 분석·검토 승인 흐름 필요 |
| Map / world | 참조 도로 도식; 실제 지도 import·편집·품질 검증 필요 |
| Data Explorer / Log Sim | MCAP 원본 ingest·CRC 검증·채널/schema·정밀 탐색·JSON/opaque, 지원 ROS2/Foxglove 이미지/점군 decoder·공유 cursor·선언 calibration/clock·원본 origin 구현; 실제 로그 pilot·추가형식·log-to-scenario 필요 |
| Synthetic datasets / traffic signs | 원본 이미지/라벨/taxonomy/split·검토 선언의 불변 개정·COCO bbox 평가·전체 분모 구현; 대용량 표준 import·분포/생성·실제 라벨 품질·domain gap 검증 필요 |
| Neural Sim | 재구성 엔진·데이터·새 시점 품질 검증 미구현 |
| HIL | 장비·signal/time/fault interface와 실제 장비 인수시험 미구현 |
| Agent workflow | 작업 제출·조회·취소·timeout 후 재관찰, suite/campaign/baseline/비교와 원격 status·멱등 회수·검증된 archive export API/SDK/CLI·예제 구현; 자연어·다단계 workflow·권한 정책 후속 |
| SDS / Vehicle OS | 별도 시험 대상 소프트웨어와의 연동 방향; 자체 상용 주행 SW/OS 구현 완료를 주장하지 않음 |
| Component stacks | 관측·정책·제어 교체 계약, 전체 파라미터 불변 개정, 기록 ego/LiDAR open-loop replay와 전체 소비 점군·판단·명령 trace 구현; 선택적 실제 CARLA LiDAR 명령·ACK·다음 frame 인수; 센서/차량 상관성 후속 |

## 다음 인수 단계

1. 이 로컬 통합 검증 결과와 문서를 v0.1 개발 기반으로 보존합니다. 상용 완료 판정은 아래 단계와 로드맵의 인수 기준을 통과해야 합니다.
2. 실제 CARLA의 명령/ACK/다음 frame·차량 응답·원격 영속 결과 회수까지 인수했습니다. 0.8 설정/장착 적용 이후 다음 인수는 근접·측면·박형 외부 장애물의 관측/가림과 semantic observer, 실제 완만 정지·제동 transient·물리 불안정 startup timeout입니다. 현재 live failed를 유지하며 metric 정의·기준을 시험 통과를 위해 바꾸지 않습니다.
3. 기록 이미지→고정 모델 CPU 추론→예측 비교와 독립 라벨 dataset 개정→COCO 평가를 추가했습니다. 이후 실제 라벨/로그 pilot·표준 dataset import·추가 형식, 교체형 observer·통계·분산 실행·팀 접근 제어를 각각 검토 가능한 기능으로 확장합니다. 현재 계약과 한계는 [실행 큐](execution-jobs.md)와 [시험 묶음·비교](test-suites.md)에 기록했습니다.
4. 원격 프로세스 종료·응답 유실 회수의0.5 당시13건 실제 인수 범위를 보존하고, 새 context/2 실제 장애 주입과 host power/storage/network 장애·장시간 실행·분산 소유권·팀 권한·retention/backup 운영을 별도 gate로 확장합니다. 새 센서/학습 모델은 각각 입력·좌표·시간·제어 범위와 실측 비교 근거를 고정해 추가합니다.

상세 인수 기준은 [로드맵](roadmap.md), 지표 의미는 [metrics.md](metrics.md), 실행 절차는 [README](../README.md)를 기준으로 합니다.
