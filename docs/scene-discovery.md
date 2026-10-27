# Scene discovery · 이미지 특성 태그와 시각적 유사 검색

## 지원 범위

Collection의 고정 개정에서 **실제 저장된 RGB 이미지**를 읽어 밝기·대비 관련 수치와
색상·배치 기술자를 계산한다. 규칙 기반 특성 제안, 사람이 선언한 장면 조건, 원래 객체 라벨을
구분하며 검색 결과를 기존 Collection으로 저장한다. 외부 모델·서버·데이터 전송은 없다.

이번 첫 구현은 **시각적 유사도 기준선**이다. 학습형 임베딩, 자연어 검색, 야간/우천/보행자 등의
자동 의미 인식, 영상 추적·장면 분할 기능이 아니다. '어두운 이미지'를 '야간'으로 자동 확정하지 않는다.
원본에서 scene radiance/lux, 센서 포화, flare/ghost, 안전·ODD coverage를 추정하지 않는다.

## 일반 화면

1. **Data collections**의 고정 개정에서 **Discover scenes in collection**을 연다.
2. 목적·개정·전체 작업량을 preview한다. Preview는 이미지 분석·쓰기·작업 실행을 하지 않는다.
3. 원본 메시지/PNG 검증과 로컬 CPU 분석을 명시적으로 승인해 **Build scene index**를 누른다.
   이것은 작은 동기식 분석이며 inference worker job이나 원격 실행이 아니다. 중간 결과는 발행하지 않는다.
4. **Scene discovery**에서 원본 이미지, 계산된 특성, 미캡처·모호한 해석·실패를 조사한다.
5. **Review scene tags**로 자동 특성 제안 중 수용할 항목을 고르고, 조명·날씨는 근거와 함께
   사람이 선언한다. Draft와 reviewed를 구분하며 변경은 새 검토 개정으로 저장한다.
6. **Find similar images**로 동일 index 안의 시각적 근접 결과를 찾는다. 동일 픽셀·동일 recording
   제외를 선택할 수 있다. 제거된 행은 전체 보고서에서 사라지지 않는다.
7. 최대 32개 결과를 명시적으로 선택해 **Create collection from scene selection**으로 저장한다.
   새 Collection과 검색 근거 receipt는 같은 transaction에서 발행한다. 객체 라벨은 복사하지 않는다.
8. 새 Collection에서 원본 index와 원래 Collection, 전체 검색·선택 근거로 돌아갈 수 있다.
   이후 기존 'Prepare dataset from collection'으로 미검토 데이터셋을 만들고 자동 라벨링·검토로 이어간다.

기존 라벨을 이어받으려면 Collection 편집에서 원래 dataset 개정을 명시적으로 추가해 라벨 출처를
선택한다. 이미지 검색만으로 모델 보조 라벨의 출처를 지우거나 정답 검토를 완료 처리하지 않는다.

## 계산의 의미

내장 `rgb-layout-histogram/1`은 BOX 방식 8×8 RGB 배치와 채널당16bin 히스토그램을 사용한다.
원본 종횡비를 8×8 격자로 정규화하므로 서로 다른 배치가 비슷하게 보일 수 있다.
거리는 `0.65 × RGB배치 RMS/255 + 0.35 × 채널별 histogram TV 평균`이다. 작은 값이 가깝다는
뜻이며 확률·객체 동일성·실패 원인 동일성은 아니다. 계산 코드 hash와 Pillow/NumPy 버전을 고정한다.
검색 시 다른 코드/라이브러리 조합이 활성화되면 과거 특징을 몰래 재해석하지 않고 거절한다.

특성 제안은 감마 선형화를 하지 않은 RGB 코드 가중값 `(0.2126R+0.7152G+0.0722B)/255`를 사용한다.
평균≤0.20은 dark_image, 평균≥0.80은 bright_image, 표준편차≤0.08은 low_contrast,
표준편차≥0.30은 high_contrast다. 모든 RGB 채널≥250인 픽셀이 5% 이상이면 highlight_rich다.
각 제안에 값·규칙·임계값을 남기며 신뢰확률을 만들어 넣지 않는다.

