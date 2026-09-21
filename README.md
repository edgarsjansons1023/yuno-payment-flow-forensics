# Payment Flow Forensics

A local Streamlit dashboard that reconstructs checkout journeys from raw payment events, quantifies every funnel transition, and ranks the patterns driving non-completion. The included deterministic dataset models HorizonMarket's 41% checkout loss across Southeast Asia and East Africa.

## Executive summary

The demonstration data contains 1,000 checkout attempts and 6,174 raw events over 14 days. Exactly 590 sessions complete payment. The top of the funnel is healthy: 99.2% select a payment method and 95.9% reach authorization. The decisive loss occurs after authorization begins, where 369 of 959 sessions fail to complete. Those sessions include declines, explicit abandonment, and pending bank transfers, so the dashboard keeps those outcomes separate.

The most actionable issue is the card flow. Among card sessions routed through 3DS, 123 of 398 (30.9%) are abandoned, compared with 12.5% abandonment for card sessions without 3DS. `gateway_beta` completes only 33.3% of its 216 sessions, while `gateway_alpha` completes 60.7%. Mobile Safari is another concentrated problem: 135 of 202 sessions do not complete. HorizonMarket should first instrument and shorten the 3DS redirect/return path, shift or retry traffic when `gateway_beta` degrades, and reproduce the checkout form on mobile Safari. Fraud rules should then be reviewed because `fraud_suspected` is the leading decline code, accounting for 62 of 211 failed authorizations.

These findings are intentionally encoded in synthetic data for demonstration; they are not claims about a production merchant.

## Run locally

Python 3.11 or 3.12 is recommended.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Streamlit prints the local URL, normally `http://localhost:8501`. The dashboard loads the included data automatically. Use **Load event log** in the sidebar to analyze another CSV with the same schema.

To regenerate the demonstration data with the fixed seed:

```bash
python generate_data.py
```

To run the verification suite:

```bash
pytest -q
```

## What the dashboard shows

- **Overview** — headline outcomes, a five-stage funnel, transition losses, and daily completion trend.
- **Segments** — completion comparisons across payment method, geography, currency, device, browser, and processor, plus a country-by-method heatmap.
- **Root causes** — five ranked findings with counts, rates, baselines, and recommended actions.
- **Anomalies** — long event gaps, slow 3DS journeys, decline spikes, duplicate events, and timestamp-order problems.
- **Session drill-down** — the session-level evidence behind the aggregates, with outcome search and CSV export.

Every sidebar filter updates the metrics, charts, root causes, anomalies, and drill-down together.

## Funnel semantics

The mandatory funnel is:

1. `checkout_initiated`
2. `method_selected`
3. `details_entered`
4. `authorization_requested`
5. `payment_completed`

A stage counts only when all preceding mandatory stages exist in that session. This prevents a malformed late event from inflating conversion. `3ds_redirect`, `3ds_completed`, and `authorization_response` are diagnostic sub-steps because 3DS is optional and an authorization can end in success, failure, or pending status.

The dashboard uses these outcome definitions:

- **Success:** a contiguous journey ending in a successful `payment_completed` event.
- **Failed:** an `authorization_response` with failed status and a decline code.
- **Pending:** an asynchronous authorization still awaiting confirmation.
- **Abandoned:** any other journey that stops before successful completion.

Transition drop-off is calculated against sessions that reached the immediately preceding mandatory stage. Overall conversion always uses all checkout sessions in the current filtered cohort.

## Input schema

Each CSV row represents one event and requires:

| Field | Meaning |
| --- | --- |
| `session_id`, `event_id`, `event_sequence` | Journey and event identity plus source order |
| `timestamp_utc`, `event_type`, `status` | Event timing and lifecycle state |
| `payment_method`, `processor` | Payment route |
| `country`, `currency` | Geographic cohort |
| `device_type`, `browser` | Client cohort |
| `decline_code` | Failure reason; blank for non-declines |
| `amount`, `duration_ms` | Transaction value and step latency |

Unknown event types are retained and flagged but do not alter the canonical funnel. Logical duplicates are flagged and removed from analytical counts. Timestamp regressions relative to `event_sequence` are flagged before events are chronologically interpreted.

## Demonstration data

`data/transaction_events.csv` is generated with a fixed seed and contains:

- 1,000 sessions from September 1–14, 2026, with a 59% completion rate.
- Visa, Mastercard, GCash, M-Pesa, local bank transfer, and Atome flows.
- PHP, KES, SGD, and USD transactions across four geographic cohorts.
- Successful, failed, pending, and abandoned sessions.
- Soft and hard declines, optional 3DS, processor degradation, mobile-browser friction, duplicates, out-of-order events, long gaps, and daily decline spikes.

The generator assigns incompletion to the highest-risk synthetic cohorts and then creates events consistent with those outcomes. This makes the analytical signals stable while preserving realistic variation in timestamps, amounts, devices, and failure reasons.

## Project structure

```text
app.py                            Streamlit dashboard
generate_data.py                  Dataset regeneration entry point
payment_funnel/analysis.py        Reconstruction, metrics, insights, anomalies
payment_funnel/generator.py       Deterministic synthetic event generator
data/transaction_events.csv       Ready-to-use demonstration data
tests/                            Behavioral and acceptance tests
```

## Limitations

- Session identity is supplied by the input rather than inferred across devices.
- Pending bank transfers are not treated as abandonment; production analysis should apply a payment-method-specific observation window.
- Anomaly thresholds are explainable heuristics for a small dataset, not a trained fraud or incident-detection model.
- Recommendations identify investigation priorities. Production changes should be confirmed against live processor, client telemetry, and experiment data.
