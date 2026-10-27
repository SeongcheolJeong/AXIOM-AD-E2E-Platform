# Sensor payload format notes

확인·구현 기준일: **2026-09-13**. 현재 지원은 실제 메시지 bytes의 제한된 해석과 preview 생성이다. 실측 센서 정확도, camera calibration, 시간 동기화, perception 성능을 검증했다는 의미가 아니다. API와 binding 규칙은 [sensors-contract.md](sensors-contract.md), 컨테이너·index 경계는 [recordings-contract.md](recordings-contract.md)를 따른다.

## 실제 지원 형식

| Decoder / version | MCAP schema / encoding | 실제 처리 |
| --- | --- | --- |
| `foxglove-json` / `1` | `foxglove.CompressedImage`, `jsonschema` + `json` | base64 PNG/JPEG를 검증·decode하여 RGB PNG 생성 |
| `foxglove-json` / `1` | `foxglove.RawImage`, `jsonschema` + `json` | `rgb8`, `bgr8`, `rgba8`, `bgra8`, `mono8`, `8UC1`, `8UC3`; row padding을 포함한 실제 pixel bytes 해석 |
| `foxglove-json` / `1` | `foxglove.PointCloud`, `jsonschema` + `json` | base64 packed fields, scalar FLOAT32/FLOAT64 XYZ, 선택 intensity. 메시지 pose를 적용하여 `frame_id` 좌표의 XYZ 생성 |
| `ros2-cdr` / `1` | `sensor_msgs/msg/Image`, `ros2msg` + `cdr` | 고정 표준 구조의 CDR1 BE/LE; `rgb8`, `bgr8`, `rgba8`, `bgra8`, `mono8`, `8UC1` 해석 |
| `ros2-cdr` / `1` | `sensor_msgs/msg/CompressedImage`, `ros2msg` + `cdr` | 명시된 PNG/JPEG와 표준 `ORIG; CODEC compressed [PIXEL]` 표현 해석 |
| `ros2-cdr` / `1` | `sensor_msgs/msg/PointCloud2`, `ros2msg` + `cdr` | organized/unorganized 데이터, point/row stride, field offset/count, point buffer의 독립 BE/LE 처리 |

