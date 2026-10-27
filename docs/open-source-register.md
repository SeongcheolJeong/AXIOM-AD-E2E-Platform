# 오픈소스·표준 도입 등록부

## 2026-10-17 · Native C/C++ Virtual ECU

C++ reference ECU의 JSON 파서는 **nlohmann/json 3.11.3 (MIT)**를 수정 없이 고정했다.
원본 header·LICENSE와 출처/파일 SHA는 `integrations/vecu/native/vendor/`에 보존하며
소스 빌드 artifact에 포함한다. 제어 알고리즘·SHA-256 도우미·빌드 래퍼는 자체 구현이다.
컴파일러 버전/옵션과 실행 파일 해시는 각 build manifest에 기록한다.
CARLA 엔진·지도·차량·영상의 기존 권리 경계는 이 코드 라이선스와 별개다.
Synopsys Silver 코드를 포함하거나 Silver 호환성을 구현한 것은 아니다.

## 2026-10-17 · Virtual ECU Lab

Synopsys virtual ECU 백서와 Silver는 아키텍처 참고 자료이며 코드·UI·상용 모델을 복제하거나 포함하지 않았다.
자체 reference DUT와 기존 CARLA adapter를 재사용한다. 기존 CARLA 코드/자산 라이선스 경계는 유지된다.
SIL Kit/FMI/Silver 런타임을 설치·연동한 것으로 표시하지 않는다. 새 third-party source는 포함하지 않았다.

## 2026-10-09 · Object Sim 설명용 차량 시각 자산

