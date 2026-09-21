"""Core funnel reconstruction, segmentation, insight, and anomaly analysis."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


CANONICAL_STAGES = [
    "checkout_initiated",
    "method_selected",
    "details_entered",
    "authorization_requested",
    "payment_completed",
]
KNOWN_EVENTS = set(CANONICAL_STAGES) | {"3ds_redirect", "3ds_completed", "authorization_response"}
REQUIRED_COLUMNS = {
    "session_id",
    "event_id",
    "event_sequence",
    "timestamp_utc",
    "event_type",
    "payment_method",
    "currency",
    "country",
    "processor",
    "status",
    "decline_code",
    "device_type",
    "browser",
    "amount",
    "duration_ms",
}
EVENT_SIGNATURE = [
    "session_id",
    "event_sequence",
    "timestamp_utc",
    "event_type",
    "status",
    "decline_code",
]


def load_and_validate_events(source: str | Path | Any) -> pd.DataFrame:
    """Load CSV event data, validate its schema, and attach quality flags."""
    events = pd.read_csv(source, keep_default_na=False)
    missing = sorted(REQUIRED_COLUMNS - set(events.columns))
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")
    if events.empty:
        raise ValueError("The event file is empty")

    events = events.copy()
    for column in ("session_id", "event_id", "event_type"):
        if events[column].astype(str).str.strip().eq("").any():
            raise ValueError(f"Column '{column}' contains blank required values")

    events["timestamp_utc"] = pd.to_datetime(events["timestamp_utc"], utc=True, errors="coerce")
    if events["timestamp_utc"].isna().any():
        count = int(events["timestamp_utc"].isna().sum())
        raise ValueError(f"Found {count} invalid timestamp value(s)")

    for column in ("event_sequence", "amount", "duration_ms"):
        events[column] = pd.to_numeric(events[column], errors="coerce")
        if events[column].isna().any():
            count = int(events[column].isna().sum())
            raise ValueError(f"Column '{column}' contains {count} invalid numeric value(s)")

    events["event_sequence"] = events["event_sequence"].astype(int)
    events["_source_order"] = np.arange(len(events))
    events["duplicate_event"] = events.duplicated(EVENT_SIGNATURE, keep=False)
    events["unknown_event"] = ~events["event_type"].isin(KNOWN_EVENTS)

    sequence_order = events.sort_values(["session_id", "event_sequence", "_source_order"])
    regressed = sequence_order.groupby("session_id")["timestamp_utc"].diff().lt(pd.Timedelta(0))
    out_of_order_sessions = set(sequence_order.loc[regressed, "session_id"])
    events["out_of_order_event"] = events["session_id"].isin(out_of_order_sessions)
    return events


def _first_nonblank(values: pd.Series) -> str:
    nonblank = values.astype(str).loc[values.astype(str).str.strip().ne("")]
    return nonblank.iloc[0] if not nonblank.empty else ""


def reconstruct_sessions(events: pd.DataFrame) -> pd.DataFrame:
    """Collapse raw events into one record per checkout attempt."""
    missing = REQUIRED_COLUMNS - set(events.columns)
    if missing:
        raise ValueError(f"Events were not validated; missing: {', '.join(sorted(missing))}")

    prepared = events.copy()
    for flag in ("duplicate_event", "out_of_order_event", "unknown_event"):
        if flag not in prepared:
            prepared[flag] = False
    prepared = prepared.sort_values(["session_id", "event_sequence", "_source_order" if "_source_order" in prepared else "event_id"])
    deduplicated = prepared.drop_duplicates(EVENT_SIGNATURE, keep="first")
    records: list[dict[str, Any]] = []

    for session_id, session_events in deduplicated.groupby("session_id", sort=False):
        source_events = prepared.loc[prepared["session_id"].eq(session_id)]
        event_types = set(session_events["event_type"])
        reached: dict[str, bool] = {}
        prior_reached = True
        for stage in CANONICAL_STAGES:
            reached[stage] = prior_reached and stage in event_types
            prior_reached = reached[stage]

        completed = reached["payment_completed"] and bool(
            session_events.loc[session_events["event_type"].eq("payment_completed"), "status"].eq("success").any()
        )
        authorization_rows = session_events.loc[session_events["event_type"].eq("authorization_response")]
        if completed:
            outcome = "success"
        elif authorization_rows["status"].eq("failed").any():
            outcome = "failed"
        elif authorization_rows["status"].eq("pending").any():
            outcome = "pending"
        else:
            outcome = "abandoned"

        next_missing = next((stage for stage in CANONICAL_STAGES if not reached[stage]), "")
        ordered_by_sequence = session_events.sort_values(["event_sequence", "_source_order" if "_source_order" in session_events else "event_id"])
        positive_gaps = ordered_by_sequence["timestamp_utc"].diff().dt.total_seconds().clip(lower=0)
        redirect = session_events.loc[session_events["event_type"].eq("3ds_redirect"), "timestamp_utc"]
        three_ds_end = session_events.loc[session_events["event_type"].eq("3ds_completed"), "timestamp_utc"]
        three_ds_seconds = np.nan
        if not redirect.empty and not three_ds_end.empty:
            three_ds_seconds = max(0.0, (three_ds_end.iloc[0] - redirect.iloc[0]).total_seconds())

        first = session_events.iloc[0]
        records.append(
            {
                "session_id": session_id,
                "started_at": session_events["timestamp_utc"].min(),
                "ended_at": session_events["timestamp_utc"].max(),
                "session_seconds": max(
                    0.0,
                    (session_events["timestamp_utc"].max() - session_events["timestamp_utc"].min()).total_seconds(),
                ),
                "payment_method": first["payment_method"],
                "currency": first["currency"],
                "country": first["country"],
                "processor": first["processor"],
                "device_type": first["device_type"],
                "browser": first["browser"],
                "amount": float(first["amount"]),
                "outcome": outcome,
                "completed": completed,
                "drop_off_before": next_missing,
                "decline_code": _first_nonblank(session_events["decline_code"]),
                "used_3ds": "3ds_redirect" in event_types,
                "three_ds_seconds": three_ds_seconds,
                "max_gap_seconds": float(positive_gaps.max()) if positive_gaps.notna().any() else 0.0,
                "has_duplicate": bool(source_events["duplicate_event"].any()),
                "has_out_of_order": bool(source_events["out_of_order_event"].any()),
                "has_unknown_event": bool(source_events["unknown_event"].any()),
                "event_count": len(session_events),
                **{f"reached_{stage}": value for stage, value in reached.items()},
            }
        )

    sessions = pd.DataFrame.from_records(records)
    if not sessions.empty:
        sessions["date"] = sessions["started_at"].dt.date
        sessions["day_of_week"] = sessions["started_at"].dt.day_name()
    return sessions.sort_values("started_at").reset_index(drop=True)


def apply_filters(sessions: pd.DataFrame, filters: Mapping[str, Any] | None = None) -> pd.DataFrame:
    """Apply inclusive date and categorical filters to session records."""
    filtered = sessions.copy()
    if not filters:
        return filtered
    for column, selected in filters.items():
        if selected is None or column not in filtered.columns:
            continue
        if column == "date_range":
            start, end = selected
            filtered = filtered.loc[filtered["date"].between(start, end)]
            continue
        values = [selected] if isinstance(selected, str) else list(selected)
        if values:
            filtered = filtered.loc[filtered[column].isin(values)]
    return filtered


def compute_funnel(
    sessions: pd.DataFrame,
    filters: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    """Return reached, conversion, and transition drop-off metrics."""
    cohort = apply_filters(sessions, filters)
    rows: list[dict[str, Any]] = []
    previous_count = len(cohort)
    for index, stage in enumerate(CANONICAL_STAGES):
        count = int(cohort[f"reached_{stage}"].sum())
        denominator = len(cohort) if index == 0 else previous_count
        dropoff = max(0, denominator - count)
        rows.append(
            {
                "stage": stage,
                "reached_sessions": count,
                "conversion_from_previous_pct": 100.0 * count / denominator if denominator else 0.0,
                "dropoff_from_previous_count": dropoff,
                "dropoff_from_previous_pct": 100.0 * dropoff / denominator if denominator else 0.0,
                "overall_conversion_pct": 100.0 * count / len(cohort) if len(cohort) else 0.0,
            }
        )
        previous_count = count
    return pd.DataFrame(rows)


def segment_funnel(sessions: pd.DataFrame, dimensions: str | Iterable[str]) -> pd.DataFrame:
    """Calculate stage metrics independently for each requested cohort."""
    dimensions = [dimensions] if isinstance(dimensions, str) else list(dimensions)
    invalid = [dimension for dimension in dimensions if dimension not in sessions.columns]
    if invalid:
        raise ValueError(f"Unknown segmentation dimensions: {', '.join(invalid)}")
    rows: list[pd.DataFrame] = []
    grouper: str | list[str] = dimensions[0] if len(dimensions) == 1 else dimensions
    for group_values, cohort in sessions.groupby(grouper, dropna=False):
        values = (group_values,) if len(dimensions) == 1 else tuple(group_values)
        funnel = compute_funnel(cohort)
        for dimension, value in zip(dimensions, values):
            funnel[dimension] = value
        funnel["total_sessions"] = len(cohort)
        rows.append(funnel)
    if not rows:
        return pd.DataFrame(columns=[*dimensions, "stage", "reached_sessions"])
    return pd.concat(rows, ignore_index=True)


def _rate_candidate(
    sessions: pd.DataFrame,
    dimension: str,
    minimum_size: int,
    category: str,
    recommendation: str,
) -> dict[str, Any] | None:
    summary = (
        sessions.groupby(dimension, dropna=False)
        .agg(total=("session_id", "size"), incomplete=("completed", lambda values: int((~values).sum())))
        .reset_index()
    )
    summary = summary.loc[summary["total"].ge(minimum_size)].copy()
    if summary.empty:
        return None
    summary["rate"] = summary["incomplete"] / summary["total"]
    worst = summary.sort_values(["rate", "incomplete"], ascending=False).iloc[0]
    baseline = float((~sessions["completed"]).mean())
    delta = float(worst["rate"] - baseline)
    if delta <= 0:
        return None
    cohort = str(worst[dimension])
    return {
        "category": category,
        "issue": f"{cohort} has elevated non-completion",
        "cohort": cohort,
        "affected_sessions": int(worst["incomplete"]),
        "rate_pct": 100 * float(worst["rate"]),
        "baseline_pct": 100 * baseline,
        "evidence": (
            f"{int(worst['incomplete'])} of {int(worst['total'])} sessions did not complete "
            f"({100 * float(worst['rate']):.1f}% vs {100 * baseline:.1f}% overall)."
        ),
        "recommendation": recommendation,
        "score": int(worst["incomplete"]) * max(delta, 0.01),
    }


def rank_root_causes(
    events: pd.DataFrame,
    sessions: pd.DataFrame,
    limit: int = 5,
) -> pd.DataFrame:
    """Rank distinct, evidence-backed causes of non-completion."""
    del events  # Reserved for future event-grain diagnostics; sessions contain current evidence.
    if sessions.empty:
        return pd.DataFrame()
    candidates: list[dict[str, Any]] = []
    failed = sessions.loc[sessions["outcome"].eq("failed") & sessions["decline_code"].ne("")]
    if not failed.empty:
        declines = failed["decline_code"].value_counts()
        code = str(declines.index[0])
        count = int(declines.iloc[0])
        recommendations = {
            "timeout": "Route retries away from the affected processor and add bounded automatic retry logic.",
            "insufficient_funds": "Offer another payment method immediately after a soft decline.",
            "fraud_suspected": "Review fraud-rule precision and provide a safe verification fallback.",
            "invalid_card": "Add earlier field validation and preserve checkout state for correction.",
        }
        candidates.append(
            {
                "category": "decline_code",
                "issue": f"{code.replace('_', ' ').title()} is the leading authorization decline",
                "cohort": code,
                "affected_sessions": count,
                "rate_pct": 100 * count / len(failed),
                "baseline_pct": 100 / max(1, failed["decline_code"].nunique()),
                "evidence": f"{count} of {len(failed)} failed authorizations ({100 * count / len(failed):.1f}%) returned {code}.",
                "recommendation": recommendations.get(code, "Review processor routing and recovery handling for this decline."),
                "score": count,
            }
        )

    for args in (
        ("processor", 25, "processor", "Review routing health, timeouts, and failover for this processor."),
        ("payment_method", 25, "payment_method", "Optimize the payment-method-specific flow and offer a fallback method."),
        ("country", 25, "geography", "Inspect local method availability, currency handling, and regional processor performance."),
    ):
        candidate = _rate_candidate(sessions, *args)
        if candidate:
            candidates.append(candidate)

    device_browser = sessions.assign(device_browser=sessions["device_type"] + " / " + sessions["browser"])
    candidate = _rate_candidate(
        device_browser,
        "device_browser",
        30,
        "device_browser",
        "Reproduce and simplify the checkout form on this device/browser combination.",
    )
    if candidate:
        candidates.append(candidate)

    three_ds = sessions.loc[sessions["used_3ds"]]
    three_ds_abandoned = three_ds.loc[
        three_ds["outcome"].eq("abandoned") & three_ds["drop_off_before"].eq("payment_completed")
    ]
    if not three_ds.empty and not three_ds_abandoned.empty:
        rate = len(three_ds_abandoned) / len(three_ds)
        non_three_ds_cards = sessions.loc[
            sessions["payment_method"].str.startswith("card_") & ~sessions["used_3ds"]
        ]
        card_baseline = float(non_three_ds_cards["outcome"].eq("abandoned").mean())
        candidates.append(
            {
                "category": "3ds",
                "issue": "3DS introduces a concentrated abandonment point",
                "cohort": "3DS card sessions",
                "affected_sessions": len(three_ds_abandoned),
                "rate_pct": 100 * rate,
                "baseline_pct": 100 * card_baseline,
                "evidence": (
                    f"{len(three_ds_abandoned)} of {len(three_ds)} 3DS sessions ({100 * rate:.1f}%) "
                    f"were abandoned vs {100 * card_baseline:.1f}% of card sessions without 3DS."
                ),
                "recommendation": "Reduce redirect latency, retain checkout context, and instrument issuer-return failures.",
                "score": len(three_ds_abandoned) * max(rate, 0.01),
            }
        )

    daily = (
        sessions.groupby("date")
        .agg(total=("session_id", "size"), failures=("outcome", lambda values: int(values.eq("failed").sum())))
        .reset_index()
    )
    daily["rate"] = daily["failures"] / daily["total"]
    worst_day = daily.sort_values(["rate", "failures"], ascending=False).iloc[0]
    baseline_failure = float(sessions["outcome"].eq("failed").mean())
    if float(worst_day["rate"]) > baseline_failure:
        candidates.append(
            {
                "category": "time",
                "issue": f"Authorization failures spike on {worst_day['date']}",
                "cohort": str(worst_day["date"]),
                "affected_sessions": int(worst_day["failures"]),
                "rate_pct": 100 * float(worst_day["rate"]),
                "baseline_pct": 100 * baseline_failure,
                "evidence": f"{int(worst_day['failures'])} of {int(worst_day['total'])} sessions failed ({100 * float(worst_day['rate']):.1f}% vs {100 * baseline_failure:.1f}% overall).",
                "recommendation": "Correlate the spike with processor incidents and enable automated cohort-level alerting.",
                "score": int(worst_day["failures"]) * max(float(worst_day["rate"]) - baseline_failure, 0.01),
            }
        )

    return (
        pd.DataFrame(candidates)
        .sort_values(["score", "affected_sessions"], ascending=False)
        .head(limit)
        .drop(columns="score")
        .reset_index(drop=True)
    )


def detect_anomalies(events: pd.DataFrame, sessions: pd.DataFrame) -> pd.DataFrame:
    """Detect data-quality, latency, and cohort-level decline anomalies."""
    del events
    anomalies: list[dict[str, Any]] = []
    total = max(len(sessions), 1)
    checks = [
        ("duplicate_events", sessions["has_duplicate"], "Sessions contain repeated logical events."),
        ("out_of_order_events", sessions["has_out_of_order"], "Event timestamps regress relative to source sequence."),
        ("long_event_gap", sessions["max_gap_seconds"].gt(15 * 60), "A checkout step took more than 15 minutes."),
        ("slow_3ds", sessions["three_ds_seconds"].gt(8), "3DS completion took more than eight seconds."),
    ]
    for anomaly_type, mask, evidence in checks:
        count = int(mask.sum())
        if count:
            anomalies.append(
                {
                    "anomaly_type": anomaly_type,
                    "scope": "sessions",
                    "count": count,
                    "rate_pct": 100 * count / total,
                    "severity": "high" if anomaly_type in {"long_event_gap", "slow_3ds"} else "medium",
                    "evidence": evidence,
                }
            )

    cohort = (
        sessions.groupby(["date", "processor"])
        .agg(total=("session_id", "size"), failures=("outcome", lambda values: int(values.eq("failed").sum())))
        .reset_index()
    )
    processor_baseline = sessions.groupby("processor")["outcome"].apply(lambda values: float(values.eq("failed").mean()))
    for row in cohort.loc[cohort["total"].ge(10)].itertuples(index=False):
        baseline = float(processor_baseline.loc[row.processor])
        if baseline <= 0 or baseline >= 1:
            continue
        rate = row.failures / row.total
        standard_error = np.sqrt(baseline * (1 - baseline) / row.total)
        z_score = (rate - baseline) / standard_error if standard_error else 0.0
        if z_score >= 2:
            anomalies.append(
                {
                    "anomaly_type": "decline_rate_spike",
                    "scope": f"{row.processor} on {row.date}",
                    "count": int(row.failures),
                    "rate_pct": 100 * rate,
                    "severity": "high",
                    "evidence": f"Failure rate is {rate:.1%} versus {baseline:.1%} for this processor overall (z={z_score:.1f}).",
                }
            )
    return pd.DataFrame(anomalies).sort_values(["severity", "count"], ascending=[True, False]).reset_index(drop=True)