Foxglove에는 이 용도의 범용 `foxglove.Image` schema를 가정하지 않는다. 정확한 이름과 binary 필드의 base64 선언을 확인한다. 위 지원은 공식 포맷의 부분집합이다. Foxglove PointCloud가 허용하는 2D 좌표만 있는 cloud는 현재 XYZ preview에서 거절한다. 공식 [RawImage](https://docs.foxglove.dev/docs/sdk/schemas/raw-image), [CompressedImage](https://docs.foxglove.dev/docs/sdk/schemas/compressed-image), [PointCloud](https://docs.foxglove.dev/docs/sdk/schemas/point-cloud), [JSON PointCloud schema](https://raw.githubusercontent.com/foxglove/schemas/main/schemas/jsonschema/PointCloud.json)를 기준으로 한다.

ROS 구조는 root뿐 아니라 `std_msgs/Header`, `builtin_interfaces/Time`, PointCloud2의 `PointField` 의존 정의까지 검사한다. `/msg/`가 있는 dependency 이름과 없는 이름은 동일하게 정규화하고, field 순서·타입·dependency가 달라지면 거절한다. CDR 구조와 point buffer endianness는 별개다. [ROS2 Image](https://docs.ros.org/en/ros2_packages/jazzy/api/sensor_msgs/msg/Image.html), [CompressedImage](https://raw.githubusercontent.com/ros2/common_interfaces/jazzy/sensor_msgs/msg/CompressedImage.msg), [PointCloud2](https://raw.githubusercontent.com/ros2/common_interfaces/jazzy/sensor_msgs/msg/PointCloud2.msg), [PointField](https://raw.githubusercontent.com/ros2/common_interfaces/jazzy/sensor_msgs/msg/PointField.msg).

`8UC3`의 의미를 두 형식에서 섞지 않는다. Foxglove RawImage는 BGR 별칭으로 명시하지만, ROS의 generic 3-channel 표현으로는 RGB/BGR 순서를 확정하지 않으므로 ROS 경로는 거절한다. ROS 색상 이미지는 `rgb8`/`bgr8`처럼 순서를 명시해야 한다.

## 크기와 변환 경계

- 입력 메시지 **16 MiB**, image **2,000,000 pixels**, source cloud **1,000,000 points**, fields **64개**를 한도로 한다. schema는 hash·size가 맞는 완전한 UTF-8 원문 **64 KiB 이하**가 필요하며 truncated preview로 decoder를 만들지 않는다.
- JSON은 중복 key, 비표준 non-finite constant, 잘못된 base64, 16단계를 넘는 nesting과 4,096개를 넘는 구조 token을 거절한다. CDR는 string/sequence 길이와 남은 bytes를 확인한 뒤 읽는다. 임의 schema를 Python 코드로 실행하거나 동적 message decoder로 생성하지 않는다.
- PNG는 chunk CRC와 framing을 검사한다. 압축 text/profile metadata를 Pillow에 전달하기 전에 제거하여 metadata 팽창을 피한다. pixel dimensions를 확인한 뒤 실제 decode하고, animation·잘못된 이미지·codec 불일치는 거절한다. 출력은 RGB8 PNG이며 alpha·원본 EXIF/profile은 보존하지 않는다. 원본 메시지 bytes는 별도로 유지한다.
- point preview는 기본 **20,000 points**, 요청 최대 **100,000 points**다. source ordinal에 대한 일정 stride로 선택하고, 비유한 값 및 float32 범위를 넘는 좌표는 제외한다. original/rendered/invalid/skipped count를 구분한다. intensity는 선택된 points와 같은 순서의 별도 float32 LE buffer이며, 선택된 intensity에 비정상 값이 있으면 intensity preview 전체를 생략한다.
- XYZ output은 **float32 LE, point stride 12 bytes**다. PNG·XYZ·intensity 각각의 bytes에 SHA-256/size를 붙이고 원본 payload SHA-256, decoder ID/version, frame, acquisition timestamp, 변환 정책도 반환한다. 이 한도는 입력·출력·반복량의 제품 제한이며 프로세스 RSS 상한이나 codec sandbox를 보장하지 않는다.

현재 Bayer, depth, YUV, packed16 image, WebP/AVIF/TIFF, video, compressed point cloud/Draco, ROS1, IDL, CDR2/parameter-list CDR, custom ROS messages는 해당 built-in decoder에서 해석하지 않는다. generic MCAP importer의 원문 보관·opaque 탐색과 sensor decode 지원 여부는 별개다.

## 좌표·시간·calibration

CARLA sensor는 Unreal의 local **x-forward/y-right/z-up** 좌표를 반환한다. ROS의 기본 body convention은 right-handed **x-forward/y-left/z-up**, camera optical frame은 **x-right/y-down/z-forward**다. CARLA→ROS body의 좌표 변환은 `(x,-y,z)`이며, camera optical 변환은 `(y,-z,x)`다. Handedness 변경은 반사 변환을 포함하므로 임의 quaternion 성분 하나를 바꾸는 방식으로 일반화하면 안 된다. [CARLA sensor reference](https://carla.readthedocs.io/en/latest/ref_sensors/), [ROS REP-103 원문](https://raw.githubusercontent.com/ros-infrastructure/rep/master/rep-0103.rst).

Decoder는 LiDAR frame 이름만으로 실제 축을 단정하지 않는다. Foxglove의 `pose`는 message frame에 대한 point origin이므로 실제 좌표에 적용하고 `message_pose_to_frame`과 원래 pose를 보존한다. rig transform은 decoder가 적용하지 않는다. ROS2 PointCloud2도 optical frame일 수 있으므로 항상 z-up으로 표기하지 않는다. rig의 frame/convention/transform은 사용자가 고정한 source declaration이며 실제 장착 검증과 구별한다.

MCAP `log_time`은 기록 시각, `publish_time`은 발행 시각이다. payload의 Foxglove timestamp 또는 ROS Header stamp는 따로 보존한다. Foxglove timestamp는 사용자 정의 epoch의 uint32 seconds/nanoseconds이고, ROS Time의 signed seconds도 임의 UTC 변환 없이 정수 ns 문자열로 유지한다. bundle 선택 기준은 **log time + 선언된 offset**이며 acquisition clock이 같다는 증거가 아니다. 이전 관측만 선택하고 미래 프레임으로 대체하지 않으며, 최대 age가 없으면 freshness는 unknown이다. [MCAP Message](https://mcap.dev/spec#message-op0x05), [Foxglove Timestamp](https://docs.foxglove.dev/docs/sdk/schemas/timestamp).

현재 CameraInfo/CameraCalibration/TF를 자동 해석하여 rig를 생성하지 않는다. 수동 intrinsics·정적 transform 선언을 revision으로 고정하는 경로가 있다. 이미지 크기, frame ID, distortion model, calibration 유효 시각과 좌표 방향을 확인하지 않고 point projection을 표시해서는 안 된다. FOV로 계산한 pinhole K는 실제 calibration 측정값으로 격상하지 않는다. [CameraCalibration](https://docs.foxglove.dev/docs/sdk/schemas/camera-calibration), [ROS2 CameraInfo](https://raw.githubusercontent.com/ros2/common_interfaces/jazzy/sensor_msgs/msg/CameraInfo.msg).

CARLA의 RGB bytes는 BGRA 출력에서 RGB PNG로 변환되며 센서 RAW/ISP 결과가 아니다. sensor timestamp는 episode 시작 이후의 double seconds다. 현재 CARLA MCAP writer는 실제로 포착한 대표 frame만 저장하고, 보고된 double을 ns로 반올림한 정책과 원래 시각을 manifest에 남긴다. 원본 local LiDAR와 변환된 MCAP LiDAR를 같은 좌표로 취급하면 안 된다. 실 CARLA 서버에서의 capture·frame synchronization 검증은 남은 인수 조건이다.

## OSS 의존성과 확인 근거

| 구성 | 이 구현에서의 사용 | 확인된 코드 라이선스 |
| --- | --- | --- |
| Pillow `12.3.0` | runtime PNG/JPEG 실제 decoding/PNG encoding | [PyPI MIT-CMU 표기](https://pypi.org/project/pillow/), [공식 프로젝트](https://pillow.readthedocs.io/en/stable/about.html) |
| mcap-ros2-support `0.5.7` | test-only 독립 public Writer/CDR fixtures | [배포 버전](https://pypi.org/project/mcap-ros2-support/0.5.7/), [MIT 원문](https://raw.githubusercontent.com/foxglove/mcap/main/LICENSE) |
| Foxglove schemas | 표준 message layout 참조 | [MIT 원문](https://raw.githubusercontent.com/foxglove/schemas/main/LICENSE) |
| ROS2 sensor_msgs | 표준 layout 참조 | [Jazzy package의 Apache-2.0 선언](https://raw.githubusercontent.com/ros2/common_interfaces/jazzy/sensor_msgs/package.xml) |

upstream `mcap_ros2.DecoderFactory`는 `ros2msg`에서 동적 decoder를 생성하며 IDL은 지원하지 않는다. 일반 CDR reader는 배열 길이에 따른 반복과 slicing을 수행한다. 따라서 이 서비스의 엄격한 길이·layout 제한을 위해 고정 CDR1 reader를 사용하고 upstream writer를 독립 fixture 생성에 사용한다. [공식 decoder API](https://mcap.dev/docs/python/mcap-ros2-apidoc/mcap_ros2.decoder), [공식 decoder source](https://raw.githubusercontent.com/foxglove/mcap/main/python/mcap-ros2-support/mcap_ros2/decoder.py), [CDR source](https://raw.githubusercontent.com/foxglove/mcap/main/python/mcap-ros2-support/mcap_ros2/_cdr.py).

코드 라이선스와 별개로 이미지·로그·차량·지도·엔진·dataset의 사용 및 배포 권리는 각각 확인해야 한다. 표준 message 형식을 지원한다는 사실이 입력 데이터나 simulator asset의 권리를 부여하지 않는다.
