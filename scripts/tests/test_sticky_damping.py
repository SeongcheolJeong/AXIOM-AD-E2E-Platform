"""Pure byte-layout contracts, not native vehicle-performance tests."""
import importlib.util
import base64
import hashlib
import json
from pathlib import Path
import struct

import pytest

spec = importlib.util.spec_from_file_location('sticky_contract', Path(__file__).parents[1] / 'sticky_damping_contract.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


def example(suspension=(0,), forward=(1, 3), lateral=(0, 2)):
    data = bytearray(c.DATA_SIZE)
    rows = bytearray()
    for offset, wheels in zip((112, 228, 344), (suspension, forward, lateral)):
        for wheel in wheels:
            data[offset + wheel] = 1
    for kind, wheels in enumerate((suspension, forward, lateral)):
        for wheel in wheels:
            row = bytearray(c.ROW_SIZE)
            struct.pack_into('<3f', row, 0, 1., 0., 0.)
            struct.pack_into('<f', row, 28, .0001 * wheel)
            struct.pack_into('<f', row, 44, -3.402823466e38)
            struct.pack_into('<f', row, 60, 3.402823466e38)
            struct.pack_into('<f', row, 68, 1000.)
            struct.pack_into('<H', row, 76, 3)
            if kind == 1:
                data[164 + wheel * 12:176 + wheel * 12] = row[:12]
                data[212 + wheel * 4:216 + wheel * 4] = row[28:32]
            rows.extend(row)
    return bytes(rows), bytes(data)


def test_only_forward_damping_changes():
    rows, data = example()
    changed, details = c.transform_rows(rows, data, c.profile(4.))
    assert details['forward_wheels'] == [1, 3]
    assert details['unrelated_bytes_unchanged']
    for i in range(5):
        assert struct.unpack_from('<f', changed, 80 * i + 68)[0] == (4. if i in (1, 2) else 1000.)
    assert changed[:80] == rows[:80] and changed[240:] == rows[240:]


def test_sham_has_identical_bytes():
    rows, data = example()
    assert c.transform_rows(rows, data, c.profile(1000.))[0] == rows


@pytest.mark.parametrize('value', [0, -1, .1, 5., True, '4', float('nan'), float('inf')])
def test_undeclared_or_disabled_damping_rejected(value):
    with pytest.raises(ValueError):
        c.profile(value)


@pytest.mark.parametrize('offset,fmt,value', [(76, '<H', 0), (68, '<f', 999.), (64, '<f', 1.),
    (28, '<f', 8.), (0, '<f', 0.), (44, '<f', -1.), (60, '<f', 0.), (12, '<f', 1.)])
def test_unexpected_constraint_semantics_fail_closed(offset, fmt, value):
    rows, data = example(suspension=(), forward=(0,), lateral=())
    bad = bytearray(rows)
    struct.pack_into(fmt, bad, offset, value)
    with pytest.raises(ValueError):
        c.transform_rows(bytes(bad), data, c.profile(4.))


def test_absent_forward_rows_are_not_fabricated():
    rows, data = example(forward=())
    rewritten, details = c.transform_rows(rows, data, c.profile(4.))
    assert rewritten == rows and details['damping_fields'] == []


def test_invalid_flags_count_and_profile_are_rejected():
    rows, data = example()
    bad = bytearray(data); bad[228] = 2
    with pytest.raises(ValueError): c.transform_rows(rows, bytes(bad), c.profile(4.))
    with pytest.raises(ValueError): c.transform_rows(rows[:-1], data, c.profile(4.))
    p = c.profile(4.); p['production_rollout'] = True
    with pytest.raises(ValueError): c.transform_rows(rows, data, p)


def write_intervention(path, damping=4., mutate=None):
    selected = c.profile(damping)
    rows, data = example()
    after, details = c.transform_rows(rows, data, selected)
    event = {'kind': 'solver_rows', 'world_frame': 99, **details,
             'constant_block_b64': base64.b64encode(data).decode(),
             'before_b64': base64.b64encode(rows).decode(),
             'after_b64': base64.b64encode(after).decode(), 'readback_matched': True}
    if mutate: mutate(event)
    events = [{'kind': 'intervention_loaded', 'profile': selected,
               'profile_sha256': hashlib.sha256(c.canonical(selected)).hexdigest()},
              {'kind': 'owned_process', 'pid': 123}, event]
    head, lines = '0' * 64, []
    for i, item in enumerate(events):
        item.update(sequence=i, previous_sha256=head)
        head = hashlib.sha256(c.canonical(item)).hexdigest()
        lines.append(json.dumps({'event': item, 'sha256': head}))
    path.write_text('\n'.join(lines) + '\n')


def load_reader(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    spec = importlib.util.spec_from_file_location('sticky_reader', Path(__file__).parents[1] / 'analyze_sticky_damping.py')
    reader = importlib.util.module_from_spec(spec); spec.loader.exec_module(reader)
    return reader


@pytest.mark.parametrize('damping,expected', [(4., 2), (1000., 0)])
def test_reader_verifies_actual_bytes_not_declared_success(tmp_path, monkeypatch, damping, expected):
    p = tmp_path/'events.jsonl'; write_intervention(p, damping)
    result = load_reader(monkeypatch).verify_interventions(p)
    assert result['rewritten_damping_fields'] == expected
    assert result['outside_damping_changes'] == 0 and result['solver_callbacks'] == 1


@pytest.mark.parametrize('field,value', [('readback_matched',False),('unrelated_bytes_unchanged',False),
    ('forward_wheels',[0]),('row_count',1),('world_frame',True),('kind','intervention_error')])
def test_even_rehashed_inconsistent_records_are_rejected(tmp_path, monkeypatch, field, value):
    p = tmp_path/'events.jsonl'; write_intervention(p, mutate=lambda e:e.update({field:value}))
    with pytest.raises(ValueError): load_reader(monkeypatch).verify_interventions(p)


def test_rehashed_lateral_change_is_not_a_valid_damping_experiment(tmp_path, monkeypatch):
    def mutate(e):
        raw = bytearray(base64.b64decode(e['after_b64']))
        struct.pack_into('<f', raw, 3*80+68, 4.)
        e['after_b64'] = base64.b64encode(raw).decode()
    p = tmp_path/'events.jsonl'; write_intervention(p, mutate=mutate)
    with pytest.raises(ValueError, match='approved damping'):
        load_reader(monkeypatch).verify_interventions(p)
