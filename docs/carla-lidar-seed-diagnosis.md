# CARLA LiDAR seed · 조사 및 수정 경계

상태: **설정 경로의 reseed 누락 확인, source patch 준비 완료. 수정 엔진의 빌드·실행 인수는 미완료입니다.** 현재 설치된 CARLA와 worker, 기존 실행 결과는 바꾸지 않았습니다.

## 실제 출력에서 시작한 문제

CARLA0.6의 같은 cruise 조건에서 요청 seed42/43/44를 비교했지만, 각 LiDAR profile 안에서는81개 native callback의 원본 점군과 궤적이 모두 같았습니다. 서로 다른 sensor actor와 world frame을 가진 실제6개 실행이며 파이프라인의 캐시 재사용으로 설명할 근거는 찾지 못했습니다. 세 seed를 독립적인 무작위 표본3개로 세지 않습니다. [기존 실제 인수](validation/carla-sensor-profiles-2026-09-13.json).

공식 [RayCastLidar.cpp · 1ae5356](https://github.com/carla-simulator/carla/blob/1ae5356/Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Sensor/RayCastLidar.cpp)은 constructor에서 초기 description으로 RNG를 seed합니다. 이후 요청 blueprint를 읽어 새 description을 적용하는 `Set(FLidarDescription)`에는 reseed가 없습니다. Dropout도 같은 RNG를 사용하므로 Gaussian noise가0이라는 사실만으로 점군 동일성을 설명할 수 없습니다. 플랫폼 manifest의 blueprint attribute readback은 요청 설정 확인이며 실제 RNG 내부 상태 조회가 아닙니다.

## 실행 중인 바이너리 확인

[원본 ELF 조사·호출 대상·판단 근거](validation/carla-lidar-seed-binary-2026-09-13.json)는 원격 Linux의 실제 Shipping 파일과 PID3801의 `/proc/3801/exe` SHA가 같음을 보존합니다. 파일은119,029,520bytes, SHA-256은 `d9433afa4f5961f1419399d61477ae52f83c54d4f657bd871f73739c8f11e958`이며 별도 debug 파일과 ELF build ID `a489e7257fbe49a8`이 일치합니다. 엔진에 debugger를 붙이거나 world를 tick하지 않았습니다.

- `Set(FLidarDescription)`의 전체694bytes를 추출했습니다. Description 복사와 scan/dropout 설정은 있으나 seed 필드·RNG pointer/state에 대한 reseed 접근이 없습니다. 함수의 직접 호출 대상은 debug symbol로 배열·벡터 메모리 관리와 allocator/memset으로 확인했습니다.
- Constructor에는 `ASensor::SetSeed(int)`와 같은 seed 필드·RNG 상태 갱신이 인라인돼 있습니다. 따라서 SetSeed라는 함수 호출이 보이지 않는다는 사실만으로 결론을 내리지 않았습니다.
- 이 결과는 **설치된 configuration 함수의 reseed 누락**을 뒷받침합니다. 실제 센서 인스턴스의 모든 lifecycle 경로나 현재 RNG 상태를 관측한 것은 아니므로 “실제 RNG seed가0”이라고 확정하지 않습니다. 초기 입력0과 내부 generator state도 동일 개념이 아닙니다.

Shipping 자체의 objdump 함수 표시는 남아 있는 동적 symbol에 대한 상대 위치일 수 있습니다. 보고서의 실제 함수 경계는 build ID가 일치하는 debug symbol의 주소·크기로 정했고 호출 대상은 별도 addr2line으로 확인했습니다. 이 정적 검토는 수정 엔진의 동작 시험을 대체하지 않습니다.

## 준비한 수정과 다음 인수

[`Set`에서 새 seed를 적용하는 source patch](../integrations/carla/patches/1ae5356-lidar-noise-seed.patch)와 [고정 해시·적용 절차·필수 인수](../integrations/carla/patches/README.md)를 추가했습니다. 공식 원본에서 적용 검사, 정확한 변경 비교, 이중 적용 거절과 역적용 원본 복원까지 확인했습니다. **컴파일이나 배포 성공은 주장하지 않습니다.**

현재 확인한 설치는 실행용 package이며 CARLA/Unreal source와 Clang이 없습니다. 별도 source build 환경과 Unreal fork 접근을 확보한 다음, 진단 build의 seed readback 및 고정 pose42/43/42 실험으로 실제 적용을 검증해야 합니다. 변경된 engine에는 새 실행 환경 식별자를 부여하고 과거 run을 그대로 보존합니다. 그때까지 UI의 seed 적용 미확인 경고를 유지합니다.