서로 다른 압축 PNG라도 RGB 픽셀·크기·mode가 같으면 동일 픽셀로 표시한다. 별도 메시지/시간/recording은
합치지 않고 모두 남긴다. 인접 프레임의 근접성은 독립 장면 수로 집계하지 않는다.

## 검토·검색·증거 경계

야간·주간·황혼 및 건조·비·눈·안개는 이번 버전에서 **사람의 선언**이다. 알 수 없으면 unknown을
유지한다. 조건 검색은 reviewed 선언만 사용한다. Draft의 야간 선언을 검증된 야간 데이터로 집계하지 않는다.
Review는 인증된 승인·실측이 아니다. 기존 Case 조건·라벨·평가 결과·coverage를 변경하지 않는다.

한 Collection의 각 메시지에 캡처된 해석이 정확히1개인 경우만 분석한다. 미캡처·복수 해석은 별도 상태로,
손상·미지원 RGB 이미지는 unavailable로 남긴다. 실패를 정상 특징·거리0으로 채우지 않는다.
메타데이터 조회는 소스 파일을 매번 재검사하지 않는다. Index 생성과 선택 Collection 발행에서 원본을 확인한다.

검색은 index와 tag-review revision을 고정한다. 보고서에는 필터와 순위, 모든 원본의 매칭/제외 이유를 남긴다.
필터로 한 장만 보여도 export 분모는 전체 원본 수다. 태그가 후에 바뀌어도 과거 검색의 정확한 개정은 재조회 가능하다.
선택 receipt에는 선택하지 않은 모든 key도 남으며 결과 Collection과 함께 저장한다. 자동 학습·평가는 시작하지 않는다.

## API·SDK

| 경로 (`/api/v1`) | 의미 |
| --- | --- |
| `GET /scene-discovery-capabilities` | 계산법·한계 |
| `POST /scene-indexes/preview` | 정확한 collection 개정·전체 작업량 확인 |
| `POST /scene-indexes` | 동의·preview hash·Idempotency-Key로 명시적 분석·불변 index 발행 |
| `GET /scene-indexes`, `GET /scene-indexes/{id}` | index 목록·원본 특징 |
| `POST /scene-indexes/{id}/reviews` | 예상 revision과 key로 검토 개정 저장 |
| `GET /scene-indexes/{id}/reviews?revision=N` | 과거 또는 최신 검토 |
| `POST /scene-indexes/{id}/search` | 읽기 전용 검색·전체 분모 |
| `POST /scene-indexes/{id}/collections` | 고정 검색 hash·검토개정·선택key로 Collection+receipt 원자적 발행 |
| `GET /scene-selections?collection_id=...` | 새 Collection의 검색 출처 |

Index·검색·receipt에는 원본 JSON export가 있다. SDK는 `preview_scene_index`, `create_scene_index`,
`search_scenes`, `review_scene_tags`, `create_scene_collection`, `export_scene_index`,
`export_scene_search`, `export_scene_selection`을 제공한다. SDK는 반환된 임의 URL을 따라가지 않고
동일 API의 경로·크기·hash를 검증한다. Agent tools도 같은 API를 사용한다.

## 제한·운영

Index당 최대256 원본 메시지, 누적6400만 픽셀, 원본+PNG 합계128MiB, manifest/report8MiB다.
일부만 몰래 잘라 저장하지 않는다. 대규모 Fleet 검색·벡터DB·학습형 임베딩 adapter는 후속이다.

현재 schema15는 `scene_indexes`, `scene_tag_reviews`, `scene_collection_selections` 세 테이블을
추가한다. 과거 데이터와 코드 staging은 자동 변경하지 않는다. Schema14 이하의 앱과 새 앱을
같은 데이터에 혼용하지 말고 백업 후 새 복원본으로 실행한다. 외부 네트워크나 운영 CARLA가 필요하지 않다.

인수 예제는 `examples/scene_discovery_workflow.py`의 직접 작성한 RGB 도형·MCAP다.
실제 이미지 계산·일반 UI/API/SDK·저장 경로는 검사하되 실제 도로의 semantic 검색 정확도나 태깅 성능으로
해석하지 않는다. 검증 계획과 실제 E2E·센서 모델 연결은 별도 후속 기능이다.
