from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from payment_funnel import (
    apply_filters,
    compute_funnel,
    load_and_validate_events,
    reconstruct_sessions,
    segment_funnel,
)

BASE = {
    "payment_method": "card_visa",
    "currency": "PHP",
    "country": "Philippines",
    "processor": "gateway_alpha",
    "status": "success",
    "decline_code": "",
    "device_type": "mobile",
    "browser": "Chrome",
    "amount": 42.5,
    "duration_ms": 800,
}


def make_event(session: str, event_id: int, sequence: int, event_type: str, **updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        **BASE,
        "session_id": session,
        "event_id": f"event_{event_id}",
        "event_sequence": sequence,
        "timestamp_utc": (datetime(2026, 9, 1, 12, tzinfo=timezone.utc) + timedelta(minutes=sequence)).isoformat(),
        "event_type": event_type,
    }
    row.update(updates)
    return row


def fixture_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    event_id = 1

    def add_journey(session: str, stages: list[tuple[str, dict[str, object]]]) -> None:
        nonlocal event_id
        for sequence, (event_type, updates) in enumerate(stages, start=1):
            rows.append(make_event(session, event_id, sequence, event_type, **updates))
            event_id += 1

    add_journey(
        "complete",
        [
            ("checkout_initiated", {"status": "started"}),
            ("method_selected", {}),
            ("details_entered", {}),
            ("authorization_requested", {"status": "submitted"}),
            ("authorization_response", {}),
            ("payment_completed", {}),
        ],
    )
    add_journey("early", [("checkout_initiated", {"status": "abandoned"})])
    add_journey(
        "details",
        [
            ("checkout_initiated", {"status": "started"}),
            ("method_selected", {}),
            (
                "details_entered",
                {"status": "abandoned", "timestamp_utc": "2026-09-01T12:00:30+00:00"},
            ),
        ],
    )
    add_journey(
        "failed",
        [
            ("checkout_initiated", {"status": "started"}),
            ("method_selected", {}),
            ("details_entered", {}),
            ("authorization_requested", {"status": "submitted"}),
            ("authorization_response", {"status": "failed", "decline_code": "timeout"}),
        ],
    )
    add_journey(
        "pending",
        [
            ("checkout_initiated", {"status": "started", "payment_method": "bank_transfer_local"}),
            ("method_selected", {"payment_method": "bank_transfer_local"}),
            ("details_entered", {"payment_method": "bank_transfer_local"}),
            ("authorization_requested", {"status": "submitted", "payment_method": "bank_transfer_local"}),
            ("authorization_response", {"status": "pending", "payment_method": "bank_transfer_local"}),
        ],
    )
    duplicate = dict(next(row for row in rows if row["session_id"] == "complete" and row["event_type"] == "authorization_requested"))
    duplicate["event_id"] = f"event_{event_id}"
    rows.append(duplicate)
    rows.append(make_event("complete", event_id + 1, 7, "client_telemetry"))
    return rows


@pytest.fixture
def sample_data(tmp_path):
    path = tmp_path / "events.csv"
    pd.DataFrame(fixture_rows()).to_csv(path, index=False)
    events = load_and_validate_events(path)
    return events, reconstruct_sessions(events)


def test_reconstructs_contiguous_funnel_and_outcomes(sample_data):
    events, sessions = sample_data
    funnel = compute_funnel(sessions)

    assert funnel["reached_sessions"].tolist() == [5, 4, 4, 3, 1]
    assert sessions.set_index("session_id")["outcome"].to_dict() == {
        "complete": "success",
        "early": "abandoned",
        "details": "abandoned",
        "failed": "failed",
        "pending": "pending",
    }
    assert sessions.loc[sessions["session_id"].eq("complete"), "event_count"].iat[0] == 7
    assert sessions.loc[sessions["session_id"].eq("complete"), "has_duplicate"].iat[0]
    assert sessions.loc[sessions["session_id"].eq("complete"), "has_unknown_event"].iat[0]
    assert sessions.loc[sessions["session_id"].eq("details"), "has_out_of_order"].iat[0]
    assert events["duplicate_event"].sum() == 2


def test_optional_3ds_is_not_required_for_completion(sample_data):
    _, sessions = sample_data
    completed = sessions.loc[sessions["session_id"].eq("complete")].iloc[0]
    assert not completed["used_3ds"]
    assert completed["completed"]


def test_filter_and_segmentation_reconcile(sample_data):
    _, sessions = sample_data
    filtered = apply_filters(sessions, {"payment_method": ["bank_transfer_local"]})
    assert filtered["session_id"].tolist() == ["pending"]

    segmented = segment_funnel(sessions, "payment_method")
    reached = segmented.groupby("stage")["reached_sessions"].sum().reindex(compute_funnel(sessions)["stage"])
    assert reached.tolist() == compute_funnel(sessions)["reached_sessions"].tolist()


def test_schema_and_timestamp_validation(tmp_path):
    missing_path = tmp_path / "missing.csv"
    pd.DataFrame([{"session_id": "only-column"}]).to_csv(missing_path, index=False)
    with pytest.raises(ValueError, match="Missing required columns"):
        load_and_validate_events(missing_path)

    invalid_path = tmp_path / "invalid.csv"
    invalid = make_event("bad", 1, 1, "checkout_initiated", timestamp_utc="not-a-date")
    pd.DataFrame([invalid]).to_csv(invalid_path, index=False)
    with pytest.raises(ValueError, match="invalid timestamp"):
        load_and_validate_events(invalid_path)


def test_later_stage_cannot_skip_a_required_stage(tmp_path):
    rows = [
        make_event("skipped", 1, 1, "checkout_initiated"),
        make_event("skipped", 2, 2, "payment_completed"),
    ]
    path = tmp_path / "skipped.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    sessions = reconstruct_sessions(load_and_validate_events(path))
    funnel = compute_funnel(sessions)
    assert funnel["reached_sessions"].tolist() == [1, 0, 0, 0, 0]
    assert sessions.iloc[0]["outcome"] == "abandoned"
