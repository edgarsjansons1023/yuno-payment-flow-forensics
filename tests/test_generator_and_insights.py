from __future__ import annotations

import pandas as pd

from payment_funnel import (
    compute_funnel,
    detect_anomalies,
    load_and_validate_events,
    rank_root_causes,
    reconstruct_sessions,
)
from payment_funnel.generator import generate_events, write_events


def test_generator_is_deterministic_and_meets_data_contract(tmp_path):
    first = generate_events()
    second = generate_events()
    assert first == second

    path = write_events(first, tmp_path / "events.csv")
    events = load_and_validate_events(path)
    sessions = reconstruct_sessions(events)

    assert len(sessions) == 1000
    assert int(sessions["completed"].sum()) == 590
    assert sessions["payment_method"].nunique() >= 5
    assert sessions["country"].nunique() >= 3
    assert (sessions["started_at"].max().date() - sessions["started_at"].min().date()).days == 13
    assert sessions["has_duplicate"].any()
    assert sessions["has_out_of_order"].any()
    assert sessions["max_gap_seconds"].gt(15 * 60).any()


def test_committed_data_produces_monotonic_funnel_and_ranked_findings():
    events = load_and_validate_events("data/transaction_events.csv")
    sessions = reconstruct_sessions(events)
    funnel = compute_funnel(sessions)
    insights = rank_root_causes(events, sessions)
    anomalies = detect_anomalies(events, sessions)

    assert funnel["reached_sessions"].is_monotonic_decreasing
    assert funnel.iloc[-1]["overall_conversion_pct"] == 59.0
    assert len(insights) == 5
    assert {"issue", "evidence", "recommendation", "affected_sessions"}.issubset(insights.columns)
    assert insights["evidence"].str.len().gt(20).all()
    assert {"duplicate_events", "out_of_order_events", "long_event_gap", "slow_3ds", "decline_rate_spike"}.issubset(
        set(anomalies["anomaly_type"])
    )
    assert pd.api.types.is_numeric_dtype(anomalies["rate_pct"])

