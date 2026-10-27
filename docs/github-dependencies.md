# 선택 실행 환경과 외부 의존성

**CARLA를 별도로 설치하는 방식은 맞다. 그러나 CARLA만 제외하면 모든 기능이 바로 실행되는 것은 아니다.** 기본 UI·DB·참조 계산은 CPU 환경에서 시작할 수 있고, 아래 기능은 각각 선택 runtime이 필요하다. adapter가 설치되었거나 설정값이 있다는 사실만으로 실행 준비 완료를 표시하지 않는다.

## 기능별 준비 범위

| 기능 | 저장소에서 설치할 패키지 | 별도 준비물과 실행 범위 |
| --- | --- | --- |
| UI·로그 조사·Case·참조 실행 | `backend`, `sdk/python`, `frontend` | Python·Node·SQLite. 지원 MCAP를 직접 제공하며, 참조 계산은 센서/안전 검증이 아니다. |
| CARLA Object/Sensor/Software-DUT | `integrations/carla` | Linux GPU의 CARLA 서버, 같은 빌드의 Python client, 선택 경로의 worker/SSH 설정, 해당 지도. adapter 버전과 CARLA 버전은 별개다. |
| Neural Sim | `integrations/neural` | 별도 NVIDIA/CUDA/Torch/gsplat/Nerfstudio 환경, 모델·입력 로그, 저장 checkpoint와 remote dataset. 로컬 adapter 설치는 GPU runtime 설치가 아니다. |
| 기록 영상 ONNX / COCO 평가 | `integrations/onnx`, `integrations/coco` | 현재 ONNX는 CPUExecutionProvider만 지원한다. 등록 모델·가중치·라벨·데이터는 별도 공급한다. |
| OpenDRIVE 작성 / NGII SHP 변환 | `integrations/opendrive`, `integrations/geospatial` | 고정 pyproj/pyshp/Shapely/scenariogeneration과 사용자 원본. 변환 성공, CARLA 지도 load/readback, 주행 판정은 별개다. |
| FPGA 행동 타이밍 | `integrations/fpga_lab` | SimPy. 행동 시간 모델이며 RTL·전기·실물 FPGA 검증이 아니다. |
| RTL / FPGA workbench / 회로 | `integrations/rtl_lab`, `integrations/fpga_workbench`, `integrations/engineering_lab` | RTL은 Verilator와 C++ compiler, 회로는 ngspice. FPGA bitstream은 추가 합성·배치배선 도구와 board profile이 필요하다. |
| vECU / Network / HIL gateway | `integrations/vecu`, `integrations/vecu_network`, `integrations/hil_gateway` | native build, SIL Kit, autoas BSW, Trampoline OS, Linux SocketCAN/vCAN·네트워크 설정, 해당 실행 profile. 실제 ECU나 hard real-time 보증을 포함하지 않는다. |
| CameraE2E RAW/ISP | `integrations/camera-e2e` | 별도 CameraE2E 저장소·서버·원본 RAW/ISP 결과와 API 계약. 이 저장소의 connector는 외부 구현·DB·보정자료를 포함하지 않는다. |

## CARLA: Linux GPU에 따로 설치

