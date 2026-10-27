"""Container-only compression must not alter sensor bytes or legacy captures."""
import hashlib,zipfile
import pytest
from axiom_carla.city_native import camera_archive
from axiom_carla.city import validate_camera_archive

def test_network_deflate_preserves_bgra_member_bytes_and_hashes(tmp_path):
    raw=bytes(range(256))*3600
    outputs=[]
    for network in (False,True):
        path=tmp_path/('network.zip' if network else 'legacy.zip')
        with camera_archive(path,network=network) as archive:archive.writestr('front/000000.bgra',raw)
        with zipfile.ZipFile(path) as archive:
            assert archive.namelist()==['front/000000.bgra']
            assert archive.read('front/000000.bgra')==raw
            assert hashlib.sha256(archive.read('front/000000.bgra')).digest()==hashlib.sha256(raw).digest()
            assert archive.getinfo('front/000000.bgra').compress_type==(zipfile.ZIP_DEFLATED if network else zipfile.ZIP_STORED)
        outputs.append(path.stat().st_size)
    assert outputs[1]<outputs[0]

@pytest.mark.parametrize('network',[False,True])
def test_publication_checks_profile_and_original_pixels(tmp_path,network):
    raw=bytes(range(256))*8
    record=dict(member='front/000000.bgra',size_bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
    path=tmp_path/'camera.zip'
    with camera_archive(path,network=network) as archive:archive.writestr(record['member'],raw)
    validate_camera_archive(path,[record],network=network)
    with pytest.raises(ValueError,match='compression'):
        validate_camera_archive(path,[record],network=not network)
    with pytest.raises(ValueError,match='content mismatch'):
        validate_camera_archive(path,[dict(record,sha256='0'*64)],network=network)
    with pytest.raises(ValueError,match='size mismatch'):
        validate_camera_archive(path,[dict(record,size_bytes=16*1024**2+1)],network=network)
    with pytest.raises(ValueError,match='closure'):
        validate_camera_archive(path,[],network=network)
