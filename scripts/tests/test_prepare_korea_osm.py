"""Synthetic selection fixtures; not evidence of a Korean surveyed map."""
import importlib.util
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

spec = importlib.util.spec_from_file_location('prepare_korea_osm', Path(__file__).parents[1] / 'prepare_korea_osm.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture(extra=''):
    return ('''<osm version="0.6"><node id="1" lat="36.5" lon="127.2"/>
    <node id="2" lat="36.502" lon="127.2"/><node id="3" lat="36.503" lon="127.2"/>
    <way id="10"><nd ref="1"/><nd ref="2"/><tag k="highway" v="residential"/>'''
    + extra + '''</way><way id="11"><nd ref="2"/><nd ref="3"/>
    <tag k="highway" v="residential"/></way></osm>''').encode()


def test_preserves_coordinates_ids_and_reports_inferences():
    raw = fixture()
    selected, report = module.select_roads(raw, ['10', '11'])
    before, after = ET.fromstring(raw), ET.fromstring(selected)
    assert [n.attrib for n in before.findall('node')] == [n.attrib for n in after.findall('node')]
    assert report['source_sha256'] == module.sha(raw)
    assert report['selected_sha256'] == module.sha(selected)
    assert 330 < report['approximate_centerline_length_m'] < 335
    assert 'lane_width_not_measured' in report['inference_flags']


def test_grade_separation_and_missing_source_are_rejected():
    with pytest.raises(ValueError, match='Grade-separated'):
        module.select_roads(fixture('<tag k="bridge" v="yes"/>'), ['10'])
    with pytest.raises(ValueError, match='absent'):
        module.select_roads(fixture(), ['999'])


def test_existing_snapshot_is_never_overwritten(tmp_path):
    target = tmp_path / 'source.osm'
    module.save(target, b'old')
    with pytest.raises(ValueError, match='Preserve existing revision'):
        module.save(target, b'new')
    assert target.read_bytes() == b'old'
