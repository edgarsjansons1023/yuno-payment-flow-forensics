"""Deterministic synthetic payment-event generation for the challenge."""

from __future__ import annotations

import csv
import random
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

METHODS = (
    "card_visa",
    "card_mastercard",
    "gcash",
    "mpesa",
    "bank_transfer_local",
    "bnpl_atome",
)
COUNTRY_CURRENCY = {
    "Philippines": "PHP",
    "Kenya": "KES",
    "Singapore": "SGD",
    "Cross-border": "USD",
}
FIELDNAMES = (
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
)


@dataclass
class SessionProfile:
    session_id: str
    started_at: datetime
    payment_method: str
    country: str
    currency: str
    processor: str
    device_type: str
    browser: str
    amount: float
    uses_3ds: bool
    risk: float
    outcome: str = "success"


def _weighted_choice(rng: random.Random, values: list[str], weights: list[float]) -> str:
    return rng.choices(values, weights=weights, k=1)[0]


def _country_for_method(rng: random.Random, method: str) -> str:
    if method == "gcash":
        return _weighted_choice(rng, ["Philippines", "Cross-border"], [0.9, 0.1])
    if method == "mpesa":
        return _weighted_choice(rng, ["Kenya", "Cross-border"], [0.9, 0.1])
    if method == "bnpl_atome":
        return _weighted_choice(rng, ["Singapore", "Philippines"], [0.65, 0.35])
    return _weighted_choice(
        rng,
        ["Philippines", "Kenya", "Singapore", "Cross-border"],
        [0.34, 0.26, 0.24, 0.16],
    )


def _processor_for_method(rng: random.Random, method: str) -> str:
    if method.startswith("card_"):
        return _weighted_choice(rng, ["gateway_alpha", "gateway_beta"], [0.58, 0.42])
    if method in {"gcash", "mpesa"}:
        return "wallet_hub"
    if method == "bank_transfer_local":
        return "bank_rail"
    return "bnpl_connect"


def _build_profiles(session_count: int, days: int, seed: int) -> list[SessionProfile]:
    rng = random.Random(seed)
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    profiles: list[SessionProfile] = []

    for index in range(session_count):
        method = _weighted_choice(rng, list(METHODS), [30, 22, 18, 14, 10, 6])
        country = _country_for_method(rng, method)
        processor = _processor_for_method(rng, method)
        device = _weighted_choice(rng, ["mobile", "desktop"], [68, 32])
        browser = _weighted_choice(rng, ["Chrome", "Safari", "Firefox", "Edge"], [55, 29, 10, 6])
        day = rng.randrange(days)
        minute = rng.randrange(24 * 60)
        second = rng.randrange(60)
        started_at = start + timedelta(days=day, minutes=minute, seconds=second)
        uses_3ds = method.startswith("card_") and rng.random() < 0.72

        risk = rng.random()
        if processor == "gateway_beta":
            risk += 0.22
        if processor == "gateway_beta" and started_at.weekday() >= 5:
            risk += 0.35
        if started_at.date().isoformat() == "2026-09-10" and processor == "gateway_beta":
            risk += 0.55
        if uses_3ds:
            risk += 0.12
        if device == "mobile" and browser == "Safari":
            risk += 0.28
        if method == "bank_transfer_local":
            risk += 0.18

        profiles.append(
            SessionProfile(
                session_id=f"ses_{index + 1:04d}",
                started_at=started_at,
                payment_method=method,
                country=country,
                currency=COUNTRY_CURRENCY[country],
                processor=processor,
                device_type=device,
                browser=browser,
                amount=round(rng.uniform(8, 480), 2),
                uses_3ds=uses_3ds,
                risk=risk,
            )
        )

    # The riskiest 41% are incomplete, preserving an exact 59% completion rate.
    incomplete_count = round(session_count * 0.41)
    incomplete = sorted(profiles, key=lambda profile: profile.risk, reverse=True)[:incomplete_count]
    for profile in incomplete:
        roll = rng.random()
        if profile.payment_method == "bank_transfer_local" and roll < 0.62:
            profile.outcome = "pending"
        elif profile.uses_3ds and roll < 0.60:
            profile.outcome = "abandoned_3ds"
        elif profile.device_type == "mobile" and profile.browser == "Safari" and roll < 0.58:
            profile.outcome = "abandoned_details"
        elif roll < 0.08:
            profile.outcome = "abandoned_method"
        elif roll < 0.21:
            profile.outcome = "abandoned_details"
        elif roll < 0.31:
            profile.outcome = "abandoned_authorization"
        elif profile.processor == "gateway_beta" and (
            profile.started_at.weekday() >= 5
            or profile.started_at.date().isoformat() == "2026-09-10"
        ):
            profile.outcome = "failed_timeout"
        elif roll < 0.69:
            profile.outcome = "failed_insufficient_funds"
        elif roll < 0.86:
            profile.outcome = "failed_fraud_suspected"
        else:
            profile.outcome = "failed_invalid_card"

    return profiles


