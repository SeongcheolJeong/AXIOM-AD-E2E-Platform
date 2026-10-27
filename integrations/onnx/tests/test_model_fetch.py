"""Exercise pinned download publication, not the external network."""
from pathlib import Path
from contextlib import nullcontext
import hashlib
import sys
import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'examples'))
import fetch_opencv_yolox as fetcher


@pytest.mark.parametrize('payload', [b'model', b'mode', b'model plus', b'wrong'])
def test_download_checks_actual_file_and_atomic_publication(tmp_path, monkeypatch, payload):
    destination = tmp_path / 'model.onnx'
    def response(*args, **kwargs):
        assert kwargs['follow_redirects'] is False
        return nullcontext(httpx.Response(200, content=payload, request=httpx.Request('GET', args[1])))
    monkeypatch.setattr(fetcher.httpx, 'stream', response)
    sha = hashlib.sha256(b'model').hexdigest()
    if payload == b'model':
        assert fetcher.fetch('https://example.com/pinned', destination, sha, 5).read_bytes() == b'model'
        assert fetcher.fetch('https://example.com/pinned', destination, sha, 5) == destination
    else:
        with pytest.raises(ValueError):
            fetcher.fetch('https://example.com/pinned', destination, sha, 5)
        assert not destination.exists()
    assert not list(tmp_path.glob('.download-*'))


def test_short_write_never_publishes_partial_weights(tmp_path, monkeypatch):
    original = fetcher.os.fdopen
    class ShortWriter:
        def __init__(self, fd, mode):self.stream = original(fd, mode)
        def __enter__(self):return self
        def __exit__(self, *args):self.stream.close()
        def write(self, chunk):return self.stream.write(chunk[:-1])
    monkeypatch.setattr(fetcher.os, 'fdopen', ShortWriter)
    monkeypatch.setattr(fetcher.httpx, 'stream', lambda *args, **kwargs: nullcontext(httpx.Response(200, content=b'model', request=httpx.Request('GET', args[1]))))
    with pytest.raises(OSError, match='Incomplete'):
        fetcher.fetch('https://example.com/pinned', tmp_path / 'model.onnx', hashlib.sha256(b'model').hexdigest(), 5)
    assert list(tmp_path.iterdir()) == []
