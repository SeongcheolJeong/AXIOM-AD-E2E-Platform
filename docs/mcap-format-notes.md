# MCAP ingestion: format and Python implementation notes

확인일: 2026-09-12. 이 문서는 **upstream 동작 조사와 구현 지침**이다. 아래 재현 결과는 작은 합성 파일로 확인한 라이브러리 동작이며, 플랫폼 ingestion의 검증 완료나 대용량 성능을 뜻하지 않는다. 플랫폼 API와 수용 범위는 [recordings-contract.md](recordings-contract.md), 실제 동작은 구현 및 회귀 테스트를 함께 확인한다.

## 고정 버전과 출처

현재 `requirements.lock` 및 로컬 Python 3.12 환경에서 `mcap==1.4.0`, `lz4==4.4.5`, `zstandard==0.25.0`을 확인했다. MCAP 1.4.0은 2026-06-18 배포되었으며 패키지 metadata는 Python >=3.7과 lz4/zstandard 의존성을 선언한다. 이것이 전체 플랫폼의 Python 지원 범위를 정하지는 않는다. [PyPI release](https://pypi.org/project/mcap/1.4.0/)

- 조사한 공식 wheel: `mcap-1.4.0-py3-none-any.whl`
- wheel SHA-256: `0b48b1cc951b8d5aabd2599e60d410bae4f1be1819094f54117b7cbf6b3ee2e9`
- PyPI provenance의 upstream commit: `b33fa682a5c517b1d213faeabd118e0b4f9d9d93`, tag `releases/python/mcap/v1.4.0`.
- 해당 commit의 `reader.py`, `stream_reader.py`, `records.py`가 wheel 안의 파일과 바이트 단위로 일치함을 확인했다. 이후 버전에서 아래 제약이 달라질 수 있다.

MCAP upstream은 MIT license다. 배포물에는 관련 저작권 및 허가 고지를 보존해야 한다. 조사한 wheel에는 별도 LICENSE 파일이 없으므로 패키지 설치만으로 제품 고지 작업이 끝났다고 가정하지 않는다. codec과 그 내부 native library의 고지는 별도로 추적한다. 로그 안의 센서 데이터, 지도, schema, attachment 권리는 MCAP 구현의 MIT license와 별개다. [고정 commit LICENSE](https://github.com/foxglove/mcap/blob/b33fa682a5c517b1d213faeabd118e0b4f9d9d93/LICENSE)

## 기본 reader를 업로드 검증기로 바로 사용하면 안 되는 이유

| API / 구현 | 확인한 동작 | 플랫폼에서의 판단 |
| --- | --- | --- |
| `make_reader(stream, validate_crcs=False, decoder_factories=())` | `record_size_limit` 인자를 노출하지 않으며 seek 가능 여부로 구현 선택 | 임의 업로드의 검증 진입점으로 사용하지 않는다 |
| `StreamReader(..., record_size_limit=2**32)` | 바깥 record의 선언 길이를 검사한다. 자동 chunk 해제와 내부 record 크기까지 제한하지 않는다 | 파일, record, 압축 출력, 누적 출력 제한을 각각 구현한다 |
| `StreamReader(..., emit_chunks=True)` | 자동 압축 해제를 피하지만 `Chunk.read`의 내부 길이를 바깥 record 경계로 제한하지 않는다 | 먼저 경계가 검증된 record buffer 또는 bounded cursor를 사용한다 |
| `NonSeekingReader.iter_messages(log_time_order=False)` | 파일 순서로 순회한다. schema/channel 사전은 유지한다 | 순회 자체는 가능하지만 별도의 framing/CRC/자원 검증이 필요하다 |
| `NonSeekingReader.iter_messages(log_time_order=True)` | 일치하는 메시지를 메모리에 모아 정렬한다 | ingestion이나 요청마다 반복되는 시간 탐색에 사용하지 않는다 |
| `SeekingReader`의 unindexed fallback | 새로운 `NonSeekingReader`에 CRC, record limit, reverse 설정을 전달하지 않는다 | 검증 또는 조회 정확성의 근거로 사용하지 않는다 |
| `SeekingReader`의 indexed chunk 읽기 | `Chunk.read`를 직접 호출하며 해당 읽기는 바깥 record limit을 거치지 않는다 | 원본 인덱스 유무와 관계없이 자체 검증 후 조회한다 |

API 설명과 실제 구현은 구분해야 한다. 특히 기본 CRC 검증은 꺼져 있다. [Python reader API](https://mcap.dev/docs/python/mcap-apidoc/mcap.reader), [고정 reader.py](https://github.com/foxglove/mcap/blob/b33fa682a5c517b1d213faeabd118e0b4f9d9d93/python/mcap/mcap/reader.py), [고정 stream_reader.py](https://github.com/foxglove/mcap/blob/b33fa682a5c517b1d213faeabd118e0b4f9d9d93/python/mcap/mcap/stream_reader.py)

### CRC는 종류별로 구분한다

- native `StreamReader(validate_crcs=True)`는 제공된 DataEnd CRC 및 자동 해제하는 Chunk CRC를 확인한다. `emit_chunks=True`이면 Chunk CRC는 호출자가 확인해야 한다.
- native `SeekingReader`는 읽은 Chunk CRC를 확인하지만 DataEnd CRC를 확인하지 않는다. 위 unindexed fallback에서는 요청한 검증 설정도 사라진다.
- `Attachment.read`는 attachment CRC를 읽어서 버리고, native reader는 Footer의 summary CRC를 검사하지 않는다.
- CRC 값 0은 검증 정보가 없다는 의미다. `omitted`, `verified`, `failed`를 구분한다. 원본 SHA-256 보존은 내용 식별과 다운로드 무결성을 위한 별도 근거다.

직접 validator는 DataEnd, Chunk, Attachment, Summary CRC를 각 지정 구간에 대해 검사해야 한다. 일부 종류를 수용하지 않으면 그 제한을 명시한다. 원본 metadata를 읽었다는 이유로 센서 정확성이나 출처를 확인했다고 표시하지 않는다. [고정 records.py](https://github.com/foxglove/mcap/blob/b33fa682a5c517b1d213faeabd118e0b4f9d9d93/python/mcap/mcap/records.py), [MCAP CRC fields](https://mcap.dev/spec)

## 압축 데이터 처리 지침

프로젝트에서 정한 record/chunk 상한을 `L`로 둔다. 현재 설계 값은 32 MiB이고, 누적 확장 데이터 상한은 256 MiB다. 이는 제품 정책 값이며 MCAP 형식 자체의 한도가 아니다. 업로드 바이트 수, payload/metadata 크기, 메시지/정의 수와 실행 시간도 별도 제한한다. 상한 안의 여러 복사본과 codec 내부 상태 때문에 프로세스 RSS가 `L` 또는 256 MiB 이하라는 의미는 아니다.

공통 순서:

1. 바깥 record 길이와 파일 잔여 길이를 먼저 검사하고 정확히 그 경계 안에서 읽는다. 내부 string/map/data 길이도 잔여 범위를 초과할 수 없다.
2. MCAP `uncompressed_size <= L`, compressed payload 길이 및 알려진 compression 이름을 해제 전에 확인한다. native 함수는 알 수 없는 이름을 비압축처럼 처리하므로 그대로 재사용하지 않는다.
3. codec header의 크기/window도 확인하고 실제 출력이 `L`을 초과하지 않도록 제한한다. 선언만 검사하고 일반 `decompress()`를 호출해서는 안 된다.
4. 출력 길이가 MCAP 선언과 일치하는지 확인하고 nonzero Chunk CRC를 계산한다. 내부 record에도 같은 framing/길이 검사와 누적 예산을 적용한다.
5. 해제 완료, compressed input 소비, 내부 record 경계를 모두 확인한다. 실패하면 임시 파일과 DB 변경을 롤백한다.

### Zstandard 0.25.0

`max_output_size`만으로는 충분하지 않다. frame에 content size가 있으면 이 값보다 큰 결과를 반환할 수 있다. 작은 실제 재현에서 20-byte frame이 `max_output_size=128`에도 65,536 bytes로 해제되었다.

한 chunk당 단일 frame만 수용하는 구현의 권장 조건:

- `zstandard.get_frame_parameters(data)`로 알려진 `content_size <= L`, `window_size <= L`, 지원하는 dictionary 조건을 해제 전에 검사한다. 외부 dictionary를 지원하지 않는 첫 구현은 `dict_id != 0`을 거절한다.
- `CONTENTSIZE_UNKNOWN`은 정상적인 별도 값이다. 이를 큰 실제 크기로 오해하지 말고 명시적으로 처리한다.
- `ZstdDecompressor(max_window_size=L).decompress(data, max_output_size=L, allow_extra_data=False)`를 사용하고 실제 결과 길이를 검사한다. 크기 미기록 frame도 출력 상한을 받는다.
- truncated frame, extra bytes 및 concatenated frame은 이 경로에서 거절한다. 여러 frame을 지원하려면 각 frame과 누적 예산에 대한 별도 구현/시험이 필요하다.

**단위 주의:** 온라인 문서는 `max_window_size`를 KiB로 설명하지만, 확인한 0.25.0 C extension은 값을 그대로 native byte 한도로 전달한다. 실제 unknown-size 64 KiB frame은 65,535에서 거절되고 65,536에서 성공했다. 단위 변환만 믿기보다 `FrameParameters.window_size`의 byte 값을 사전 검사하고 고정 codec 버전으로 회귀 검증한다. [Zstandard API](https://python-zstandard.readthedocs.io/en/latest/decompressor.html), [Frame parameters](https://python-zstandard.readthedocs.io/en/latest/misc_apis.html), [0.25.0 C source](https://github.com/indygreg/python-zstandard/blob/0.25.0/c-ext/decompressor.c)

### LZ4 4.4.5

native MCAP 경로의 `lz4.frame.decompress(data)`에는 출력 상한이 없다. `get_frame_info()`의 nonzero content size가 상한을 초과하면 먼저 거절한다. `LZ4FrameDecompressor().decompress(data, max_length=L+1)`로 전체 compressed input을 한 번 제공하고 결과가 `L`을 초과하면 거절한다. 상한 이내 결과라도 `eof`가 참이어야 하고 `unused_data`가 없어야 한다. 끝나지 않은 frame, trailing bytes, 여러 frame을 성공으로 처리하지 않는다. 작은 실제 시험에서 `max_length=129`는 64 KiB 데이터의 출력을 129 bytes로 제한했고, truncated/trailing 입력은 `eof`/`unused_data`로 구분되었다. [LZ4 frame API](https://python-lz4.readthedocs.io/en/stable/lz4.frame.html)

이 방식은 크기가 제한된 chunk를 해제하는 설계다. 파일 전체를 계속 메모리에 누적하는 구현은 피한다. 위 API 조건 확인만으로 native codec의 모든 결함이나 CPU 고갈 가능성을 제거했다고 주장하지 않는다.

## 인덱스와 timestamp 처리

Summary와 Summary Offset은 없어도 된다. indexed file에서도 chunk 밖의 message는 native index 탐색에서 빠질 수 있다. 따라서 원본 Summary 통계나 시간 범위를 신뢰해서 데이터를 생략하지 않고 실제 Data section을 끝까지 검사한다. 시간 순서가 뒤섞여도 `end_time`을 처음 넘은 지점에서 순회를 종료하지 않는다. [MCAP structure and indexing](https://mcap.dev/spec)

프로젝트의 권장 저장 경로는 **원본 그대로 보존 + 순회로 검증한 payload sidecar + SQL 메시지 인덱스**다. `(recording_id, channel_id, log_time_ns, ordinal)` 정렬로 중복 timestamp를 안정적으로 다루고, sidecar offset/length/payload SHA-256과 원본 SHA-256을 연결한다. 이는 설계 선택이며 upstream 인덱스의 유효성을 보증하는 것은 아니다.

- channel ID를 identity로 사용한다. 같은 topic 이름에 다른 channel/schema가 올 수 있다.
- schema/channel 정의가 필요할 때 이미 존재해야 한다. Summary의 정의로 앞선 message의 잘못된 참조를 나중에 정당화하지 않는다. 메시지가 없는 정의는 보존할 수 있다.
- 동일 ID의 반복 정의는 내용 일치 여부를 확인한다. 충돌하는 정의를 마지막 값으로 덮어쓰지 않는다.
- empty recording/channel의 count는 0이고 start/end는 null이다. timestamp 0을 missing 값으로 쓰지 않는다.
- 원본 `log_time`, `publish_time`, sequence를 보존한다. MCAP timestamp는 사용자에게 알려진 epoch의 uint64 nanoseconds이므로 항상 UTC라고 해석하지 않는다.
- API에서는 ns를 decimal string으로 전송한다. SQL 문자열 정렬은 20자리 zero-padding처럼 uint64 수치 순서를 보존해야 한다. UI 상대 초 변환은 원점 차이를 먼저 계산한다.
- native reader의 `start_time`은 포함, `end_time`은 제외다. 플랫폼 cursor/범위 계약과 섞지 않는다. 현재 플랫폼 API의 별도 inclusive 규칙은 [recordings-contract.md](recordings-contract.md)를 따른다.

## container 읽기와 payload decode는 별개다

`iter_messages()`는 envelope와 payload bytes를 반환한다. schema 이름이나 `message_encoding`을 읽는 것은 ROS 2 CDR, protobuf, 이미지 또는 point cloud decode의 구현 증거가 아니다. `iter_decoded_messages()`도 설치/등록된 DecoderFactory에 따라 동작한다. [Decoder API](https://mcap.dev/docs/python/mcap-apidoc/mcap.decoder)

첫 slice는 명시적인 bounded JSON decode와 opaque preview만 제공한다. JSON null도 decode 성공일 수 있으므로 상태 필드를 사용한다. unknown encoding, calibration/transform 부재, sensor clock 관계와 source provenance는 별도로 표시한다. JSON을 파싱했다는 이유로 schema 적합성이나 센서 모델 타당성을 검증했다고 표시하지 않는다.

## 작은 파일로 확인한 upstream 회귀 사례

아래는 이 문서 작성 중 로컬에서 실행한 조사 결과다. 공식 `Writer`로 작은 파일을 생성하고 특정 필드만 변경했다. 플랫폼 importer의 수용 테스트 결과는 아니다.

| 입력 / 호출 | mcap 1.4.0 관찰 결과 |
| --- | --- |
| 1,024-byte payload, record limit 128 | NonSeekingReader는 거절, unindexed SeekingReader는 메시지 3개 반환 |
| DataEnd CRC 1-bit 변경, validate_crcs=True | NonSeekingReader는 CRC 오류, unindexed SeekingReader는 메시지 반환 |
| 파일 시간 순서 30, 10, 20, unindexed reverse=True | SeekingReader는 10, 20, 30 반환 |
| encoded Chunk 132 bytes, record limit 256 | StreamReader가 65,536-byte payload 3개 반환 |
| Header record 길이를 1로 변조 | StreamReader가 전체 header를 읽고 정상 종료 |
| Attachment CRC 또는 Summary CRC 1-bit 변경 | StreamReader(validate_crcs=True)가 정상 종료 |

플랫폼 회귀 테스트에는 위 사례 외에 잘린 바깥/내부 record, 과대한 내부 length, 없는 channel/schema 참조, 충돌 정의, zero CRC, 누적 확장 한도, 과대한 codec header, 압축 frame 뒤의 추가 바이트, empty/unindexed/out-of-order 로그, uint64 끝 범위, rollback 및 exact download hash를 포함한다. 단일 frame 제한 등 수용 정책은 오류 메시지와 사용자 문서에도 일치해야 한다.