def _event(
    profile: SessionProfile,
    event_id: int,
    sequence: int,
    timestamp: datetime,
    event_type: str,
    status: str,
    duration_ms: int,
    decline_code: str = "",
) -> dict[str, object]:
    return {
        "session_id": profile.session_id,
        "event_id": f"evt_{event_id:06d}",
        "event_sequence": sequence,
        "timestamp_utc": timestamp.isoformat().replace("+00:00", "Z"),
        "event_type": event_type,
        "payment_method": profile.payment_method,
        "currency": profile.currency,
        "country": profile.country,
        "processor": profile.processor,
        "status": status,
        "decline_code": decline_code,
        "device_type": profile.device_type,
        "browser": profile.browser,
        "amount": f"{profile.amount:.2f}",
        "duration_ms": duration_ms,
    }


def generate_events(
    session_count: int = 1000,
    days: int = 14,
    seed: int = 20260921,
) -> list[dict[str, object]]:
    """Generate repeatable event rows with known conversion and anomaly patterns."""
    if session_count < 10:
        raise ValueError("session_count must be at least 10")
    if days < 1:
        raise ValueError("days must be positive")

    rng = random.Random(seed + 1)
    profiles = _build_profiles(session_count, days, seed)
    long_gap_sessions = {profile.session_id for profile in rng.sample(profiles, max(1, session_count // 40))}
    duplicate_sessions = {profile.session_id for profile in rng.sample(profiles, max(1, session_count // 60))}
    out_of_order_sessions = {profile.session_id for profile in rng.sample(profiles, max(1, session_count // 80))}
    rows: list[dict[str, object]] = []
    next_event_id = 1

    for profile in profiles:
        timestamp = profile.started_at
        sequence = 1
        journey: list[tuple[str, str, str]] = [("checkout_initiated", "started", "")]

        if profile.outcome != "abandoned_method":
            journey.append(("method_selected", "success", ""))
        if profile.outcome not in {"abandoned_method"}:
            journey.append(("details_entered", "abandoned" if profile.outcome == "abandoned_details" else "success", ""))
        if profile.outcome not in {"abandoned_method", "abandoned_details"}:
            journey.append(
                (
                    "authorization_requested",
                    "abandoned" if profile.outcome == "abandoned_authorization" else "submitted",
                    "",
                )
            )
        if profile.uses_3ds and profile.outcome not in {
            "abandoned_method",
            "abandoned_details",
            "abandoned_authorization",
        }:
            journey.append(("3ds_redirect", "redirected", ""))
            if profile.outcome == "abandoned_3ds":
                journey.append(("3ds_completed", "abandoned", "3ds_abandoned"))
            else:
                journey.append(("3ds_completed", "success", ""))
        if profile.outcome in {"success", "pending"} or profile.outcome.startswith("failed_"):
            if profile.outcome == "success":
                journey.append(("authorization_response", "success", ""))
                journey.append(("payment_completed", "success", ""))
            elif profile.outcome == "pending":
                journey.append(("authorization_response", "pending", ""))
            else:
                decline_code = profile.outcome.removeprefix("failed_")
                journey.append(("authorization_response", "failed", decline_code))

        session_rows: list[dict[str, object]] = []
        for event_type, status, decline_code in journey:
            base_duration = rng.randint(180, 1800)
            if event_type in {"3ds_redirect", "3ds_completed"}:
                base_duration = rng.randint(1100, 5200)
                if profile.outcome == "abandoned_3ds":
                    base_duration += rng.randint(4500, 9000)
            if event_type == "authorization_response" and decline_code == "timeout":
                base_duration = rng.randint(6500, 12000)

            gap_seconds = max(1, round(base_duration / 1000)) + rng.randint(1, 10)
            if profile.session_id in long_gap_sessions and sequence == 3:
                gap_seconds += rng.randint(16 * 60, 35 * 60)
            timestamp += timedelta(seconds=gap_seconds)

            event_timestamp = timestamp
            if profile.session_id in out_of_order_sessions and sequence == 3:
                event_timestamp -= timedelta(minutes=4)

            session_rows.append(
                _event(
                    profile,
                    next_event_id,
                    sequence,
                    event_timestamp,
                    event_type,
                    status,
                    base_duration,
                    decline_code,
                )
            )
            next_event_id += 1
            sequence += 1

        if profile.session_id in duplicate_sessions and len(session_rows) >= 2:
            duplicate = dict(session_rows[-2])
            duplicate["event_id"] = f"evt_{next_event_id:06d}"
            next_event_id += 1
            session_rows.append(duplicate)

        rows.extend(session_rows)

    return rows


def write_events(rows: Iterable[dict[str, object]], destination: str | Path) -> Path:
    """Write event rows to CSV and return the resolved output path."""
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    return path.resolve()


def main() -> None:
    rows = generate_events()
    destination = write_events(rows, "data/transaction_events.csv")
    print(f"Wrote {len(rows):,} events to {destination}")


if __name__ == "__main__":
    main()
