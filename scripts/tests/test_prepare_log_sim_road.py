"""Small authored camera/clock contracts; the actual road output is separate."""
import base64
import io
import json
from pathlib import Path
import sys

from mcap.reader import make_reader
from mcap.writer import Writer
from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prepare_log_sim_road as road


def source():
    output = io.BytesIO()
    writer = Writer(output)
    writer.start()
    schema = writer.register_schema('authored-test-clock', 'jsonschema', b'{"type":"object"}')
    ego = writer.register_channel('/planning/ego', 'json', schema)
    objects = writer.register_channel('/planning/original_annotations', 'json', schema)
    writer.add_metadata('authored-original-receipt', {'sha256': 'a' * 64})
    for index, at in enumerate((1_000_000_000, 1_500_000_000)):
        writer.add_message(objects, at, road.canonical({'original': index}), at, index)
        writer.add_message(ego, at, road.canonical({'ego': index}), at, index)
    writer.finish()
    image = io.BytesIO()
    Image.new('RGB', (16, 8), (40, 90, 160)).save(image, format='JPEG')
    raw = image.getvalue()
    cameras = [{'timestamp_us': at, 'width': 16, 'height': 8,
                'sha256': road.sha(raw), 'data_base64': base64.b64encode(raw).decode(),
                'sample_data_token': str(index), 'sample_token': str(index), 'name': 'CAM_FRONT',
                'path': 'authored/camera.jpg'}
               for index, at in enumerate((965_000, 1_465_000))]
    return output.getvalue(), cameras, raw


def test_original_pixels_independent_camera_clocks_and_metadata_are_preserved():
    original, cameras, image = source()
    raw, receipt = road.add_original_cameras(original, cameras)
    reader = make_reader(io.BytesIO(raw))
    records = list(reader.iter_messages(log_time_order=False))
    assert len(records) == 6
    images = [(channel, message) for _, channel, message in records if channel.id == receipt['camera_channel_id']]
    assert [message.log_time for _, message in images] == [965_000_000, 1_465_000_000]
    for _, message in images:
        body = json.loads(message.data)
        assert base64.b64decode(body['data']) == image
        assert body['timestamp']['sec'] * 10**9 + body['timestamp']['nsec'] == message.log_time
    assert list(reader.iter_metadata())[0].metadata == {'sha256': 'a' * 64}
    assert receipt['channel_ordinals']['1'] == [1, 4]
    assert receipt['camera_lineage']['commercial_use'] is False


def test_changed_bytes_and_camera_clock_reordering_are_rejected():
    original, cameras, _ = source()
    with pytest.raises(ValueError, match='source hash'):
        road.add_original_cameras(original, [{**cameras[0], 'sha256': '0' * 64}])
    with pytest.raises(ValueError, match='original increasing order'):
        road.add_original_cameras(original, list(reversed(cameras)))
