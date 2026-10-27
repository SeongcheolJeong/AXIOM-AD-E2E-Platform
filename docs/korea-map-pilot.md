# 한국 지도 내부 베타 · 세종 OSM / NGII 입력부 · 2026-10-24

Object Sim의 **한국·외부 지도**에서 원본 bytes·출처·권리를 저장하고,
변환 진단 → 큐 기반 CARLA 지도 로드 확인 → 근거가 일치하는 지도 게시 →
시나리오 작성으로 연결하는 기능을 추가했다. 외부 지도는 저장 개정과 해시를
고정하며 편집 가능한 `object-map/1`로 위장하지 않는다.

현재 실제 자료는 **세종 정부청사 인근 OpenStreetMap 도로**다. 원본 취득과
약 1.639 km의 도로 선택, OpenDRIVE 생성, 원본/첨부 export 대조와 실제 CARLA
로드·차로 읽기를 확인했다. 별도 실제 주행 4건 중 직진·좌회전 3/2 m/s는 기준을
통과했고, 우회전 3 m/s는 실행을 완료했으나 횡방향 오차 기준을 넘었다. NGII 원본은 확보하지
못했으며 선택 변환기의 31개 검사는 합성 의미 검사용 SHP에 한정한다.
우회전 실패의 원본 시점은 실제 UI에서 Case로 저장·복원·export하고 SDK와
bytes 해시를 대조했다. 지도 로드와 제한된 경로 제어 결과는 센서·정밀지도·
실세계 안전 검증이 아니다.

## 현재 확인 상태

| 단계 | 상태와 확인 범위 |
| --- | --- |
| 실제 지도 자료 | OSM 원본 1,096,000 bytes, 3,669 nodes / 428 ways 취득·보존 |
| 구간 선택 | 좌표를 고치지 않고 way ID 8개 / node 15개 선택; 중심선 길이 합계 약 1,639.186 m |
| OSM → OpenDRIVE | CARLA `Osm2Odr` 사전 변환; 출력 road 16개 / junction 3개, 41,404 bytes |
| 원본 등록·복원 | 일반 map-source API에 XODR r1 등록; 상위 OSM 원본 등 첨부 5개와 원본 export bytes 대조 완료 |
| 실제 CARLA 지도 확인 | `job_2e7f835782134bd88ef6780a1578c42d` 완료; 원본 후보/readback SHA 일치, drivable node 1,682개 / spawn point 20개, cleanup confirmed |
| 지도 게시·시나리오 작성 | map catalog r1 게시·초기 네 시험 API 작성. 일반 UI에서 기존 좌회전 3 m/s 시험을 편집해 2 m/s r2 저장·실행 |
| 실제 RGB 주행 | 실제 4회 실행 완료·3 pass / 1 fail. 우회전 횡오차 기준 fail 유지. UI r2의 600 native / 150 RGB 기록·재생 동선 확인 |
| 원본 시점 Case | 우회전 frame 272 / 13.65 s를 실제 UI에서 저장·새로고침 복원·봉인 export. SDK 읽기/export와 4,305 bytes·SHA 일치 |
| NGII SHP 변환기 | 선택 패키지 구현·합성 semantic 검사 31개 통과. 실제 NGII 입력·변환·native 인수 미확인 |
| 외부 공개 | 로컬 내부 베타. 공개 서비스 배포나 지도 재배포 인수 완료를 뜻하지 않음 |

약 1.639 km는 선택한 OSM way 중심선 길이의 합계다. 연속 주행으로 달성한 거리,
양방향 차로 길이, 측량 정확도 또는 원본 전체 영역의 크기로 해석하지 않는다.
출력 road/junction 수는 변환기가 만든 OpenDRIVE 요소 수다.

## 실제 원본과 변환 근거

