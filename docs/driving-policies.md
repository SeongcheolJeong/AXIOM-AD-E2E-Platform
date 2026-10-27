# 실제 E2E 연결 준비 · VAD-Tiny

현재 상태: **실제 nuScenes mini·CAN + 공식 VAD-Tiny 가중치의 3-frame GPU 추론·재현성에 이어, 2026-10-07 격리 연구 환경에서 일반 Experiment API·영속 입력·큐·worker·실제 원격 GPU 실행·원본 artifact 다운로드를 인수했다. 단일 고정 checkpoint만 지원하며 정책 A/B·계획의 안전 판정은 미완료다. 운영 서버에 배포하지 않았다.**

Component stacks → Driving policies에서 준비한 입력 JSON을 점검하고 결과 receipt의 세 궤적 모드를
살펴볼 수 있다. 검출 bbox 형식에 궤적을 끼워 넣지 않는다. UI/API의 native 실행 상태는
`not_configured`이며, 업로드한 실행 출처는 `self_reported_receipt_not_independently_verified`이다.
이 기존 업로드 검사 경로 자체는 서버 GPU 작업 제출·정책 A/B·Verification Plan 판정 연결이 아니다.
아래의 새 선택적 Experiment 경로에서 실제 실행을 제출하며, 운영 profile은 명시적으로 설정하기 전까지 미구성 상태다.
연구 CLI만으로 상용 제품 기능이나 실제 모델 인수를 완료 처리하지 않는다.

## 확인한 문제와 해결 경계

- 원격 `seongcheoljeong@carla-host.example.invalid:22`의 SSH 키 인증을 등록한 뒤 연결에 성공했다.
  RTX2070SUPER 8192MiB, 드라이버595.84, GPU 여유3537MiB, RAM 가용 약12GiB, 디스크 여유155GB를
  확인했다. 기존 CUDA12.4.1 컨테이너의 GPU 접근도 성공했다. CARLA 서버/client 빌드1ae5356과
  Town10HD_Opt 맵을 read-only API로 확인했다. CARLA가 약3978MiB를 사용 중이므로 기존 실행을
  종료하지 않았다. VAD 격리 환경과 CUDA 연산은 이후 인수했다. 공식 Tiny 가중치 확보와 strict 로드는 이후 확인했다. 2026-10-05 비상업 연구 범위를 확인한 뒤 원본 확보·실제 추론·동일 출력 재현까지 확인했다.
- VAD 원본 `forward_test/simple_test_pts`는 미래 GT·객체 GT를 읽어 지표를 계산한다.
  converter는 미래 정답 위치에서 route command를 만들고, 일부 CAN/LCF 부족을 미래 궤적이나
  zero로 보완한다. 해당 평가 경로를 그대로 주행 정책의 인과적 추론 입력으로 사용하지 않는다.
- 이번 runner는 원본 `extract_feat`와 학습된 `pts_bbox_head`를 직접 호출한다. GT/points/map 정답을
  전달하지 않고, 모든 mode를 보존한다. 입력은 실제 6-camera·현재까지의 CAN·보정/자차 위치와
  이전 BEV다. 원본 Tiny 설정에서 optional ego-history/LCF encoder는 꺼져 있으며 이를 지원한다고
  꾸며 입력값을 만들지 않는다. 2026-10-05 실제 CUDA에서 이 head 경로와 세 mode 출력·반복 실행을 확인했다. GT 지표를 읽는 원본 평가 wrapper 전체와의 평가 점수 동등성을 주장하지 않는다.

## 고정 입력과 출력

`driving-input/1`, profile `vad-tiny-native/1`은 다음을 보존한다.

- 카메라 순서: FRONT, FRONT_RIGHT, FRONT_LEFT, BACK, BACK_LEFT, BACK_RIGHT.
- 동일 scene의 실제 연속 3–16 keyframe. 부족한 history·카메라·CAN을 반복/zero로 채우지 않는다.
- sample/sample_data token·원본 timestamp·image path/hash/크기·LiDAR→image 행렬·ego/global transform.
- 원본 metadata hash, CAN 파일/hash와 선택 message timestamp. anchor 이하의 최근 메시지만 사용하며
  CAN/카메라 100ms 입력 정합 한계를 적용한다. Native check는 원본에서 다시 구성해 manifest와 비교한다.
