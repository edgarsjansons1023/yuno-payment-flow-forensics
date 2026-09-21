"""Build the browser-ready session dataset used by the Vercel dashboard."""

from pathlib import Path

from payment_funnel import load_and_validate_events, reconstruct_sessions

ROOT = Path(__file__).parent
SOURCE = ROOT / "data" / "transaction_events.csv"
DESTINATION = ROOT / "public" / "sessions.json"


def main() -> None:
    sessions = reconstruct_sessions(load_and_validate_events(SOURCE))
    sessions.to_json(DESTINATION, orient="records", date_format="iso", indent=2)
    print(f"Wrote {len(sessions):,} sessions to {DESTINATION}")


if __name__ == "__main__":
    main()
