# CARLA 근접 점의 자차 출처 진단

2026-09-14 KST(실행 기록은 2026-09-13 UTC), 기존 near 지붕 장착 조건의 **일반 LiDAR 근접 후보 8,853개 모두가 현재 자차 actor252의 semantic LiDAR 점과 XYZ 12바이트 단위로 일치**했습니다. 이 조건에서 자차 표면이 관측기의 양의 높이 장애물 후보로 들어오는 것을 확인했습니다. 실제 주행 판정과 기존 원본은 바꾸지 않았습니다.

## 확인한 범위

기존 CARLA build1ae5356·Town10HD_Opt·vehicle.tesla.model3, 높이2.2m 지붕 장착·64채널·256,000명목광선/s·하향FOV−65°를 유지했습니다. 정지한 차량에 일반 LiDAR와 semantic LiDAR를 같은 위치·방향으로 부착했습니다. Semantic 객체 ID는 진단용 관측에만 사용하며 제어 입력으로 공급하지 않았습니다.

| 관측 | 결과 |
| --- | ---: |
| 계획 / 수집 / 검증된 프레임 쌍 | 8 / 8 / 8 |
| 일반 LiDAR 전체 점 | 54,448 |
| 같은 채널의 semantic XYZ와 12바이트 일치 | 54,448 |
| 일반 LiDAR 중 자차 ID에 정확히 대응 | 18,678 |
| 근접 후보 / 그중 자차 ID에 정확히 대응 | 8,853 / 8,853 |
| 근사·모호·미대응 연결 | 각각0 |
| Semantic 자체의 자차 ID 관측 | 33,928 |

근접 후보는 선언된 ego 좌표계에서 `0 ≤ x ≤ 2.5`, `|y| ≤ 1.2`, `0.2 ≤ z ≤ 2.5`m입니다. 변환은 원본 sensor XYZ에서 `(x, -y, z + 2.2)`이며, 선언 장착과 API/measurement의 상대 위치 차이는 최대 약5.52µm로 기록했습니다. 이는 센서 물리 calibration 정확도 평가가 아닙니다. 매 프레임 실제 sensor parent와 차량 pose·bounding box를 보존했으며, bounding box 안에 있다는 사실만으로 actor ID를 부여하지 않았습니다.

Dropout 때문에 두 센서의 점 개수와 순서는 다릅니다. 분석기는 점 순서로 두 배열을 연결하지 않고 같은 채널에서 XYZ를 비교합니다. 바이트 일치, 수치만 일치하는 signed zero, 최대0.1mm 근사, 복수 ID 후보, 미대응을 구분합니다. 이번 실제 데이터는 모두 바이트 일치였으므로 근사 허용오차를 사용해 ID를 전달한 점은 없습니다.

## 재사용할 도구

- [`scripts/diagnose_carla_self_hit.py`](../scripts/diagnose_carla_self_hit.py): 이번 고정 pilot의 원본·실제 pose/parent·callback·정리 근거 수집. 실행에 사용한 파일과 동일한 bytes입니다. 현재는 특정 intent hash·near recipe·adapter0.7·전용23actor 장면을 요구하는 진단 도구이며 일반 설정 편집 API는 아닙니다.
- [`scripts/analyze_carla_self_hit.py`](../scripts/analyze_carla_self_hit.py): CARLA 연결 없이 저장된 자료를 분석합니다. 원본 및 event chain·프레임·pose·parent를 확인하고 모든 일반 LiDAR 점의 대응 상태를 새 JSON에 저장합니다.

저장 자료의 재분석 예:

```sh
.venv/bin/python scripts/analyze_carla_self_hit.py \
  --input .runtime/carla-self-hit-v2-2026-09-13/roof/capture \
  --source-script .runtime/carla-self-hit-v2-2026-09-13/roof/diagnose.py \
  --output /path/to/new-analysis.json
```

출력 파일은 새 경로여야 합니다. Exit0은 모든 계획 쌍의 분석 완료, exit1은 유효 쌍 부족 등 불완전 결과, exit2는 근거 검증 실패입니다. 실패 시 ID 결론을 만들어내지 않습니다. 원본 capture는 로컬 `.runtime`의 실제 자료이며 새로운 checkout에 자동 포함되는 예제 데이터가 아닙니다.

## 진단 도구 수정과 검증

첫 진단은 생성 직후 목록에서 센서가 보이지 않는 문제로 유효 쌍0/8에서 멈췄고, 정리에서도 센서2개를 놓쳤습니다. 실패와 별도 정리 기록은 [이전 보고서](validation/carla-self-hit-diagnostic-2026-09-13.json)에 남아 있습니다. 새 도구는 생성 시 반환된 센서 참조와 ID·종류·고유 role·parent를 확인합니다. 현재 목록에 명시적인 ID 충돌이 있으면 삭제하지 않습니다. 새로운 센서만 목록에서 누락됐을 때에만 listener 시작 전에 최대1회의 갱신 tick을 허용합니다.

센서 stop/destroy → 진입한 callback 종료 → 부모 차량 삭제 → world 설정 복구 → 새 snapshot 확인 순서를 검증합니다. 이는 native C++ 내부 queue의 일반적인 종료 보장을 입증하지 않으므로, 이번 실제 프로세스의 exit0과 stderr도 별도로 보존했습니다. 16개 callback이 종료됐고 활성0·정리된 소유 actor0·원래23actor 및 map/settings/weather 복원을 확인했습니다.

로컬 회귀시험은 센서 수명·지연된 목록·소유권 충돌·RPC/기록 실패·callback 경합18건과 오프라인 분석 반례13개를 포함한 시험1건, 총 pytest19개입니다. 실제 CARLA 수집1건과 구분합니다. 검사 도구를 repository 경로로 옮긴 후 재분석한 전체 결과 hash도 처음 분석과 동일했습니다.

## 남은 작업

이번 증거는 같은 장착 조건에서 자차 반사가 생기는 원인을 확인합니다. 과거의 ID 없는 XYZI에 새 actor ID를 소급해서 붙이지 않습니다. 실제 급제동 문제의 해결, 완만한 정지, 실차 센서 성능이나 독립적인 확률 시험의 통과를 의미하지 않습니다.

다음은 **센서 설정 개정과 rig 위치·회전을 실제 센서에 적용하고 확인하는 API·UI·adapter 경로**입니다. 광선 설정과 장착 위치를 분리해서 변경하고, raw 점은 보존해야 합니다. 자차 점을 제외하더라도 가려진 공간이 관측된 것으로 처리해서는 안 됩니다. 전방 장착 변경은 별도 물리 구성 시험으로 평가합니다.

[전체 검증 근거](validation/carla-self-hit-confirmed-2026-09-14.json)
