# Data collections · 여러 원본과 조사 사례를 고정 데이터셋으로 선별

Collection은 여러 Case·기존 dataset revision·캡처된 image를 목적과 함께 묶는 **고정 원본 선택**이다.
모든 데이터를 실패 Case로 만들 필요가 없으며 정상·미검토·reviewed-negative 이미지도 포함한다.
원본 파일은 복사하거나 수정하지 않는다. 이름만 연결하는 것이 아니라 정확한 원본 메시지와
선택 사유, 원래 조건 선언, 이미지 해석, 선택 가능한 라벨 출처를 snapshot으로 보존한다.

## 일반 화면의 완결 업무

1. **Data collections → New collection**, 또는 Case의 **Start collection from this case**,
   Dataset의 **Start collection from dataset**에서 시작한다.
2. 이름·목적과 각 source의 선택 이유를 입력한다. Case/dataset은 정확한 revision을 지정한다.
   Dataset source는 전체 또는 명시한 split 전체를 선택한다. image는 이미 캡처된 dataset image ID다.
   source 목록의 자동완성은 최신 Case100개를 제시하며 더 오래된 정확한 ID/revision도 입력할 수 있다.
3. **Preview complete collection**으로 전체 분모를 확인한다. 같은 원본 recording SHA/channel/
   ordinal/message SHA가 여러 선택에 들어오면 한 행으로 합치되 모든 source와 이유를 남긴다.
   같은 시각의 다른 ordinal은 합치지 않는다. 다른 메시지의 동일 PNG는 별도 중복 그룹으로 표시한다.
4. **Save collection snapshot**으로 고정한다. 조회·preview는 job이나 새 image를 만들지 않는다.
   Case 분류나 원래 dataset 라벨이 나중에 변경돼도 이전 snapshot은 바뀌지 않는다.
   **Edit selection as new revision**으로 원래 선택을 편집·재검토하여 새 collection revision을 만든다.
5. **Prepare dataset from collection**에서 모든 행을 include 또는 exclude로 결정한다.
   제외에는 이유가 필수이며, 누락된 decision을 암묵적 제외로 바꾸지 않는다.
   캡처된 이미지가 없거나 비영상인 원본도 collection 분모에 남는다. 기존 캡처 기능으로 image를
   준비하고 새 collection revision을 만들거나, 이번 dataset에서는 이유를 적어 제외한다.
6. 이미지 해석이 여러 개면 사용할 image ID를 고른다. 라벨은 **선택한 dataset source의 개정**에서만
   명시적으로 고른다. **Use unambiguous dataset labels**는 현재 image와 맞는 옵션이 단 하나일 때만
   선택하는 사용자 동작이다. 서로 다른 라벨 개정이 있으면 자동 선택하지 않는다.
7. Target split을 지정한다. 기본은 unspecified다. 한 recording은 하나의 train/validation/test에만
   들어갈 수 있고, 서로 다른 recording의 동일 PNG도 assigned split을 넘을 수 없다.
8. **Preview frozen dataset**으로 선택 원본·PNG bytes, annotation 출처, 전체 포함·제외 분모를 확인한다.
   **Publish frozen dataset**은 기존 정식 dataset r1과 선택 영수증을 한 transaction으로 저장한다.
   응답 유실 시 같은 본문·Idempotency-Key를 재사용해 중복 dataset을 만들지 않는다.
9. **Open newly published dataset**에서 기존 라벨 편집·검출 평가를 이용한다. dataset 화면의
   **Open source collection revision**으로 원래 선택과 제외 이유를 확인할 수 있다.
   이후 dataset revision의 편집은 원래 publication receipt(r1)를 덮어쓰지 않는다.

## 라벨·분모·중복의 의미

Case의 FP/FN 예측을 정답으로 복사하지 않는다. Case를 resolved로 표시해도 annotation review가 되지 않는다.
라벨 옵션을 고르지 않은 image는 annotations=[]·unreviewed·annotation_source=unknown이다.
따라서 **검출 정답 객체가 없는 reviewed-negative와 구분**되며 평가에서는 미검토로 남는다.