- 명시적인 route command 또는 all_modes. 미래 GT로 유리한 mode를 고르지 않는다.
- 실행마다 reset 후 기록 prefix를 순서대로 공급한다. 새 scene/새 실행 간 BEV를 재사용하지 않는다.

`driving-result/1`은 입력 전체/hash, 원본 source commit/파일 hash, adapter/계약 hash, checkpoint,
설정 원본과 해석된 설정 hash, 라이브러리·seed·device·CUDA peak·경과 시간과 모든 frame을 보존한다.
궤적은 원본 head의 offset을 누적한 **현재 nuScenes LiDAR XY, meter** 6점/0.5초이며,
right/left/straight 순서의 세 mode다. 전역 지도 좌표·차량 제어 입력과 혼동하지 않는다.
실패하면 성공 receipt를 발행하지 않고 기존 결과를 덮어쓰지 않는다.

## 연구 runner 사용

upstream은 `hustvl/VAD`의 `1688c4b1c3a9e2e7873ca9700ff8058170c0e3c8`에 고정한다.
다운로드한 source는 `.runtime/vad-source-20261004`에 있고 제품 패키지에 넣지 않는다.
Native 환경은 플랫폼 Python 3.12/CARLA 환경과 별도로 준비해야 한다. 고정 profile 후보는
Python3.8, Torch1.9.1/cu111, mmcv-full1.4.0, mmdet2.14.0, mmdet3d0.17.1,
mmsegmentation0.14.1, nuscenes-devkit1.1.9이다. 후보를 준비한 뒤 CUDA 연산과 실제 결과로 인수한다.
공식 설치 안내를 그대로 기존 환경에 적용하거나 시스템 드라이버·CARLA를 변경하지 않는다.

Native Linux 작업 디렉터리에서 아래 경로를 실제 선택한 경로로 치환한다. Core와 adapter 소스만
PYTHONPATH로 제공하며, core 서버 dependency를 native Python에 설치할 필요는 없다.

```bash
export PYTHONPATH="$PWD/backend/src:$PWD/integrations/vad/src"
python -m axiom_vad environment
python -m axiom_vad prepare --data-root /selected/nuscenes \
  --scene-token SCENE_TOKEN --sample-token TARGET_SAMPLE_TOKEN --frames 3 \
  --rights-note 'Approved isolated research scope; source dataset terms retained' \
  --output /selected/evidence/driving-input.json
python -m axiom_vad check --data-root /selected/nuscenes \
  --input /selected/evidence/driving-input.json
python -m axiom_vad run --source /selected/VAD --data-root /selected/nuscenes \
  --checkpoint /selected/weights/VAD_tiny.pth --input /selected/evidence/driving-input.json \
  --weights-rights 'Record the actual checkpoint source and permitted scope here' \
  --confirm-trusted-native-code --output /selected/evidence/driving-result.json
```

데이터 루트에는 `v1.0-mini/*.json`, 필요한 `samples/` 이미지와 `can_bus/scene-xxxx_pose.json`이
함께 있어야 한다. CAN expansion 없이 mini 영상만 넣으면 부족한 입력으로 거절한다.
Pickle infos를 읽지 않고 필요한 원본 JSON만 읽는다. Native checkpoint/config는 신뢰 코드 입력이다.
현재 Mac의 environment 검사에서는 Torch/MMCV/nuScenes가 없으므로 unavailable을 반환했다.

## 공통 API/SDK

`GET /api/v1/driving-policies/capabilities`, `POST /api/v1/driving-policies/preflight`,
`POST /api/v1/driving-policies/inspect-result`를 UI와 agent/SDK가 공유한다.
SDK 메서드는 `driving_policy_capabilities`, `preflight_driving_input`, `inspect_driving_result`다.
UI는 JSON 숫자를 다시 직렬화하지 않고 원래 JSON text를 전달해 Python의 float/hash 표현을 보존한다.
구조 검사와 receipt hash 일관성은 실행 출처 인증·물리 검증이 아니다. API에서 원격 파일을 읽거나
임의 shell 명령을 제출하지 않는다. DB schema16·기존 검출 평가/계획을 변경하지 않는다.

## 라이선스와 다음 인수

