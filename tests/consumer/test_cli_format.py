"""Tests for the CLI update-message formatter (consumer.cli._format_update)."""

from consumer import cli


def test_baseline_message_leads_with_action_and_counts():
    msg = cli._format_update(
        {
            "mode": "baseline",
            "applied": 0,
            "cursor": "cb48b5c3e046f240aa0b7b9656c8505d6cbb98b7",
            "baseline": {
                "tag": "baseline-2026-07",
                "points": 150822,
                "entities": 309773,
                "edges": 682068,
            },
        }
    )
    # The action + the loaded dataset must be front and center (not "applied 0").
    assert "restored full baseline (baseline-2026-07)" in msg
    assert "150,822 vectors" in msg and "309,773 entities" in msg and "682,068 edges" in msg
    assert "cb48b5c3e046f240aa0b7b9656c8505d6cbb98b7" in msg
    assert "compaction" in msg  # the expected-re-download note
    assert not msg.startswith("update: baseline, applied 0")  # the old misleading form is gone


def test_diffs_message():
    msg = cli._format_update({"mode": "diffs", "applied": 3, "cursor": "a7b8", "baseline": None})
    assert "applied 3 incremental update(s)" in msg
    assert "Version:  a7b8" in msg


def test_up_to_date_message():
    msg = cli._format_update(
        {"mode": "up_to_date", "applied": 0, "cursor": "a7b8", "baseline": None}
    )
    assert "no changes — already the latest" in msg
    assert "Version:  a7b8" in msg