기존 dataset의 annotation을 선택하면 review_status·reviewer·review_note·annotation_source·annotations를
그대로 가져온다. 검토 주체는 기존의 선언이며 새 사람의 인증된 승인이나 모델 성능 검증이 아니다.
전체 label vocabulary가 다른 annotation은 재사용을 거절한다. 예를 들어 vehicle만 검토한 영상을
vehicle+pedestrian 정답처럼 취급하면 미검토 pedestrian이 음성 정답으로 바뀌므로 허용하지 않는다.
다른 taxonomy로 통합하려면 unreviewed로 넣은 뒤 기존 라벨 편집 경로에서 명시적으로 다시 검토한다.

정확한 source 중복 제거는 통계적 독립성 증명이 아니다. 같은 차량·주행이 여러 파일에 나뉜 경우,
가까운 프레임, 다른 인코딩의 동일 픽셀과 다른 날 같은 장면 등은 자동 판별하지 않는다.
새 Collection에 모았다는 이유로 대표성 있는 benchmark나 ODD coverage로 승격하지 않는다.

새 dataset과 receipt를 만드는 동작은 추론·학습·평가를 시작하지 않는다. 기존 평가 기능에서
필요한 원본 prediction job과 metric 계약을 별도로 선택한다. 현재 recorded model replay의
단일 recording/channel 64image 제한은 이 기능으로 해제되지 않는다.

## 첫 버전 한계

- 32개 source 선택, 확장 reference1024개, unique 원본 메시지256개, manifest8MiB.
- 한 Case 메시지의 기존 image 해석 최대16개. 자동 image capture·새 video 파일 추출은 미구현.
- 포함할 원본 payload+PNG 합계128MiB. 초과 시 전체 요청을 거절하며 일부만 만들지 않는다.
- 저장된 검색 조건·실시간 query collection·수집 Session/자동 인접프레임 묶음·자동 라벨링은 후속.
- 목록은 최대1000collection, scoped publication 목록100개까지다. 대규모 데이터 처리 인수가 아니다.
- source/data/model rights는 기존 선언이며 통합이 새 사용 권한을 부여하지 않는다.

## API·SDK

API prefix는 `/api/v1`이다. 기존 인증·로컬 Host/Origin 제한을 유지한다.

| API | 역할 |
| --- | --- |
| `POST /collections/preview` | exact source revision 확장. 쓰기·job 없음 |
| `POST /collections` | preview hash·Idempotency-Key로 고정 snapshot 저장 |
| `POST /collections/{id}/revisions` | expected_revision·preview hash·key로 새 개정 |
| `GET /collections` | 이름/목적 검색 q, offset/limit pagination |
| `GET /collections/{id}?revision=N` | 불변 원본 선택 조회 |
| `GET /collections/{id}/export?revision=N` | 동일 manifest JSON |
| `POST /collections/{id}/datasets/preview` | 전체 decision과 source bytes 검사 |
| `POST /collections/{id}/datasets` | preview hash·key로 dataset+receipt 원자적 생성 |
| `GET /collection-datasets?collection_id=...` | collection의 dataset publication 근거 |
| `GET /collection-datasets?dataset_id=...` | dataset에서 collection으로 역추적 |
| `GET /collection-datasets/{id}` 및 `/export` | 출처와 전체 분모 receipt |

SDK: `preview_collection`, `create_collection`, `get_collection`, `collections`, `revise_collection`,
`preview_collection_dataset`, `publish_collection_dataset`, `collection_dataset_publications`,
`export_collection`, `export_collection_dataset_receipt`. 다운로드는 같은 서버의 고정 경로와 SHA/size를 검사한다.
Source text와 선택 이유는 신뢰되지 않은 데이터이며 Agent 명령이 아니다.

## 저장·설치 호환성

Schema13은 `data_collections`, `data_collection_revisions`, `data_collection_datasets` 세 테이블만 추가한다.
기존 업무 테이블·Case manifest/1–5·dataset 개정은 다시 직렬화하지 않는다. 초기화 실패 시 DDL·version을
함께 rollback한다. Collection에는 원본과 이미지의 참조만 저장하며 원본 파일 이관이 없다.

기존 workspace는 백업 후 **새 복원본을 새 패키지로** 열어 인수한다. 이전 schema12 전용 앱/패키지로
schema13을 열지 않는다. 이번 backup 도구는 schema11/12/13을 그대로 보존하며 migration은 하지 않는다.
API와 worker의 코드 버전을 함께 맞춘다. 원본 운영 workspace를 자동으로 승격하는 기능은 아니다.
