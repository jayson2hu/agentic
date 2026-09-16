import json

import pytest
from judgment_graph.scripts.consume_events import parse_envelope


def test_parse_versioned_content_analyzed_envelope() -> None:
    topic, payload, event_id = parse_envelope(json.dumps({
        "topic": "content.analyzed",
        "payload": {
            "content_id": "42",
            "run_id": "run-v2",
            "revision": 2,
        },
        "idempotency_key": "content.analyzed:run-v2",
    }))

    assert topic == "content.analyzed"
    assert payload == {
        "content_id": "42",
        "run_id": "run-v2",
        "revision": 2,
    }
    assert event_id == "content.analyzed:run-v2"


@pytest.mark.parametrize("value", [
    [],
    {"payload": {}, "idempotency_key": "event"},
    {"topic": "content.analyzed", "payload": [], "idempotency_key": "event"},
    {"topic": "content.analyzed", "payload": {}},
])
def test_parse_envelope_rejects_malformed_messages(value) -> None:
    with pytest.raises((TypeError, ValueError)):
        parse_envelope(json.dumps(value))