대상은 세종 도움4로·도움6로·다솜3로다. 원본 취득 시각은
`2026-10-24T13:26:15.864659+00:00`이며 요청 bbox는
`[127.256, 36.498, 127.269, 36.507]`이다. 제공자는 OpenStreetMap contributors이고
수집 manifest의 라이선스 표기는 `ODbL-1.0`, 표시 문구는
`© OpenStreetMap contributors`다. 실제 이용조건은 원본의
[저작권·라이선스 안내](https://www.openstreetmap.org/copyright)를 함께 보존한다.
서비스의 권리 필드는 사용자가 기록한 선언이며 법적 허가 심사 결과가 아니다.

| 보존 단계 | SHA-256 |
| --- | --- |
| 상위 OSM 원본 | `aaa40e5c35700df99c9a3b7ff33a2f05c4b32e279aa65fe7f59f65bb233f1f0b` |
| 선택 OSM | `d7957a9ae8ce12402d11a2f5f84328717dfe5a0c7089da1276d4621d1eb31b55` |
| 현재 XODR 후보 / native readback | `8c05b20e11b7386a22f628f37c9bd6a3ab3ebb50d61aaada232cbe353b168e82` |
| map-source r1 manifest | `1469962321f8ea43faa1cd9cc07ac077593e5a94ab20a671e31db9ce6e7aff8f` |
| conversion r1 manifest | `330527a0fc867b948b560ec3fc610a6c29a5be6f010dea312c42c834c4f34990` |

등록 ID는 `korea-sejong-osm-20261024`, 변환 ID는
`mapconv_8c329b587ac9dddcd9ce63c5950b4967`이다. 현재 등록의 `source_kind`는
`opendrive`다. **OSM 직접 업로드·자동 변환은 현재 일반 UI/API 입력 기능이 아니다.**
CARLA `Osm2Odr`로 사전 변환한 XODR를 등록하며, 상위 OSM·선택 OSM·선택 manifest·
변환 manifest·취득 manifest를 첨부로 함께 보존한다.

게시 지도는 `external-f0d2c89af5049cf450c4eb2272a41673e12074f4` r1이며 manifest
SHA는 `177ed733cefa1ad17337a1849cf1567f43108983d137dc8761691d157d68304e`다.
게시 문서의 source/conversion/qualification 참조와 위 native 후보 해시가 일치한다.

현재 v2 변환은 CARLA Python 0.9.16을 사용했다. 날짜를 고정하고 한글 이름을
ASCII XML numeric reference로 보존하여 native 문자열 왕복을 검사한다.
이전 `selected/roads.xodr` 후보는 별도로 남겨 둔다. v2와 이전 후보의 서로 다른
해시를 섞거나 과거 자료를 최신 결과로 덮어쓰지 않는다.

좌표는 WGS84 경위도에서 위도 `36.502`, 경도 `127.263`을 기준으로 하는 로컬
Transverse Mercator로 변환한다. 변환 설정의 `center_map=false`,
`use_offsets=false`를 보존하고 OpenDRIVE와 CARLA의 Y축 반전은 엔진이 적용한다.
기본 폭 3.5 m, 누락 차로 수의 기본값, 생성 교차로, 원본 고도 부재와 신호
프로그램 미제공을 가정으로 명시한다. 측량 기반 NGII 정밀도로지도가 아니다.

근거 파일:

- [취득 manifest](../.runtime/korea-map-20261024/sources/sejong-government-area.acquisition.json)
- [선택 manifest](../.runtime/korea-map-20261024/selected/selection.json)
- [현재 v2 변환 manifest](../.runtime/korea-map-20261024/converted-v2/conversion.json)
- [일반 API 원본 등록·export 대조](../.runtime/korea-map-20261024/source-registration.json)
- [실제 native 로드·차로 읽기 실행](../.runtime/korea-map-20261024/qualification-first.json)
- [게시된 map catalog](../.runtime/korea-map-20261024/published-map.json)
- [네 개 시험의 작성 기록](../.runtime/korea-map-20261024/authored-trials.json)

## 일반 제품 동선과 상태 경계

`Object Sim → 한국·외부 지도`에서 아래 흐름을 사용한다.

1. **원본 등록**: `.xodr` 또는 NGII SHP ZIP을 bytes 그대로 저장하고 이름·출처 URL·
   라이선스·재배포 조건·메모를 기록한다. 기존 원본을 선택하면 새 개정으로
   등록할 수 있다. 원본과 첨부의 합계는 32 MiB 이하이며 첨부는 최대 8개다.
2. **변환·진단 실행**: 고정 원본 개정·해시를 입력으로 변환 후보와 진단을
   저장한다. XODR 구조 확인 또는 NGII 변환이 성공해도 native 준비 완료가 아니다.
   원본에 선언한 가공·가정과 이번 변환기가 추가한 가정을 별도 표시한다.
3. **CARLA 지도 확인 요청**: `Idempotency-Key`와 source/conversion의
   ID·revision·SHA를 고정해 기존 Experiment 큐에 제출한다. 작업 상태·오류·취소·
   산출물을 동일 실행 API에서 확인한다. 설정된 adapter가 있다는 응답은
   worker·엔진의 현재 readiness 확인이 아니다.
4. **확인된 실행 지도로 게시**: 성공한 동일 후보의 native receipt, 입력/readback
   해시, 엔진 버전, 산출물·cleanup을 서버가 검증한 뒤 map catalog 개정을 만든다.
   여기서 게시란 프로젝트 안의 지도 등록이며 인터넷 공개 배포가 아니다.
5. **이 지도로 시나리오 작성**: 게시된 map ID·revision·SHA를 Object Sim으로 넘겨
   배우·경로·행동 저장과 별도의 일반 실행·재생으로 이어간다. 외부 지도 자체는
   읽기 전용이며 지도 확인 작업만으로 주행 검증을 인정하지 않는다.

원본/변환/실행 선택은 URL에 복원한다. 원본은 `source`, `revision`,
`source_hash`, 변환은 `conversion`, `conversion_revision`, `conversion_hash`,
실행은 `qualification`으로 고정한다. 잘못된 해시, 누락된 실행 응답, 단순히
`completed`라고 쓴 결과, 불확실한 실행은 성공 근거로 인정하지 않는다.
작성·변환·실행·게시에는 해당 프로젝트의 Engineer 권한이 필요하다.

공통 API는 `/api/v1/map-sources` 아래 원본 목록/등록/개정, `convert`, `qualify`,
`publish`, `export`를 제공한다. Python SDK의 `map_source_capabilities`,
`create_map_source`/`upload_map_source`, `convert_map_source`,
`qualify_map_source`, `publish_map_source`, `export_map_source`가 같은 계약을
사용한다. SDK export는 응답의 `X-Content-SHA256`와 반환 bytes를 대조한다.

## 실제 native 확인 범위

최초 성공 실행은 `job_2e7f835782134bd88ef6780a1578c42d`이며 엔진 build는
`1ae5356`, adapter는 `carla-map-qualification/1.0.0`이다. 시작부터 종료까지
약 8.45초가 걸렸고 원본 후보와 native `to_opendrive()` readback의 bytes 해시가
일치했다. 드라이빙 차로 노드 1,682개와 spawn point 20개를 읽었으며
`cleanup_status=confirmed`, `outcome_uncertain=false`다.

로드 설정은 `vertex_distance=1.0`, `wall_height=0`, `additional_width=0`,
`smooth_junctions=false`다. 확인기는 ±10,000 m의 로컬 좌표 범위와 유한값,
차로 노드 존재, 보존한 엔진/runner identity, candidate·산출물 해시를 검사한다.
receipt의 scope는 `native_map_load_readback_not_driving`,
`driving_evaluated=false`, `physical_validation=false`다.

신규 전용 2500 endpoint의 기동 실패는 운영 관측에서 메모리 부족으로 분류했다.
보존한 [초기 로그](../.runtime/korea-map-20261024/server.log)는 `Killed`를 기록하며,
[메모리 4 GiB 제한 재시도](../.runtime/korea-map-20261024/bounded-start-failure.json)는
`failed`다. 해당 파일 자체가 커널 OOM 판정 전문을 포함하는 것은 아니다. 성공한
지도 확인은 기존 2100 endpoint와 공통 lease를 사용했다. 이 절차가 기존 엔진을
자유롭게 재시작하거나 동시에 점유할 권한을 주는 것은 아니다. 실패 시도는
성공 실행으로 덮어쓰지 않으며 별도 운영 근거를 유지한다. 서비스/엔진의 현재
가용성은 보존한 과거 receipt만으로 보장하지 않는다.

## 작성된 시험과 실제 주행 결과

게시 map r1의 native 방향성 node 연결을 사용해 네 시험을 일반 scenario API에
저장했다. [작성 보조 스크립트](../scripts/author_korea_map_pilot.py)는 카탈로그와
시나리오를 작성하는 도구이며 native 엔진 실행이나 검증 결과를 대신하지 않는다.
정상 제품의 시나리오 API·고정 map 참조는 재사용하지만, 이 스크립트 자체를
브라우저에 제공된 자동 시험 생성 기능으로 표현하지 않는다.

| 작성 조건 | 저장 경로 길이 | 현재 의미 |
| --- | --- | --- |
| 직진 3 m/s | 약 42.000 m | 작성 시나리오 |
| 좌회전 3 m/s | 약 42.134 m | 작성 시나리오 |
| 우회전 3 m/s | 약 40.991 m | 작성 시나리오 |
| 별도 좌회전 2 m/s r1 | 약 42.134 m | 작성만 완료·미사용 |

네 번째 실제 실행은 위 별도 2 m/s r1을 사용하지 않았다. 일반 UI에서 기존
좌회전 3 m/s `scenario2_0eace5f76b114ddf8864f33671f64631` r1을 열고 속도 2 m/s,
duration 30 s, 이름을 편집했다. 설정 검증 → r2 저장 → 실행 준비 → 시험 큐 제출 →
재생 동선을 사용했으며 원래 r1을 보존했다. 속도와 duration이 함께 변경된
관측 비교이므로 속도 하나만의 통제된 인과 효과로 확대하지 않는다.

작성 경로 길이는 실제 이동 거리와 다르다. 아래는 보존된 실제 native 응답에서
다시 계산한 결과이며 위 작성 계획과 구분한다. 네 실행 모두 seed 42, 같은 map
r1과 XODR 해시, `carla-city-object/1.0.0`을 사용했다. `status=completed`,
`cleanup_status=confirmed`, `outcome_uncertain=false`를 확인했다.

| 실제 조건·receipt | 실제 이동 거리 | 횡방향 오차 p95 | 최종 goal 오차 | RGB 기록 / drop | 기준 판정 |
| --- | ---: | ---: | ---: | ---: | --- |
| [직진 3 m/s](../.runtime/korea-map-20261024/run-straight-3.json) | 41.123333 m | 0.0000465 m | 0.876830 m | 110 / 0 | pass |
| [좌회전 3 m/s](../.runtime/korea-map-20261024/run-left_turn-3.json) | 41.450301 m | 0.428690 m | 0.914462 m | 115 / 0 | pass |
| [우회전 3 m/s](../.runtime/korea-map-20261024/run-right_turn-3.json) | 40.203726 m | **0.715182 m** | 1.043327 m | 110 / 0 | **fail** |
| [좌회전 2 m/s · UI r2](../.runtime/korea-map-20261024/run-left_turn-2-ui.json) | 41.393857 m | 0.305802 m | 0.937450 m | 150 / 0 | pass |

공통 기준은 횡방향 오차 p95 ≤0.5 m, goal 오차 ≤2 m, 최종 속도 ≤0.2 m/s,
goal 도달·native contact 없음이다. 네 실행의 최종 속도는 모두 0.2 m/s 미만,
차량 collision callback 집계는 모두 0이다. **우회전은 0.715182 m >0.5 m이므로
실행 완료와 관계없이 `sut_performance_failure`다.** 기준을 완화하거나 실패를
제외하지 않는다. 이 결과만으로 원인이 지도 형상인지 제어기인지 확정하지 않는다.

각 실행 ID는 직진 `job_d4b1a793c29e484b91fe69a29dd4be03`,
좌회전 `job_66173a2e9ed3493aaac3c2351685bea4`,
우회전 `job_04034212ff6e42a3bbb6fb820878aaa6`,
UI 좌회전 r2 `job_850d746a6e2a4d1988d139baec1f38a4`다. 실행 receipt에는 scenario 개정·
manifest, seed, 설정 hash, 원본 scene·evaluation·camera archive·map 해시가 있다.
RGB 수치는 엔진 기록 수이며 전체 bytes 해시·영상 표시 검사는 해당 산출물과
브라우저 근거를 대조한다. UI r2는 600 native frame / 150 RGB / drop 0으로
완료했고 scenario r2 manifest SHA는
`27b4bd47afebcddb8cdaec25934b659a9fbcf2e1f945826daf09d183719d6c37`이다.

제어기는 지도와 배우 ground truth를 사용하는 bounded route-follow다. RGB는
관측 산출물이며 제어기 입력이 아니다. 횡방향 오차는 요청 경로에 대한 오차이고
실제 도로의 측량 오차가 아니다. 접촉 0은 기록한 차량 callback 범위의 결과다.
실물 차량·센서 검증, 실도로 안전성, 렌더링·물리의 bytes 단위 재현성을 주장하지 않는다.

## 실패 시점의 조사 Case

코드에 `선택한 원본 시점을 Case로 저장` 기능을 연결했다. 일반 City 재생 화면에서
선택한 프레임의 제목과 조사 메모를 입력해 `선택 프레임 Case 저장`을 누르면
봉인된 실행 manifest·scene SHA·frame index·simulation time을 서버가 대조한다.
실제 우회전 미통과 실행에서 frame 272 / 13.65 s를 선택해 Case
`case_cf8adc8b06a14a368cd9848cb1f0397b` r1을 UI로 저장했다. 새로고침 뒤 동일
프레임·시각으로 복원했고 UI의 봉인 download와 SDK get/export를 대조했다.
보존 export는 4,305 bytes이며 manifest SHA는
`bdafb71707f7555f6f2af2360426b6fe0aa83badbf0261afe5e72c26ee6bb98e`다.
[Case 인수 receipt](../.runtime/korea-map-20261024/case-acceptance.json),
[봉인 export](../.runtime/korea-map-20261024/case-export.json),
[실제 UI 화면](../.runtime/korea-map-20261024/case-ui-proof.png)을 보존했다.

선택 프레임의 순간 경로 오차는 약 0.808862 m다. 실행 전체 p95 0.715182 m와
동일한 지표로 취급하지 않는다. Case 메모는 접촉 관측 없음과 지도 형상/제어
기여 원인 미분리를 함께 기록하며, 해당 메모를 검증된 원인 판정으로 바꾸지 않는다.

서버는 해당 `city-scene.json`의 **객체 상태 JSON 프레임 하나**를 좌표 변환 없이
MCAP 메시지로 추출하고 recording-backed Case를 만든다. RGB 영상 bytes를 새로
복사한 센서 측정이나 새 시뮬레이션 실행이 아니다. source map·conversion·native
qualification 계보, map/scenario 개정, adapter/fidelity, execution/input/scene/frame/
recording 해시를 함께 보존한다. 완료된 native 실행만 허용하되 평가 verdict가
fail인 조사 대상은 제외하지 않는다.

일반 API는 `/api/v1/object-sim/executions/{execution_id}/cases` 아래 생성·고정
개정 읽기·export를 제공한다. SDK의 `create_object_scene_case`, `object_scene_case`,
`export_object_scene_case`가 같은 핀을 사용하며 export는 기대 manifest SHA와
헤더·반환 bytes를 대조한다. UI는 `scene_case`·`scene_case_revision` URL로 저장
프레임을 다시 열고 `봉인 Case 내려받기`에서 해시를 검사한다.
Case의 workflow state는 시험 verdict가 아니며 `physical_validation=false`,
`safety_verdict=not_evaluated`를 유지한다.

실제 인수 중 `route_follow` 원본에 reactive planner event가 없다는 이유로 Case
읽기가 거절되는 문제를 발견해 수정했다. Case는 원본 scene·계보·프레임 시각을
그대로 검증하면서 해당 이벤트를 필수로 요구하지 않는다. 원본에 빈 이벤트를
추가하거나 해시를 바꾸지 않으며 기존 story 경로는 이벤트 요구를 유지한다.
기존 story 45개와 Case 8개의 집중 회귀 검사 총 53개가 통과했다. 이는 실제 UI
Case 인수 및 native 주행 4건과 별도의 코드 검사다.

근거: [Case backend](../backend/src/ade2e/object_scene_cases.py),
[SDK](../sdk/python/src/axiom_sdk/object_scene_cases.py),
[UI](../frontend/src/components/city-sim/CityCaseCapture.tsx).

## NGII 선택 패키지의 범위

실제 NGII 원본은 현재 없다. [선택 패키지 안내](../integrations/geospatial/README.md)와
[합성 semantic 검사](../integrations/geospatial/tests/test_ngii.py)가 구현 근거다.
`axiom_geospatial.inspect_ngii_zip`은 A1 node, A2 lane link, B2 boundary와
SHP/SHX/DBF/PRJ/CPG를 읽고 원본 ID·속성·좌표·해시를 보존한다.
`pyproj`의 offline 좌표 변환, strict UTF-8/CP949, 로컬 원점과 역변환 잔차를 기록한다.

차로 폭은 명시적으로 연결된 B2의 단면에서 계산하며 1 m 이하 간격으로 검사한다.
F/T node 방향·연결을 보존하고 근접 좌표만으로 연결·방향을 추측하지 않는다.
원본 A2 polyline을 그대로 piecewise line으로 출력하므로 그 원본 선에 대한
reference XY 오차는 0이며, 경계 기반 차로 중심 이동은 0.5 m 이하만 허용한다.
이 수치는 실제 도로와의 측량 오차나 연속 곡률의 품질 보장이 아니다.

기본 상한은 선택 256 links, 차로 길이 합계 5 km, footprint 5 km다. multipart,
불명확한 CRS, 잘못된 방향·연결·폭, 미지정 branch/merge는 차단한다.
2D 평면 높이, 폭 override, junction connector 지정은 명시적 작성값으로 기록한다.
측량 높이·원래의 합법 회전 관계로 가장하지 않는다. source Z의 수직 datum은
검증하지 않으며 lane change·교통통제·도로 묶음·주변 도시 asset은 가져오지 않는다.

전국좌표 원점을 표준 OpenDRIVE header offset으로 다시 적용하지 않는다.
정확한 source/target WKT와 local origin은 report 및 `userData`에 보존하며,
NGII 변환 결과의 native GNSS/geolocation은 별도 인수 전까지 미검증이다.

## 최종 실행 정리와 소개자료

[최종 runtime receipt](../.runtime/korea-map-20261024/runtime-final.json)의
`2026-10-24T14:21:01.678002+00:00` 확인 시점에 본 베타 소유 active job은 0개다.
완료 job 5개는 지도 확인 1개와 실제 주행 4개이며 모두 주행 시험이라는 뜻은
아니다. 사용한 2100 endpoint의 현재 지도는 `Carla/Maps/OpenDriveMap`, dynamic
actor는 0개, 공통 lock은 free, quarantine은 없다. 기존 엔진 관련 PID 4개
`289474`, `289481`, `496583`, `496590`의 생존을 확인했다. 이는 해당 시점의
정리 상태이며 서비스의 지속 가용성이나 과거 기본 지도로 복원되었다는 뜻은 아니다.

배포용 파일:

- [한국 지도 소개 HTML](../reports/axiom-korea-map-introduction-20261024.html)
- [한국 지도 베타 사용 안내 HTML](../reports/axiom-korea-map-beta-guide-20261024.html)

[보고서 정적 QA](../.runtime/korea-map-20261024/report-static-qa.json)에서 파일 링크,
중복 HTML ID, JavaScript 문법과 미디어 참조를 검사했다. 원본 미디어 해시는
[provenance](../reports/korea-map-assets-20261024/provenance.json)에 보존한다.
**브라우저의 `file://` URL 정책 차단으로 두 HTML의 브라우저 시각 검토는
실시하지 못했다.** 실제 제품 UI의 지도 게시·시험 편집·실행·재생·Case 인수와
별도의 문서 검토 경계다. 정적 QA와 내부 베타 인수는 상용 release gate 완료를
뜻하지 않는다.

## 남은 작업과 범위

- 우회전 횡오차의 원인을 원본 지도 형상·요청 경로·제어 추종으로 나눠 조사한다.
  기준 미통과 실행과 Case를 보존하며, 변경 시 새 개정·새 실행으로 대조한다.
- 현재 확인은 세종 선택 구간·한 차량·seed 42·기록한 조건에 한정한다.
  전체 1.639 km 연속 주행, 여러 시드·교통 조합, 일반화된 교차로 품질 인수는
  완료했다고 주장하지 않는다.
- NGII 원본과 사용조건을 확보하면 첫 300–500 m 구간에서 source geometry·폭·
  방향·node 연결을 확인한 뒤 명시적인 교차로와 1–3 km로 확장한다.
- 세종 OSM 지도는 원본 가정과 표시 의무를 유지한다. 신호 정책·실측 높이·정밀
  센서 환경·물리 검증·상용 release gate는 별도 후속 작업이다.

현재 구현 근거는 [backend](../backend/src/ade2e/map_sources.py),
[native handler](../integrations/carla/src/axiom_carla/map_qualification.py),
[SDK](../sdk/python/src/axiom_sdk/map_sources.py),
[제품 UI](../frontend/src/components/external-maps/ExternalMapLibrary.tsx)다.
브라우저 [계약 검사](../e2e/tests/external-maps.spec.ts)의 작성된 응답은 native
실행 근거가 아니며, 실제 완료 판단은 위 보존 receipt를 사용한다.