2026-10-04 공식 원문 확인: [VAD 코드](https://github.com/hustvl/VAD/blob/main/LICENSE)는 Apache-2.0,
[설치](https://github.com/hustvl/VAD/blob/main/docs/install.md)는 위 구형 환경을 안내한다.
VAD README의 라이선스 진술은 **코드**이며 공개 checkpoint의 상용 권한을 따로 확정하지 않았다.
[nuScenes 데이터](https://www.nuscenes.org/nuscenes)는 기본 비상업용이며 상업용은 별도 라이선스 문의가 필요하다.
내부 사용이라는 이유만으로 상업적 개발이 비상업 연구가 되는 것은 아니다. 선택한 데이터/가중치의
실제 사용 권한을 확인해야 한다. 초기 준비 단계에서는 데이터·가중치를 내려받지 않았다. 이후 공식 가중치를 원격 연구 작업공간에 확보해 CPU strict-load 호환성을 검사했으며, 제품에는 재배포하지 않았다. 데이터 사용 범위는 별도 확인 중이다.

실제 인수는 **원격 인증 → 격리 환경/사용 권한/원본/가중치 확보 → native 3-frame inference →
입력·모든 mode·실행 근거 확인 → 정책 실행 DB/기존 queue·모델 A/B·Verification Plan 연결** 순서다.
현재 만든 화면의 authored 궤적을 이 인수의 근거로 사용하지 않는다. 센서/ISP 연결과 폐루프 시험도 남아 있다.

원격 키 인증을 등록하려면 사용자가 이 Mac의 Terminal에서 다음 명령을 실행해 기존 SSH 비밀번호를
직접 입력할 수 있다. 비밀번호를 채팅·저장소에 보내지 않는다.

```bash
ssh-copy-id -i ~/.ssh/id_ed25519.pub seongcheoljeong@carla-host.example.invalid
```

등록 후 같은 host의 BatchMode SSH로 환경을 다시 확인할 수 있다.

## 이번 준비 검사

관련 Python 87개, 개발 Chrome 업무 2개 및 숫자 표현 보존 후 최종 Chrome 검사 1개가 통과했다.
브라우저 수는 중복이며 VAD CUDA 실행 인수 수가 아니다. TypeScript/production build,
Python3.8 문법과 diff 검사를 통과했다. 준비 검사 당시 인증에 막혔던 원격 장비는 이후 SSH·Docker GPU·CARLA 연결을 확인했다. 해당 초기 준비 검사 당시 모델 실행은 미실행이었고, 아래 2026-10-05 인수에서 실제 실행했다.
[기계 판독 기록](validation/driving-policy-preparation-20261004.json).


## 원격 native 환경 인수 · 2026-10-04

SSH 인증 이후 `/home/axiom-user/workspace/axiom-vad-research`에 전용 환경을 만들었다.
호스트 Python/CARLA venv/드라이버는 변경하지 않았다. `axiom-vad-research:profile1`의 이미지 ID는
`sha256:9cd45263d67573a2944aeffbc8b82cd415388734e67dba609f402dc0bc2058bf`이다.
Python3.8.20, Torch1.9.1+cu111, MMCV1.4.0, MMDet2.14.0, MMDet3D0.17.1,
MMSeg0.14.1, nuScenes-devkit1.1.9을 실제 import/metadata로 확인했다. `pip check`도 통과했다.

**발견한 문제:** PyTorch1.9.1 태그의 베이스 이미지 내부는 Python3.7.11/Torch1.10.0이었다.
태그를 신뢰해 통과 처리하지 않고, digest를 고정한 베이스 안에 별도 Python3.8.20 환경과
정확한 Torch wheel을 설치하고 빌드 시 버전을 검사했다. 오래된 NVIDIA apt 저장소의 서명 오류는
컨테이너 내부의 해당 feed만 제거해 해결했다. CUDA11.1 툴킷과 호스트 드라이버는 유지했다.

실제 RTX2070SUPER에서 MMCV deformable-attention CUDA 연산이 알려진 `[2,3]` 기대값을 반환했다.
원본 VAD 플러그인·Tiny 설정 import와 원본 commit/내용 hash도 확인했다. read-only bind mount의
소유자 차이는 이미지 안에서 **정확한 `/work/VAD` 경로만** Git safe.directory로 지정했다.
이 검사에는 가중치·도로 영상·학습 모델 궤적 출력이 없다. UI/API GPU 작업 제출 기능도 추가되지 않았다.

[컨테이너 설정·사용법](../integrations/vad/container/README.md),
[기계 판독 인수 기록](validation/vad-native-environment-20261004.json)에 설정과 근거 hash를 보존했다.
원격의 `pip-freeze-profile1.txt`, `cuda-smoke-profile1.json`, `source-smoke-profile1.json`도 보존한다.
로컬 사본은 `.runtime/vad-native-environment-20261004`에 있다.

재검사 명령:

```bash
ssh seongcheoljeong@carla-host.example.invalid
cd /home/axiom-user/workspace/axiom-vad-research
docker run --rm --network=none --gpus all \
  --mount type=bind,src="$PWD",dst=/work,readonly \
  -e PYTHONPATH=/work/backend/src:/work/integrations/vad/src \
  axiom-vad-research:profile1 python -B /work/container/cuda_smoke.py
```

다음 실제 추론에는 승인된 nuScenes mini 원본과 CAN 확장, 신뢰할 VAD-Tiny 가중치 및 각 사용 범위가
필요하다. 원격 workspace를 깊이 제한해 검색한 범위에서는 해당 파일을 찾지 못했으며,
다른 위치나 외부 저장소에도 없다고 단정하지 않는다. 사용자 경로·권한 확인을 기다리는 동안
모델 결과를 대신 작성하지 않는다. 기존 CARLA가 GPU 약4GB를 사용 중인 상태는 유지한다.


## 공식 Tiny checkpoint 호환성 확인 · 2026-10-04

고정 VAD README가 연결하는 공식 Google Drive 파일을 원격의
`/home/axiom-user/workspace/axiom-vad-research/weights/VAD_tiny.pth`에 확보했다.
파일은 484,968,871 bytes이고 SHA256은
`430e8881f33bdb19f86557db00c2ccc77da22b3bc042ba7f41a9ef4c2b258a16`이다.
이 해시는 내려받은 파일의 식별자이며, 게시자가 별도로 인증한 checksum이라고 주장하지 않는다.
정적 ZIP/pickle GLOBAL 검사 후, 명시적으로 신뢰한 공식 source/config/checkpoint를
격리된 네트워크 없는 컨테이너에서 CPU `strict=True`로 로드했다. 불일치 없이
39,865,999개 parameter와 746개 state tensor가 준비됐다. **도로 입력·모델 추론·궤적 출력은 없다.**

[공식 배포 링크](https://github.com/hustvl/VAD/blob/1688c4b1c3a9e2e7873ca9700ff8058170c0e3c8/README.md),
[검사 기록](validation/vad-checkpoint-20261004.json),
[격리 검사 스크립트](../integrations/vad/container/checkpoint_smoke.py)를 보존했다.

**이전 대기 상태(2026-10-04, 2026-10-05 해소):** 상업 사용이 허가된 보유 데이터의 경로·범위 또는 상용 개발과
분리된 비상업 연구 범위를 확인해야 한다. nuScenes 기본 배포는 비상업용이다.
플랫폼 상용 목표나 일반적인 진행 요청만으로 해당 데이터의 상업 권한이 확보됐다고 가정하지 않는다.
공식 mini 다운로드 endpoint가 접근 가능하다는 사실도 사용 허가와 별개다.
이번 단계에서 nuScenes 데이터는 내려받지 않았고, checkpoint 상용 권한도 확정하지 않았다.
범위 확인 후 공식 mini·CAN 원본 확보, 고정 3-frame 입력 생성, native 실행과 모든 mode 검사를 진행한다.
데이터가 아직 없다는 이유로 authored 입력을 실제 학습 모델 인수로 대신 사용하지 않는다.


## 실제 기록 영상 추론 인수 · 2026-10-05

사용자가 **상용 개발과 분리된 비상업 연구만** 명시적으로 선택했다. 이 범위에서 공식 mini와
CAN archive를 확보하고 원본 6개 JSON 테이블·2,424개 카메라 영상·mini 10개 scene의 pose CAN을
격리 연구 workspace에 풀었다. 객체/지도 annotation은 추출하거나 모델에 전달하지 않았다.
코드 라이선스와 데이터·가중치 상용 권한은 별개이며, 이번 인수로 상용 권한을 부여하지 않는다.

### 사용자에게 추가로 확인된 업무

실제 원본에서 준비한 input JSON과 GPU result JSON을 **Component stacks → Driving policies**에
업로드해 3개 시점·각 right/left/straight mode의 6개 예측점을 살펴볼 수 있다. 버전과 원본/가중치
해시를 함께 표시하고 판정은 `not_evaluated`로 보존한다. 인수는 분리된 개발 API/브라우저에서 했다.
기존 운영 서버·외부 공유 서비스를 배포/재시작하지 않았다.
GPU 실행은 여전히 격리 연구 CLI이며, 화면의 `Native execution: not_configured`는 서버 제출
기능의 상태다. 업로드 receipt의 출처를 API가 인증하지 않으므로 `self_reported_receipt_not_independently_verified`
표시도 유지한다. 이 문서의 원격 실제 실행 근거와 업로드 API의 출처 인증은 다른 책임이다.

### 실제 입력과 실행

- `scene-0061`, 첫 scene의 원본 keyframe indices **1,2,3**을 실행 전에 고정했다.
- index0은 CAN 기록이 LiDAR anchor보다 17,155µs 뒤에서 시작하여 거절했다. 미래 값/zero/반복
  입력을 넣지 않았으며 첫 시도·제외 사유를 보존했다. 추론 결과를 보고 입력을 고른 것이 아니다.
- 원본 `sample.json`에는 `data` 필드가 없다. 준비 단계의 가정을 수정해
  `sample_data → calibrated_sensor → sensor`로 keyframe 채널을 연결했다. `sensor.json`도 해시를
  보존하고 중복 채널을 거절한다. Test fixture도 실제 테이블 구조를 따라 수정했다.
- 어댑터 개정은 `axiom-vad-native/2`, 패키지는0.2.0이다. 입력/결과 계약은 `/1`을 유지하고
  기존 `/1` 어댑터 receipt도 읽을 수 있다. 지원하지 않는 어댑터 개정은 여전히 거절한다.
- 동일 input hash·source·checkpoint·seed42로 두 개의 독립적인 reset 실행을 했다.
  **3프레임 × 3모드 × 6점의 모든 출력 숫자가 정확히 일치**했다.
- 처리 구간은 약3.64초/3.33초, CUDA peak allocated는 각498,074,112 bytes였다.
  모델 로딩을 제외하고 이미지 처리·추론·결과 점검을 포함한 구간이며 실시간 지연 성능 인수가 아니다.
- 현재 입력의 pose/calibration은 원본 dataset의 현재 localization으로 사용한다. 객체/지도·미래
  궤적 GT를 입력하거나 GT로 mode를 고르지 않는다. Localization 추정기의 성능을 검증한 결과는 아니다.

입력·결과·로그·선택/제외·archive hash·재현성은 원격
`/home/axiom-user/workspace/axiom-vad-research/evidence`와 로컬
`.runtime/vad-native-acceptance-20261005`에 보존한다. 비상업 연구 자료/모델은 제품 패키지에 넣지 않는다.

- [실제 입력](../.runtime/vad-native-acceptance-20261005/driving-input-20261005.json)
- [실제 GPU 결과](../.runtime/vad-native-acceptance-20261005/driving-result-20261005.json)
- [실제 결과의 화면](../.runtime/vad-native-acceptance-20261005/real-vad-trajectories.png)
- [기계 판독 인수 기록](validation/vad-native-acceptance-20261005.json)

현재 관련 Python28개, Chrome2개(그중 실제 receipt 업무1개), Python3.8 문법·diff 검사를 통과했다.
CARLA 서버/build/맵은 마지막 read-only 확인에서도 유지됐다. 이전 authored 검사와 이번 원본
학습 모델 실행을 합쳐 실도로 정확도·폐루프 충돌 회피·실측 센서 검증으로 계산하지 않는다.

다음 제품 기능 단위는 **모델/입력/실행 이력 DB·기존 queue·정상 UI/API 제출 → 고정 입력 정책 A/B →
Verification Plan의 open-loop 진단 근거 연결**이다. 범용 실행기보다 이번 고정 VAD profile 하나를
먼저 연결한다. 주행 안전 판정은 지원되는 별도 closed-loop 근거와 기준을 갖추기 전까지 통과시키지 않는다.

## 2026-10-07 · 선택적 일반 제품 큐의 실제 원격 추론

`DrivingSequence`는 원래 `driving-input/1` JSON bytes/hash와 인과적 카메라·CAN·보정 참조를
영속 보존한다. 검출 Dataset/bbox 계약에 궤적을 넣지 않는다. 일반 API는 다음과 같다.

- `POST /api/v1/driving-sequences/import`: operator가 정한 `AXIOM_DRIVING_INPUT_ROOT` 안의
  상대 JSON 경로만 가져온다. symlink/상위 경로/과대 파일/지원하지 않는 계약을 거절한다.
- `POST /api/v1/driving-sequences`: 명시적인 입력 manifest를 보존한다. 이 입력 검사만으로
  원격 파일 점검이나 실제 모델 실행을 완료했다고 표시하지 않는다.
- `kind=driving_open_loop`, adapter `vad-tiny-native-research`, 입력
  `{"driving_sequence_id":"...","confirm_noncommercial_research":true}`를
  `/experiments/preview → /experiments → /experiments/{id}/executions`로 명시적으로 제출한다.
- 공통 worker가 GPU resource `carla`를 예약하고, 고정 SSH host·immutable Docker image·원본
  source/config/checkpoint/runner hash로 기존 격리 native runner를 실행한다. 모델 실행 전에
  native data root에서 영상·CAN·metadata/보정을 다시 확인한다.
- `/experiments/executions/{id}`에서 status와 진단 근거를 보고, sealed receipt와 원래 입력,
  native result/log/dispatch receipt를 개별 download route로 받는다.

선택적 패키지 `axiom-vad-adapter`의 Experiment entry point만 core에 등록한다. Torch/MMCV는
native 컨테이너에 있으며 core API에 설치하지 않는다. `AXIOM_VAD_NATIVE_CONFIG`는 operator가
선택한 JSON 설정이다. SSH destination/workspace/image digest, source/data/checkpoint 상대경로,
원래 checkpoint/source/config/adapter hash, 신뢰 코드 승인, 비상업 연구 범위·권리 메모·timeout을
전부 고정한다. 사용자 Experiment JSON은 명령·host·가중치 경로를 고를 수 없다.
설정이 없으면 `not_configured`이며 대체/이전 결과로 성공 receipt를 만들지 않는다.

이 slice의 원본 및 파생물은 default 격리 연구 workspace만 사용한다. 비상업 연구 확인을
명시적으로 요구하고 commercial use를 false로 보존한다. 한 개의 승인된 checkpoint밖에 없으므로
`same_input_model_ab`와 새로운 센서/GT/안전 evaluator는 거절한다. 입력 mode를 바꾸거나 seed를
바꾼 것을 새 모델 A/B라고 부르지 않는다. 실행 후 직접 취소는 지원하지 않고 timeout/SSH 응답
실패는 불명확한 외부 실행으로 남긴다. 원격 근거 폴더를 묵시적으로 지우거나 재실행하지 않는다.

실제 인수 job `job_29d8ca984bf94a128fbc1a28d8334657`는 위의 일반 API로 생성·제출하고 공통
worker로 실행했다. 기존 선정 입력/공식 checkpoint를 다시 다운로드하지 않고 실제 3 frame ×
6 camera·세 trajectory mode를 실행했다. 5 native artifact와 sealed receipt의 bytes/SHA를 일반
다운로드 API로 확인했다. Native 처리 구간은 약3.59초, 전달·container/model load·실행·receipt
회수 전체는 약21.21초다. 둘 모두 실시간 응답이나 주행 품질 인수를 뜻하지 않는다.
기존 CARLA를 종료·재시작하지 않았다. 원격 Docker `--rm` 반환을 확인하고 근거 폴더는 남겼다.

- 격리 제품 DB/설정/근거: `.runtime/four-module-vad-product-20261007`
- 실제 제품 큐 기록: `.runtime/four-module-vad-product-20261007/acceptance.json`
- 간결한 재현·출처·브라우저 인수 기록: [`validation/vad-product-queue-20261007.json`](validation/vad-product-queue-20261007.json)
- sealed receipt SHA: `bf47708389d155d45885d2442716d79dd22ed473c99b5e8ae313dadc6ad03f2b`

연구 외 상용 데이터/가중치 권리, 두 번째 모델 등록·고정 입력 정책 A/B, 폐루프 카메라 피드백과
계획의 안전 판정은 별도 남은 gate다. 이 완료 단위로 이를 통과 처리하지 않는다.
