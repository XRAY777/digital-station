from __future__ import annotations

from backend.app.websocket.protocol import apply_sequence, bounded_enqueue


def frame(sequence: int, event_id: str, kind: str = "train.speed.updated") -> dict[str, object]:
    return {
        "sequence": sequence,
        "eventId": event_id,
        "type": kind,
        "payload": {"trainId": "train-1", "speed": sequence},
    }


def test_duplicate_sequence_or_id_is_ignored() -> None:
    seen = {"e1"}
    assert apply_sequence(1, seen, frame(1, "e1")) == ("DUPLICATE", 1)


def test_missing_sequence_requests_snapshot_resync() -> None:
    assert apply_sequence(2, set(), frame(4, "e4")) == ("RESYNC", 2)


def test_valid_sequence_advances_client_watermark() -> None:
    seen: set[str] = set()
    assert apply_sequence(2, seen, frame(3, "e3")) == ("APPLY", 3)
    assert "e3" in seen


def test_position_burst_coalesces_but_critical_event_is_never_replaced() -> None:
    queue = [frame(1, "p1", "train.position.updated")]
    assert bounded_enqueue(queue, frame(2, "p2", "train.position.updated"), 1)
    assert queue[0]["eventId"] == "p2"
    assert not bounded_enqueue(queue, frame(3, "r3", "route.reserved"), 1)