- [Kenney Car Kit](https://kenney.nl/assets/car-kit) 3.1의 sedan GLB·colormap을 로컬에 포함했다.
  공식 다운로드의 `License.txt`는 **CC0 1.0**이며 코드/엔진/센서 원본과 별도 자산으로 관리한다.
- [보존한 LICENSE](../frontend/public/assets/kenney-car-kit/License.txt)와
  [출처·archive/member SHA-256 manifest](../frontend/public/assets/kenney-car-kit/manifest.json)를 함께 배포한다.
  보존 native bbox에 크기를 맞추고 actor 색상을 적용한다. CARLA blueprint mesh나 물리 모델과 같다는 뜻은 아니다.
- 압축하지 않은 GLB를 읽으므로 Draco/Meshopt WASM decoder를 초기화하지 않는다. 기존 production CSP는 유지한다.

## 2026-10-09 · Object Sim 도로 컴파일러

- `integrations/opendrive`는 `scenariogeneration==0.16.7`을 선택적으로 설치해 실제 OpenDRIVE를 생성한다.
  upstream 코드의 [MPL-2.0 원문](https://github.com/pyoscx/scenariogeneration/blob/main/LICENSE)과
  의존성 버전을 고정한다. 자체 adapter·생성 지도·CARLA asset의 권리는 별개다.
- compiler 파일과 geometry 파일 hash를 지도 빌드 출처에 보존한다. 상용 배포물의 전체 SBOM·notice 검토 완료를 뜻하지 않는다.
- 지원 geometry와 명시적인 차로·교차로 연결에 한정한다. pyoscx 사용만으로 OpenDRIVE 전체 또는 표준 인증을 주장하지 않는다.
- 웹 도로·actor 대체 형상은 기존 Three.js 기반이다. 경쟁사 mesh·화면·지도 데이터를 복사하지 않는다.

확인일: **2026-09-12**. 공식 프로젝트 저장소·공식 사이트를 직접 확인했다. 이 표는 도입 후보와 확인된 upstream 조건을 정리한 것이며, 모든 구성요소의 설치·연결 완료 또는 특정 배포물의 법적 검토 완료를 뜻하지 않는다. 버전이 고정되지 않은 링크는 이후 바뀔 수 있다. 실제 배포 전에는 **선택한 release/commit과 함께 배포하는 파일**의 LICENSE·NOTICE·dependency·asset 조건을 다시 기록한다.

MCAP·sensor decoder·codec의 구현 상태 갱신: **2026-09-13**. 아래 고정 버전과 보존한 notice를 반영했으며, 다른 원문 조사의 확인일은 위 날짜를 유지한다.

## 도입 상태의 의미

2026-10-09 도시/Autoware 개발에서 추가로 고정한 경계:

- Autoware 실행 이미지는 `ghcr.io/autowarefoundation/autoware@sha256:00aae4988d137f042de42e744d24616054c9d1917c6b64562eabae3eb6a6fe17`이다.
  실제 설치된 메시지·서비스·제어 보정표와 바이너리 해시를 프로필 receipt로 보존한다.
  자사 adapter 코드, upstream Autoware/ROS package, CARLA/Unreal과 지도·asset 권리는 각각 별도다.
- Bitbucket `carla-simulator/autoware-contents`의 commit `062f94b5322e679ee0430e2daa6995dca9f7f0ea`
  Lanelet2 지도는 별도 지도 LICENSE를 확인하지 못했으므로 private 원격 runtime에만 보관한다.
  native 정지선으로 만든 파생 지도도 같은 제한을 유지한다. 코드의 Apache-2.0으로 지도 권한을 추정하지 않는다.
- 도시 카탈로그는 설치 CARLA의 blueprint ID·메타데이터·원본 XODR/차로 표본을 기록한다.
  엔진 mesh/texture를 web용 asset으로 추출·배포하지 않는다. Web의 절차적 proxy와 실제 renderer RGB는 구분한다.
- YAML은 SafeLoader를 사용하고 실행 태그·별칭·중복 키·과도한 크기/깊이를 거절한다.
  PyYAML 의존성과 해당 배포물 notice는 backend dependency inventory에서 함께 관리한다.

- **M0 선택**: 첫 로컬 개발판의 기술 선택. 실제 설치 버전은 저장소의 lock/requirements 파일 및 실행 환경을 기준으로 한다.
- **기록 slice 구현**: 고정 dependency와 별도 bounded parser로 MCAP 등록·조회가 동작한다. 내부 센서 payload 해석이나 실측 유효성 검증까지 완료했다는 뜻은 아니다.
- **센서 decode slice 구현**: 아래 명시한 표준 payload의 실제 bytes를 해석하고 PNG/XYZ preview를 생성한다. 지원하지 않는 schema·codec은 거절하며, 장비 calibration·시간 동기화·실측 성능 검증과 구별한다.
- **연결 후보**: 근거 자료를 확인했지만 실제 adapter와 엔진 실행 검증은 별도 작업이다.
- **표준 검토**: 데이터 교환 규격 후보. 규격을 언급하거나 자체 JSON을 사용하는 것만으로 준수 상태가 되지 않는다.

## 플랫폼 기반

| 구성요소 | 상태 / 용도 | 공식 원문에서 확인한 조건 | 출처 및 도입 시 기록할 것 |
| --- | --- | --- | --- |
| React | M0 선택 · Workbench UI | MIT | [LICENSE](https://github.com/facebook/react/blob/main/LICENSE). 선택 버전, copyright·license notice, frontend dependency 목록 |
| TypeScript | M0 선택 · UI/API 타입 | Apache-2.0 | [LICENSE](https://github.com/microsoft/TypeScript/blob/main/LICENSE.txt). compiler 배포 범위와 notice |
| Vite | M0 선택 · 개발/build | MIT | [LICENSE](https://github.com/vitejs/vite/blob/main/LICENSE). build tooling과 배포 frontend 의존성을 각각 기록 |
| three.js | M0 선택 · 3D 객체 재생 | MIT | [LICENSE](https://github.com/mrdoob/three.js/blob/dev/LICENSE). 라이브러리와 별도로 사용하는 3D 모델·texture·font의 출처 확인 |
| DM Sans / Manrope / IBM Plex Mono | M0 포함 · 같은 origin에서 글꼴 제공 | 설치 package에 포함된 SIL Open Font License 1.1 | 각 `@fontsource` package 5.3.0 고정. [font 출처·버전](../frontend/README.md), 원문 copyright/LICENSE는 `frontend/public/licenses/`에 보존하며 build에 포함 |
| FastAPI | M0 선택 · Python API/OpenAPI | MIT | [LICENSE](https://github.com/fastapi/fastapi/blob/master/LICENSE). ASGI server, validation 등 실제 포함 dependency까지 기록 |
| SQLAlchemy | M0 선택 · DB 접근 | MIT 조건의 upstream LICENSE | [LICENSE](https://github.com/sqlalchemy/sqlalchemy/blob/main/LICENSE). DB driver 및 migration 도구는 별도 dependency |
| Pillow 12.3.0 | **센서 decode slice 구현** · runtime PNG/JPEG decode·RGB PNG encode | 본체 **MIT-CMU**. 설치 wheel의 통합 LICENSE에는 bundled codec/library notice도 포함 | [PyPI](https://pypi.org/project/pillow/12.3.0/), [보존한 전체 wheel notice](../third_party/licenses/pillow-12.3.0.txt). 16 MiB input·2M pixel 제한과 실제 decode 검증은 자체 코드에서 추가 |
| SQLite | M0 선택 · 로컬 metadata | upstream는 deliverable code·문서를 public domain으로 설명 | [Copyright](https://www.sqlite.org/copyright.html). 일부 build script가 다른 조건을 가질 수 있어 배포물 기준으로 구분 |
| PostgreSQL | 연결 후보 · 팀/상용 metadata | PostgreSQL License | [공식 License](https://www.postgresql.org/about/licence/). server image·driver·extension의 출처와 조건도 별도 기록 |

이 표는 전체 dependency SBOM이 아니다. UI chart/icon library, Python server/test 도구, transitive dependency가 추가되면 lockfile에서 실제 목록을 추출하여 release 단위 SBOM과 NOTICE를 만든다. 플랫폼 자체 코드의 라이선스는 upstream의 라이선스로 자동 결정되지 않는다.

## 시뮬레이션·로그·스택 도입 상태

| 구성요소 | 맡길 역할 / 상태 | 공식 근거와 라이선스 범위 | 첫 검증과 남은 판단 |
| --- | --- | --- | --- |
| **esmini** | 후속 engine 후보 · OpenSCENARIO XML 실행, OpenDRIVE 접근 | 공식 README는 XML player, RoadManager, ScenarioEngine/API, replay·OSI 도구를 설명한다. 코드 LICENSE는 **MPL-2.0**. [README](https://github.com/esmini/esmini), [LICENSE](https://github.com/esmini/esmini/blob/master/LICENSE) | release 고정, 지원 XML/XODR 요소 목록과 golden scenario 확인. third-party library·resource 조건을 별도 확인. 전체 표준 및 센서 정확도를 제공한다고 표시하지 않음 |
| **Eclipse SUMO** | 교통 흐름·traffic actor 후보 | microscopic traffic simulation. 프로젝트 README는 **EPL-2.0**, 보조 코드·library는 별도 조건이 있음을 명시한다. [README/License](https://github.com/eclipse-sumo/sumo) | 실제 배포 version의 LICENSE·NOTICE와 Python/TraCI dependency 확인. 시간 step·경로·차선·ego ownership 계약을 시험. photoreal sensor나 정밀 차량 동역학 역할로 취급하지 않음 |
| **CARLA** | 사용자가 선택한 **첫 외부 엔진** · 실험적 remote bridge 구현, 실제 server 검증 필요 | 공식 README는 CARLA 자체 코드 **MIT**, CARLA 자체 asset **CC-BY**, Unreal Engine은 별도 조건이라고 구분한다. [README의 Licenses](https://github.com/carla-simulator/carla#licenses) | engine branch·version·GPU/OS 요구사항 고정. 선택 asset의 CC-BY 정확한 version/attribution 확인. Unreal 및 FBX SDK 등 포함 dependency/integration을 별도 검토. 현재 macOS 로컬에서 실행된 것으로 가정하지 않음 |
| **ROS 2** | 스택/장비 messaging bridge 후보; Image/CompressedImage/PointCloud2의 고정 CDR1 payload 해석은 **센서 decode slice 구현** | ROS 2 전체를 단일 라이선스 묶음으로 취급하지 않는다. rclcpp는 **Apache-2.0**, 참조한 Jazzy sensor_msgs package도 **Apache-2.0** 선언. [rclcpp LICENSE](https://github.com/ros2/rclcpp/blob/rolling/LICENSE), [sensor_msgs package](https://github.com/ros2/common_interfaces/blob/jazzy/sensor_msgs/package.xml) | ROS/DDS runtime을 설치한 bridge와 구별. 고정 reader와 표준 fixture 정의를 자체 작성했다. QoS·DDS/RMW·ROS 폐루프·CameraInfo/TF 자동 연결·clock/calibration 검증은 후속 |
| **MCAP 1.4.0** | **기록 slice 구현** · Python writer/API와 별도 bounded ingestion parser, 원본·payload·메시지 DB | **MIT**. upstream commit `b33fa682a5c517b1d213faeabd118e0b4f9d9d93`. [고정 LICENSE](https://github.com/foxglove/mcap/blob/b33fa682a5c517b1d213faeabd118e0b4f9d9d93/LICENSE), [보존 원문](../third_party/licenses/mcap-1.4.0.txt) | `requirements.lock`에 고정. 정확한 ns·전체 메시지 탐색·JSON/opaque 조회와 별도 sensor decoder를 연결. 기본 reader의 크기/CRC 제한을 그대로 신뢰하지 않음. 모든 payload 포맷·video·clock/calibration 검증 완료를 뜻하지 않음 |
| **mcap-ros2-support 0.5.7** | **test/example-only** · 독립 ROS2 CDR fixture writer | **MIT**. release tag `releases/python/mcap-ros2-support/v0.5.7`, commit `b13eb43cec62fd812689759a54eadcb6a0c64bfd`. [고정 LICENSE](https://github.com/foxglove/mcap/blob/b13eb43cec62fd812689759a54eadcb6a0c64bfd/LICENSE), [보존 원문](../third_party/licenses/mcap-ros2-support-0.5.7.txt) | backend test extra와 fixture generator에 사용. production decoder는 임의 schema 코드 생성을 수행하지 않는 자체 고정 CDR1 reader. 이 package의 일반 decoder를 안전한 무제한 decoder로 취급하지 않음 |
| **Foxglove schemas** | **센서 decode slice 구현** · JSON RawImage/CompressedImage/PointCloud 표준 layout 참조 | upstream schema 프로젝트 **MIT**. [LICENSE](https://github.com/foxglove/schemas/blob/main/LICENSE), [공식 schema](https://docs.foxglove.dev/docs/sdk/schemas) | CARLA JSON schema 설명과 validation을 자체 작성. JSON bytes/base64·pose를 실제 해석하지만 전체 Foxglove schema 지원은 아님. proprietary viewer를 복사하거나 제품 전체의 license로 적용하지 않음 |
| **lz4 4.4.5 / zstandard 0.25.0** | **기록 slice 구현** · 제한된 단일 frame chunk 압축 해제 | 설치 wheel의 notice는 각각 **BSD-3-Clause** 조건. [LZ4 보존 원문](../third_party/licenses/lz4-4.4.5.txt), [Zstandard 보존 원문](../third_party/licenses/zstandard-0.25.0.txt) | frame header·실제 출력 크기·종료/추가 바이트 확인. native codec 포함 배포물의 조건은 별도 release inventory 필요. [notice 출처](../third_party/README.md), [압축 API 검토](mcap-format-notes.md) |
| **Open Simulation Interface 구현** | object/sensor model 간 표준 message 후보 | protobuf 기반 interface. 현재 upstream source LICENSE는 **MPL-2.0**. [공식 저장소](https://github.com/OpenSimulationInterface/open-simulation-interface), [LICENSE](https://github.com/OpenSimulationInterface/open-simulation-interface/blob/master/LICENSE) | 선택 OSI version, protobuf compiler/runtime, 생성 코드 배포 범위 확인. message 타입 사용만으로 의미·좌표·시간 호환이나 정확도를 보장하지 않음 |

MPL/EPL 구성요소의 수정·결합·배포 범위는 구체적인 제품 구성과 원문을 기준으로 검토한다. adapter나 별도 프로세스를 사용한다는 이유만으로 모든 의무가 사라진다고 가정하지 않는다. 조사 단계에서 상용 이용 가능/불가능을 일괄 판정하지 않는다.

MCAP 및 mcap-ros2-support wheel에 빠진 upstream LICENSE는 각 고정 commit에서 별도 보존했다. Pillow wheel notice는 bundled library 부분을 포함한 전체 원문을 보존했다. 현재 기록 기능과 오류·크기·decoder 경계는 [README](../README.md), [기록 데이터 사용법](data-recordings.md), [recording API 계약](recordings-contract.md), [sensor 형식 근거](sensor-format-notes.md)에 연결한다. [third_party](../third_party/README.md)는 이 dependency들의 notice 모음이며 전체 제품 SBOM이나 로그·schema·asset 데이터의 이용 허가가 아니다.

## ASAM 표준: 규격과 구현 라이선스를 구분

| 규격 | 실제 역할 | 프로젝트의 현재 상태 / 도입 기준 | 공식 출처 |
| --- | --- | --- | --- |
| OpenSCENARIO XML | 교통 참여자의 동적 행동·조건·동기화된 maneuver 표현 | 표준 검토. M0 JSON은 ASAM XML이 아님. esmini 통합 시 version별 subset·unsupported 목록·원본 보존·변환 보고서를 제공 | [ASAM XML](https://www.asam.net/standards/detail/openscenario-xml/) |
| OpenSCENARIO DSL | 재사용·조합 가능한 scenario language | 표준 검토. XML과 병행되는 별도 규격으로 관리. DSL parser/compiler와 concretization engine을 선택한 후 계약 검증 | [ASAM DSL](https://www.asam.net/standards/detail/openscenario-dsl/) |
| OpenDRIVE | 도로 geometry·lane·signal 등의 XML road network | 표준 검토. 3D texture·실측 digital twin과 구분. importer의 geometry·연결·고도·좌표·version 지원을 시험 | [ASAM OpenDRIVE](https://www.asam.net/standards/detail/opendrive/) |
| ASAM OSI | GroundTruth·SensorData·SensorView·TrafficCommand 등의 모델 간 교환 | 표준 검토. 선택 interface/version·필수 필드·semantic mapping을 adapter 계약으로 명시 | [ASAM OSI](https://www.asam.net/standards/detail/osi/) |

ASAM 사이트가 규격을 무료로 내려받을 수 있다고 설명하는 것과, 규격 문서·schema·예제·구현 코드를 제품에 어떤 조건으로 포함할 수 있는지는 별개다. ASAM의 일반 약관과 다운로드한 package 내 적용 조건을 확인한다. OSI 등 공개 프로젝트의 source license도 프로젝트별로 확인한다. [ASAM License Terms](https://www.asam.net/license/), [ASAM OSI의 프로젝트별 라이선스 설명](https://www.asam.net/standards/detail/osi/)

같은 OpenSCENARIO 입력이라도 엔진 간 결과는 달라질 수 있다는 점을 ASAM 공식 XML 설명에서도 명시한다. 따라서 표준 import 성공, adapter 계약 통과, 모델의 실측 상관성은 각각 별도의 검증 결과로 관리한다. [ASAM XML](https://www.asam.net/standards/detail/openscenario-xml/)

## asset·dataset·model 출처 기록

라이브러리 코드가 허용되는 조건으로 배포되어도 함께 쓰는 지도, 사진, mesh, texture, font, 실차 log, pretrained weights의 조건이 같다고 가정하지 않는다. 출처가 확인되지 않은 파일은 검증 근거를 가진 기본 제품 asset으로 승격하지 않는다.

각 외부 산출물에 다음 항목을 저장한다.

| 필드 | 기록 내용 |
| --- | --- |
| identity | artifact ID, 원본 URL, 공급자, 파일명, 내려받은 날짜, 선택 revision |
| integrity | SHA-256, byte size, format/schema version |
| rights | license 이름/version, 원문 위치, attribution, 배포/학습/파생 사용 검토 상태 |
| provenance | 원본→변환/가공/생성 관계, 사용한 도구·parameter·version |
| quality | 실측/추정/합성 구분, 좌표·단위·시간, 검증 방법과 confidence |
| scope | 적용 가능한 센서/ODD/지도 영역, 알려진 한계 |
| owner | 검토 주체, 승인/보류 상태, 재검토 사유 |

건축 사진을 참고했다는 사실은 그 사진을 texture로 배포할 권리나 정확한 건물 치수를 얻었다는 뜻이 아니다. 지도·asset 사실은 주소·영역·측정 근거와 연결하고, 미확인 부분은 추정으로 남긴다.

## 배포 revision을 고정할 때

1. 선택한 코드 release/commit·package version·image digest를 고정한다.
2. 해당 revision의 LICENSE·NOTICE와 dependency 목록을 보존한다.
3. mesh·texture·map·dataset·weights를 코드와 별도 목록으로 만든다.
4. 수정한 upstream 파일·patch·source 제공 범위를 기록한다.
5. adapter 지원 feature/version과 실제 테스트 환경을 기록한다.
6. SBOM·NOTICE 및 고객 배포 구성의 검토 결과를 release artifact에 연결한다.

이 단계를 완료한 뒤에만 특정 release에 대한 도입 상태를 확정한다. 후보 조사 결과를 제품 전체의 포괄적인 법적 승인이나 기능 보증으로 표현하지 않는다.

## Optional recorded-image inference · 2026-09-13

| Component | Pinned use | License/source boundary |
| --- | --- | --- |
| ONNX 1.22.0 | Model protobuf/checker in optional child; fixture graph writer | [Apache-2.0 and notices](https://github.com/onnx/onnx/blob/v1.22.0/LICENSE) |
| ONNX Runtime 1.30.0 | Actual CPUExecutionProvider execution | [MIT](https://github.com/microsoft/onnxruntime/blob/v1.30.0/LICENSE); bundled notices preserved separately |
| NumPy 2.5.3 / OpenCV-headless 5.0.0.93 | Tensor layout, explicit RGB resize and output processing | Exact wheel licenses/third-party notices preserved in the [notice manifest](../third_party/licenses/perception/manifest.json) |
| protobuf 7.36.1 / ml_dtypes 0.6.0 / FlatBuffers 25.12.19 | ONNX runtime transitive dependencies | Exact wheel/upstream notices, not a blanket license for uploaded models |
| OpenCV Zoo YOLOX-S FP32 | Optional explicit downloaded trained model, official SHA and source commit pinned | [Directory license](https://github.com/opencv/opencv_zoo/blob/47534e27c9851bb1128ccc0102f1145e27f23f98/models/object_detection_yolox/LICENSE), [model/source rights notes](perception-model-notes.md); model weights absent from Git |
| Original red-pixel ONNX / RGB MCAP fixture | Actual input-dependent contract/UI checks | Project-authored pixel rule and images; not trained semantic perception; platform distribution license remains undecided |

No COCO image is fetched or redistributed by these examples. Model distribution,
code notices and training-image rights remain separate. This inventory supports
review and reproducibility; it is not completion of the commercial release SBOM.

## Optional COCO bbox evaluator · 2026-09-13

`pycocotools==2.0.11` from the [official PyPI release](https://pypi.org/project/pycocotools/2.0.11/)
and its pinned source commit `ac87f5077ad6b8864c2dc5e93d14cae62d1db05a` is
called in an optional child process. The BSD 2-clause/FreeBSD-disclaimer
[exact upstream notice](../integrations/coco/third_party/pycocotools-license.txt)
is preserved. NumPy2.5.3 notices are already recorded above. See
[source/metric notes](detection-evaluation-notes.md) for version provenance,
COCO bbox cap/crowd/area behavior and limits of scoring saved pipeline predictions.
No COCO annotations or images are bundled. Evaluator code, authored fixture
labels and third-party dataset rights remain distinct release inventory items.

## VAD 연구 연결 확인 · 2026-10-04

[VAD code LICENSE](https://github.com/hustvl/VAD/blob/main/LICENSE) Apache-2.0를 확인하고
`1688c4b1c3a9e2e7873ca9700ff8058170c0e3c8` source를 `.runtime` 연구 경로에 고정했다.
Code 조건과 공개 checkpoint 권한을 합치지 않는다. checkpoint의 상용 권한은 미확정이고
[nuScenes](https://www.nuscenes.org/nuscenes)의 데이터는 기본 비상업용/상업용 별도 문의다.
데이터·가중치를 내려받거나 기본 패키지에 포함하지 않았다. [현재 실행 경계](driving-policies.md).


2026-10-05: User explicitly approved nuScenes use only for isolated noncommercial research.
Official mini/CAN archives and the VAD README-linked Tiny checkpoint are kept in the remote research
workspace; none is shipped in the product package. Archive and checkpoint hashes, source links and
actual execution are in `validation/vad-native-acceptance-20261005.json`. This research acceptance does
not grant commercial dataset/checkpoint usage or redistribution rights.

## FPGA Lab · 2026-10-13

- **실제 도입:** `@xyflow/react==12.10.1` (MIT) 고정 블록 UI,
  `simpy==4.1.1` (MIT) 선택형 CPU 이산 사건 adapter. 기존 Recharts로 계측 표시.
  설치 배포물의 원본 notice는 `third_party/licenses/react-flow-12.10.1.txt`와
  `third_party/licenses/simpy-4.1.1.txt`에 보존했다.
- **직접 구현:** RAW 크기·DDR/FIFO·스케줄러·링크·ECU 타이밍 모델, 불변 개정,
  실행 근거·사건 trace·A/B·SDK. [기능과 검증 경계](fpga-lab.md).
- **미포함:** vendor RTL/IP·bitstream·부품 모델·CSI-2 규격 구현·실제 RAW 데이터.
  합성 패턴은 자체 정의다. OSS 사용 권한을 표준·부품·장비·데이터 권리로 확대하지 않는다.

## Apollo Camera Lab optional adapter (2026-10-14)

`integrations/apollo_lab` pins NumPy 2.5.3 (BSD), OpenCV headless 5.0.0.93
(Apache-2.0 and bundled third-party notices), Pillow 12.3.0 (HPND), and SimPy 4.1.1 (MIT).
NumPy/OpenCV notices are indexed by `third_party/licenses/perception/manifest.json`;
Pillow and SimPy notices are `third_party/licenses/pillow-12.3.0.txt` and
`third_party/licenses/simpy-4.1.1.txt`. Internal FPGA adapter is reused separately.
These code licenses do not grant rights to Qualcomm documentation/IP, uploaded RAW data,
standards, assets or hardware. Synthetic test imagery is generated locally; vendor PDF content
is not redistributed by the Lab. No proprietary Qualcomm ISP algorithm is included.

## FPGA RTL Lab (2026-10-14)

- Verilator 5.052 was installed from Homebrew and executed locally as an optional external
  compiler/simulator. Installed README and runtime headers declare
  `LGPL-3.0-only OR Artistic-2.0`; retained installed notices are
  `third_party/licenses/verilator-5.052.txt` and `verilator-5.052-readme.rst`.
- SimPy 4.1.1 is pinned in the optional adapter; its MIT notice is retained separately.
  FIFO RTL and C++ harness are project-authored reference implementations, not vendor IP.
- Runtime/binary redistribution, user RTL rights, standards and physical hardware are separate
  from this local integration. No Qualcomm implementation or competitor UI/code is bundled.

## FPGA Workbench (2026-10-14)

- Reuses the pinned React Flow 12.10.1 editor dependency, SimPy 4.1.1 and optional Verilator
  5.052 integration and their retained notices. No additional third-party engine is bundled.
- Graph validation, generated SystemVerilog top-level wiring, C++ harness, virtual instruments
  and the serial-FIFO SimPy model are project-authored. The provided reference FIFO is reused
  from the RTL Lab; neither Qualcomm RTL nor LabVIEW code/assets are included.

## Engineering Lab (2026-10-14)

- ngspice 47 is installed locally from Homebrew as an optional executable. Its installed
  `COPYING` is retained as `third_party/licenses/ngspice-47.txt`; the notice identifies a
  Modified BSD baseline and component-specific licenses (including LGPL, MPL and GPL parts).
  No ngspice executable, third-party SPICE device library or vendor model is bundled here.
- The fixed Stream32 harness reuses locally installed Verilator 5.052. User RTL source rights
  remain separate from the adapter, its reference examples and the simulator license.
- Only self-contained R/C/L/V/I/diode models are accepted by this initial analog adapter;
  external model/code loading is disabled. The reference netlists are project-authored.
- Qualcomm architecture PDFs remain in their original study folder. No Qualcomm runtime,
  firmware, RTL or licensed ISP algorithm is copied or represented as connected by this feature.

## Open ISP RTL (2026-10-17)

- Six AMD mini-isp SystemVerilog blocks are vendored from commit
  `67e752d6bf668179ec45446089a709bbc1ef9736` under MIT. The source file notices,
  `LICENSE.md`, `REUSE.toml` and URL/content-hash manifest are preserved under
  `integrations/isp_rtl/src/axiom_isp_rtl/vendor/mini_isp`. Generated design exports
  retain the license and source provenance. No AMD-specific proprietary IP is included.
- The four-channel ADAS streaming design adds AXIOM FIFO/control wrappers and
  explicitly named generated `axiom_adas_demosaic.sv` ready/line-output register
  changes. `axiom_adas_colorgain.sv` separates full-product registration and
  saturation across the existing gain stages without changing integer arithmetic.
  Upstream files remain unchanged; original and derived source hashes, change
  descriptions and MIT notices travel together in each design export. Arithmetic
  equivalence is checked against an independent spatial integer reference.
- Local optional Yosys, Slang and nextpnr/Trellis come from the official OSS CAD
  Suite2026-10-16 macOS ARM64 distribution. Executables are not bundled or
  redistributed by AXIOM; their separate component licenses remain with that
  installed distribution. The suite build repository's license is not treated
  as a license for all its binaries. Verilator, NumPy and Pillow reuse the
  previously registered optional runtime dependencies.
- Qualcomm architecture documents supply structural context only and are not
  redistributed. No Qualcomm RTL/runtime/algorithm is included. Uploaded RAW,
  vendor EDA licenses, standards and board assets retain separate rights.
- Gowin Standard and Quartus source-project templates are authored text, not
  bundled vendor software or IP. Neither template is a completed vendor build.

### ADAS pre-board verification additions · 2026-10-17

- Generated `axiom_adas_blc.sv` explicitly zero-extends the RAW12 working wire;
  the untouched pinned original remains the four-state X/Z negative control.
  The generated linebuffer derivative registers cascade writes as a unit while
  retaining upstream MIT notices and original/derived hashes.
- Icarus Verilog 13.0 is an optional locally installed native compiler/runtime.
  Its GPL-2.0-or-later compiler/runtime distribution is not bundled by AXIOM;
  actual tool and installed backend hashes are recorded in check artifacts.
- The ULX3S 85F example records official `emard/ulx3s` commit
  `6a92cec6b177191c5b0f80e260013a1f8ec147dd` README and constraint hashes/URLs.
  Only a small control-pin fact subset and our example profile are included;
  no schematic/PCB files or board certification claims are redistributed.
  The EHXPLLL wrapper is generated locally by `ecppll` from the existing OSS CAD
  Suite; generation does not establish physical clock or board timing validation.

### Virtual ECU native network · 2026-10-17

- Vector [SIL Kit5.0.8](https://github.com/vectorgrp/sil-kit/releases/tag/v5.0.8): MIT;
  pinned Ubuntu24.04 x86_64 SDK archive SHA and extracted runtime hashes travel with
  the native build manifest. MIT and ThirdParty notices retained. Functional
  CAN/Ethernet service is not a physical arbitration/PHY timing model.
- [autoas/as](https://github.com/autoas/as/tree/cc3709675ac30f7539c0710dfec6d579a5235841):
  pinned Com/PduR/CanIf source subset, original license plus complete GPLv3 text.
  This internal evaluation selects the GPLv3 option of upstream's GPLv3/commercial
  dual license. ASKAR is not reused. AXIOM configuration/host driver are separate.
- [Trampoline](https://github.com/TrampolineRTOS/trampoline/tree/ff287023252bf73273067f00d2dbc8bbc9a20401):
  upstream root GPLv2 text and individual GPL/LGPL notices retained. The authored
  participant is GPL-2.0-or-later. Explicit POSIX external-clock patch, original
  archive, GOIL/generated configuration and binary hashes are preserved. This
  build enables OSEK services, WITH_AUTOSAR=NO.
- nlohmann/json3.11.3: MIT notices retained independently by the BSW/controller
  and transport build packages. Existing C++ traffic controller is preserved
  with an explicit two-line network-sequence/version patch.
- Process separation does not settle binary distribution obligations. These
  components are an internal public-stack evaluation, not proprietary Silver
  code/UI or production AUTOSAR. CARLA assets keep their existing terms.
