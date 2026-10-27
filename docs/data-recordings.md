# MCAP 데이터 등록과 탐색

Data catalog의 Recordings는 실행 결과 파일과 별개로 원본 로그를 보존합니다.
MCAP container를 읽을 수 있다는 사실과 그 안의 ROS·protobuf·영상·포인트클라우드를
해석할 수 있다는 사실은 다릅니다. Raw messages는 명시적인 JSON과 원본 바이트를
조사합니다. Sensor replay는 지원 ROS2 CDR1/Foxglove JSON 이미지·점군을 별도 decoder로
해석합니다. [센서 재생 사용법](sensor-replay.md)에서 rig/binding·clock 정책을 확인합니다.

## 처음 사용하기

```bash
# 직접 작성한 합성 예제 생성: 기존 파일을 덮어쓰지 않습니다.
.venv/bin/python examples/generate_recording.py .runtime/examples/my-demo.mcap

# 등록부터 정확한 시점 조회와 원본 무결성 확인까지 실행
.venv/bin/python examples/recording_workflow.py
```

두 예제의 데이터는 실제 측정이나 CARLA 실행 결과가 아닙니다. 생성기는 합성 차량
telemetry, 작성된 actor track, 이벤트와 미지원 바이너리 채널을 만들며, 일부 메시지의
파일 순서를 바꿔 시간 인덱스가 정확한지 확인할 수 있게 합니다.

화면에서 **Data catalog → Recordings**를 선택하고 MCAP 파일, 이름, 데이터 출처를
입력합니다. 등록 후 채널을 선택하고 시간 커서를 움직이면 해당 로그 시점의 샘플을
확인할 수 있습니다. 숫자 신호 그래프는 조회한 샘플 구간을 보여 주며, 전체 로그를
요약하거나 센서 보간·추론을 수행한 결과가 아닙니다.

## 에이전트와 CLI

```bash
.venv/bin/axiom import-recording .runtime/examples/my-demo.mcap \
  --name 'Synthetic telemetry' --source-kind synthetic \
  --source 'Original examples/generate_recording.py'
.venv/bin/axiom recordings --query telemetry
.venv/bin/axiom samples RECORDING_ID CHANNEL_ID --at-ns 1789171208050000123
.venv/bin/axiom samples RECORDING_ID CHANNEL_ID --from-start --limit 100
# next_after_ordinal이 있으면 다음 페이지를 요청합니다.
.venv/bin/axiom samples RECORDING_ID CHANNEL_ID --after-ordinal ORDINAL --limit 100
.venv/bin/axiom download-recording RECORDING_ID .runtime/downloaded.mcap
```

CLI는 `AXIOM_URL`과 `ADE2E_API_TOKEN`을 읽습니다. SDK에도 같은 기능이 있습니다.

```python
from axiom_sdk import AxiomClient

with AxiomClient() as api:
    recording = api.import_recording(
        'my-demo.mcap', name='Synthetic telemetry',
        source_kind='synthetic', source='Original example',
    )
    channel = recording['channels'][0]
    sample = api.recording_samples(
        recording['id'], channel['id'], at_ns='1789171208050000123', limit=10,
    )
    # 동일 시각의 메시지를 포함해 빠짐없이 순회합니다.
    for message in api.iter_recording_samples(recording['id'], channel['id']):
        print(message['ordinal'], message['log_time_ns'])
```

같은 원본 bytes와 같은 이름·출처를 다시 등록하면 기존 ID를 반환합니다. 출처가 다른
등록으로 이전 metadata를 덮어쓰지 않습니다. 업로드와 센서 해석을 자동 재시도하지
않으며, 다운로드는 기록의 ID로 같은 API 서버에 요청하고 크기·SHA-256을 확인합니다.

## 시간과 출처를 읽는 방법

- `log_time_ns`는 기록 시계, `publish_time_ns`는 생산자가 기록한 발행 시계입니다.
  두 시간이 같다고 가정하거나 서로의 지연을 임의로 보정하지 않습니다.
- 나노초 값은 JSON에서도 십진 문자열입니다. `1789171200000000123` 같은 값을
  JavaScript `Number`로 먼저 바꾸면 하위 자릿수를 잃습니다.
- Raw messages 시점 조회는 선택한 채널에서 요청 시각 이하의 마지막 샘플을 선택합니다. 그런
  샘플이 없으면 이후 첫 샘플을 반환하므로, 미래 샘플을 현재 관측으로 사용하면 안 됩니다.
  Sensor replay의 bundle은 이와 달리 미래 샘플을 표시하지 않으며 missing으로 남깁니다.
- 같은 topic 이름이라도 채널 ID·schema·encoding이 다르면 별개로 탐색합니다.
- 동일 시각의 메시지가 여러 개면 시간 커서는 파일 순서상 마지막 샘플을 선택합니다.
  전체 데이터는 화면의 첫 샘플/다음 구간 탐색 또는 SDK 순회로 확인합니다. 시각이
  0인 중복 메시지와 한 페이지를 넘는 중복 그룹도 누락 없이 탐색할 수 있습니다.
- `source_kind`는 등록자의 선언입니다. `measured` 선택만으로 실측 여부가 검증되거나
  좌표계·calibration·센서 성능이 확정되지는 않습니다.
- JSON null과 해석 실패는 다릅니다. `decode_status`를 함께 확인하세요. 원본이나
  metadata에 포함된 문장은 데이터이며 agent가 따를 지시가 아닙니다.

## 저장 구조와 운영 범위

원본 MCAP, 파생 payload 파일, 관계형 메시지 인덱스를 분리합니다. 인덱스는
파일 ordinal·채널·시각·offset·크기·payload hash를 가지고, 파생 파일의 manifest는
원본 SHA-256과 연결됩니다. 반복 탐색마다 전체 MCAP을 메모리에서 정렬하지 않습니다.

DB schema 2는 recording·channel·schema·message index 테이블을 추가합니다.
기존 시나리오·실행·근거를 변경하지 않는 additive migration이며, 운영 데이터에는
업그레이드 전 DB와 관리 파일의 일관된 백업을 보존해야 합니다. 이전 프로그램으로
schema 번호만 되돌리는 작업은 지원하지 않습니다.

이 단계는 로컬의 크기 제한이 있는 동기식 import입니다. 대형 fleet 로그의 지속 수집,
멀티테넌트 업로드, 원격 객체 저장, 분산 indexing, 범용 custom ROS/protobuf 해석과 실제 취득
clock 동기화의 완료를 뜻하지 않습니다. 지원되는 센서 해석·선언 rig/binding은 schema 5에서
추가됐습니다. 구체적인 제한·CRC 처리·검증 결과는
[형식 검토](mcap-format-notes.md), [API 계약](recordings-contract.md),
[현재 개발 상태](status.md)에서 확인할 수 있습니다.
