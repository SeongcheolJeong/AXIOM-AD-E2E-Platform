"""Independent traversal tests: time ties must not make original samples unreachable."""

import io
import json

import pytest
from mcap.writer import CompressionType, Writer


@pytest.fixture
def traversal_recording(client):
    output = io.BytesIO()
    writer = Writer(output, compression=CompressionType.ZSTD)
    writer.start()
    channel = writer.register_channel("/same", "json", 0)
    other = writer.register_channel("/same", "json", 0)
    empty = writer.register_channel("/empty", "opaque", 0)
    # Stable file order differs from chronological order. The zero-time group
    # exceeds the API page limit, and no valid timestamp precedes it.
    rows = [(channel, 0) for _ in range(150)]
    rows += [(channel, 2**64 - 1), (other, 0), (channel, 1), (other, 1)]
    for ordinal, (channel_id, timestamp) in enumerate(rows):
        writer.add_message(channel_id, timestamp, json.dumps({"original_ordinal": ordinal}).encode(), timestamp)
    writer.finish()
    response = client.post("/api/v1/recordings", params={"name": "Traversal boundary fixture", "source_kind": "synthetic"}, headers={"Content-Type": "application/octet-stream"}, content=output.getvalue())
    assert response.status_code == 201, response.text
    return response.json()["id"], channel, other, empty, rows


def fetch(client, fixture, **params):
    recording_id, channel, *_ = fixture
    params.setdefault("channel_id", channel)
    return client.get(f"/api/v1/recordings/{recording_id}/samples", params=params)


@pytest.mark.parametrize("page_size", [1, 17, 100])
def test_all_zero_time_duplicates_are_reachable_with_stable_pagination(client, traversal_recording, page_size):
    _, channel, _, _, original = traversal_recording
    expected = [ordinal for ordinal, (channel_id, timestamp) in sorted(enumerate(original), key=lambda row: (row[1][1], row[0])) if channel_id == channel]
    seen = []
    params = {"from_start": "true", "limit": page_size}
    for _ in range(len(expected) + 1):
        response = fetch(client, traversal_recording, **params)
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["requested_time_ns"] is None
        assert len(page["samples"]) <= page_size
        for sample in page["samples"]:
            assert sample["decoded"]["original_ordinal"] == sample["ordinal"]
            seen.append(sample["ordinal"])
        next_ordinal = page["next_after_ordinal"]
        if next_ordinal is None:
            break
        assert page["samples"] and next_ordinal == page["samples"][-1]["ordinal"]
        params = {"after_ordinal": next_ordinal, "limit": page_size}
    else:
        pytest.fail("Traversal did not terminate")
    assert seen == expected
    assert len(seen) == len(set(seen)) == 152


def test_time_cursor_keeps_latest_duplicate_semantics(client, traversal_recording):
    response = fetch(client, traversal_recording, at_ns="0", limit=1)
    assert response.status_code == 200, response.text
    assert response.json()["requested_time_ns"] == "0"
    assert [item["ordinal"] for item in response.json()["samples"]] == [149]
    assert response.json()["next_after_ordinal"] == 149


def test_ordinal_cursor_uses_time_then_ordinal_not_file_order(client, traversal_recording):
    # File ordinal 152 has timestamp 1. Chronologically ordinal 150 comes next.
    response = fetch(client, traversal_recording, after_ordinal=152, limit=100)
    assert response.status_code == 200, response.text
    assert [item["ordinal"] for item in response.json()["samples"]] == [150]
    assert response.json()["next_after_ordinal"] is None


def test_cursor_after_last_sample_is_empty(client, traversal_recording):
    response = fetch(client, traversal_recording, after_ordinal=150)
    assert response.status_code == 200, response.text
    assert response.json()["samples"] == []
    assert response.json()["next_after_ordinal"] is None


def test_empty_channel_has_no_continuation(client, traversal_recording):
    response = fetch(client, traversal_recording, channel_id=traversal_recording[3], from_start="true")
    assert response.status_code == 200, response.text
    assert response.json()["samples"] == []
    assert response.json()["requested_time_ns"] is None
    assert response.json()["next_after_ordinal"] is None


@pytest.mark.parametrize("anchor", [151, 99999])
def test_cursor_requires_anchor_from_selected_channel(client, traversal_recording, anchor):
    response = fetch(client, traversal_recording, after_ordinal=anchor)
    assert response.status_code == 404, response.text


@pytest.mark.parametrize("anchor", [-1, 100000])
def test_cursor_outside_recording_ordinal_bound_is_rejected(client, traversal_recording, anchor):
    response = fetch(client, traversal_recording, after_ordinal=anchor)
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("params", [
    {"at_ns": "0", "from_start": "true"},
    {"at_ns": "0", "after_ordinal": 0},
    {"from_start": "true", "after_ordinal": 0},
])
def test_cursor_modes_are_mutually_exclusive(client, traversal_recording, params):
    response = fetch(client, traversal_recording, **params)
    assert response.status_code == 422, response.text
