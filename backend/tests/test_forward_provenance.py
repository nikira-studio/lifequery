"""Regression tests for preserving Telegram forwarded-message provenance."""

from telegram.json_import import _extract_forward_info


def test_json_import_preserves_available_forward_provenance():
    info = _extract_forward_info(
        {
            "forwarded_from": "Original Sender",
            "forwarded_from_id": "user222898370",
            "forwarded_date": "2026-07-12T19:08:09",
            "forwarded_from_chat_id": "222898370",
            "forwarded_from_message_id": 98525,
        }
    )

    assert info == (
        1,
        "user222898370",
        "Original Sender",
        1783883289,
        "222898370",
        "98525",
    )


def test_json_import_leaves_non_forwarded_messages_unmarked():
    assert _extract_forward_info({"text": "normal message"}) == (
        0,
        None,
        None,
        None,
        None,
        None,
    )