공식 0.9.16 설치 안내는 Ubuntu 20.04/22.04, Python 3.7–3.12, NVIDIA 2070급 이상의 GPU와 VRAM 8 GB 이상, 약 20 GB의 엔진 공간을 제시한다. 녹화·학습·추가 지도 공간은 따로 잡는다. 이 프로젝트에서 새 환경을 준비할 때는 **Linux x86-64 + Python 3.12 + 선택한 CARLA 배포본**을 한 profile로 기록한다. Mac UI 호스트에 libcarla를 설치할 필요는 없다. [공식 설치 안내](https://carla.readthedocs.io/en/0.9.16/start_quickstart/)

1. [공식 release](https://github.com/carla-simulator/carla/releases)에서 사용할 서버 버전을 직접 선택한다. 공식 0.9.16 문서에도 다운로드 예시가 0.9.15를 가리키는 부분이 있으므로 `latest`라는 표현만으로 버전을 결정하지 않는다.
2. 서버 배포본의 `PythonAPI/carla/dist`에서 Python·OS에 맞는 wheel을 별도 환경에 설치한다. 공식 0.9.16 PyPI에는 Linux x86-64/CPython 3.12 wheel이 있지만, custom/nightly 서버에는 **그 서버에 동봉된 wheel**을 사용한다. [공식 wheel 목록](https://pypi.org/project/carla/0.9.16/)
3. 서버·wheel·지도 hash, GPU/driver, adapter source와 실행 profile을 기록한다. 같은 `0.9.16` 파일명이어도 실제 빌드가 다르면 이 저장소의 검사에서 거절된다.

GPU 호스트에서 운영자가 준비한 CARLA 디렉터리의 실행 예:

```sh
./CarlaUE4.sh -RenderOffScreen -carla-rpc-port=2000
```

화면 없는 렌더링은 카메라 생성을 유지한다. `no_rendering_mode=True`는 카메라/GPU 센서에 빈 데이터를 반환하므로 RGB 캡처의 대체 설정으로 쓰지 않는다. 기본 RPC/streaming은 2000/2001이며, 실행 profile에 따라 추가 포트를 사용한다. 외부 공개 대신 같은 호스트 또는 명시적인 터널 안에서 연결한다. [공식 렌더링 설명](https://carla.readthedocs.io/en/0.9.16/adv_rendering_options/)

선택한 client 환경에서 아래 읽기 전용 확인을 수행한다. 이것은 실제 실행 인수가 아니다.

```python
import carla
client = carla.Client("127.0.0.1", 2000)
client.set_timeout(10.0)
client_build = client.get_client_version()
server_build = client.get_server_version()
print({"client": client_build, "server": server_build})
assert client_build == server_build, "CARLA client/server build mismatch"
print(client.get_world().get_map().name)
```

플랫폼 측 adapter는 `.venv/bin/python -m pip install -c requirements.lock -e integrations/carla`로 설치한다. 기본 remote adapter는 `ADE2E_CARLA_WORKER_URL`과 인증 token으로 별도 HTTP worker에 연결한다. Object 도시/외부 지도·multisensor·stunt 등의 native 경로는 각자의 명시적 실행 profile이 더 필요하다. **기본 HTTP worker 연결만으로 모든 Object/Sensor 기능이 준비되지는 않는다.** API와 큐 worker에 같은 설정·source를 적용하고, 소유권·지원 capability를 확인한 뒤 대표 실행과 원본 artifact 재생을 인수한다. 상세 계약은 [CARLA integration](../integrations/carla/README.md)을 참고한다.

**CARLA 엔진·Unreal Engine·지도/mesh/texture는 이 소스 저장소에 묶지 않는다.** 기본 packaged server 사용에는 Unreal 편집기 소스 빌드가 필요 없다. 엔진 수정이나 custom asset cook에는 별도 CARLA용 Unreal fork, 계정 접근 권한, toolchain과 큰 빌드 공간이 필요하다. UE4 계열의 공식 Linux 빌드 문서는 약 130 GB 공간과 수정된 UE4.26 fork를 안내한다. 일반 Unreal 설치로 대체할 수 있다고 가정하지 않는다. [공식 소스 빌드 절차](https://carla.readthedocs.io/en/0.9.15/build_linux/)

## 나머지 선택 패키지

아래는 선택한 기능만 설치하는 예다. 한꺼번에 모두 설치할 필요가 없다.

```sh
# 기록 영상의 CPU 추론·평가: Python 3.12
.venv/bin/python -m pip install -c requirements.lock -e integrations/onnx -e integrations/coco

# 지도 작성·NGII 형식 변환: 원본 데이터 취득은 별도
.venv/bin/python -m pip install -c integrations/opendrive/requirements.lock -e integrations/opendrive -e integrations/geospatial

# 행동 타이밍과 RTL/회로 adapter: native 실행 도구는 별도
.venv/bin/python -m pip install -e integrations/fpga_lab -e integrations/rtl_lab -e integrations/fpga_workbench -e integrations/engineering_lab

# 외부 runtime을 연결하는 adapter만 설치
.venv/bin/python -m pip install -e integrations/neural -e integrations/camera-e2e
```

- **Neural:** 현재 adapter 문서의 기준은 Nerfstudio 1.1.5·gsplat 1.4.0이며, 호환 Torch/CUDA/compiler/driver를 별도 고정한다. `AXIOM_NEURAL_NATIVE_CONFIG`에 launcher와 환경 hash를 등록한다. 학습에는 추가 GPU 메모리가 필요하며 CARLA와 동시 실행 가능하다고 추정하지 않는다. published checkpoint만으로 remote dataset·config를 복원할 수 없는 현재 제약도 있다. [Neural 설치 계약](../integrations/neural/README.md)
- **ONNX:** `requirements.lock`에 버전이 있어도 모델 가중치를 동봉한다는 뜻이 아니다. 현재 bounded 이미지 검출 계약과 운영자 신뢰 모델 범위를 따른다. 프로젝트의 pixel-rule 예제는 학습된 도로 검출기가 아니다. [ONNX runtime](../integrations/onnx/README.md)
- **지도:** NGII 원본 SHP ZIP의 CRS·높이·차로 연결·권리를 제공해야 한다. 좌표계·폭·분기·고도를 모르면 자동으로 그럴듯한 도로를 생성하지 않는다. 현재 NGII converter의 fixture 검사는 실제 취득 NGII 도로 인수와 구별한다. [지원 형식·제한](../integrations/geospatial/README.md)
- **RTL/SPICE:** `verilator --version`, `clang++ --version` 또는 `g++ --version`, `ngspice -v`로 도구를 확인한다. 사용자 RTL은 `ADE2E_ENABLE_LOCAL_RTL=1`의 신뢰된 local non-team 환경에서만 활성화한다. 모델 출력·board bitstream·실물 장비 I/O를 같은 검증으로 표시하지 않는다. [Engineering Lab](engineering-lab.md)
- **CameraE2E:** 현재 retained ISP는 `AXIOM_CAMERA_E2E_URL`, lens/scene은 `AXIOM_CAMERA_SCENE_URL`로 외부 서비스를 연결한다. 해당 경로의 token/API 버전/원본 hash를 확인한다. 호환 서비스·RAW·보정 DB가 없으면 그 기능은 미설정 상태다. 외부 소스와 데이터의 사용 권한은 따로 확보한다. [연결 업무 범위](camera-lens-sensor-experiments.md)

## 코드 라이선스와 데이터 권리

GitHub에 소스가 있다고 모든 파일을 하나의 라이선스로 재배포할 수 있는 것은 아니다. 플랫폼 자체 코드, 포함한 upstream 코드/notice, 다운로드하는 engine/runtime, 모델 가중치, 지도·영상·DB를 각각 구분한다. 현재 목록은 [component register](open-source-register.md), [보존 notice](../third_party/README.md), 각 integration의 lock/manifest를 기준으로 확인한다. 전체 배포물의 라이선스 검토 완료를 의미하지 않는다.

특히 native HIL Network에는 다음 별도 조건이 있다.

| 구성요소 | 저장소에서 확인할 근거 |
| --- | --- |
| autoas BSW | 고정 subset의 선언은 **GPL-3.0 또는 저자와 별도 상용 계약**. [pin·파일 hash·조건](../integrations/vecu_network/native/bsw/vendor/autoas.lock.json)을 유지한다. |
| Trampoline OS | 원본 GPL/LGPL 파일 조건과 새 application의 **GPL-2.0-or-later**를 구분한다. [native OS 배포 설명](../integrations/vecu_network/native/os/README.md)을 따른다. |
| Verilator | 도구의 LGPL/Artistic 조건과 사용자의 RTL/C++ 소스 조건은 별개다. 도구/헤더 수정·배포 범위는 [공식 FAQ](https://verilator.org/guide/latest/faq.html)를 확인한다. |
| ngspice | 사용한 release의 여러 구성요소 조건을 [공식 COPYING](https://github.com/ngspice/ngspice/blob/master/COPYING)에서 확인한다. 회로 모델의 권리는 별도다. |

별도 프로세스라는 이유만으로 고객 배포 권리가 해결되지는 않는다. 선택 native 소스·패치·빌드 산출물을 배포할 때는 해당 구성요소의 원본 notice와 필요한 source를 함께 검토한다. 기본 샘플 실행에 이 native 구성요소들은 필요하지 않다.

nuScenes 등 외부 데이터의 연구 제한은 코드 라이선스로 바뀌지 않는다. OSM 출처·ODbL, NGII 제공 조건, 모델 가중치 조건, CARLA/Unreal asset 조건도 개별로 유지한다. 작은 샘플 DB는 프로젝트가 새로 만든 합성 예제와 그 manifest를 사용하며 운영 DB·고객 데이터·실도로 원본을 포장하지 않는다.

마지막 확인: 2026-10-27. 설치 환경별 재검증과 실제 실행 인수는 사용자가 선택한 profile에서 수행한다.
