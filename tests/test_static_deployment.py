import json
from pathlib import Path


def test_vercel_dataset_matches_headline_metrics():
    dataset = Path(__file__).parents[1] / "public" / "sessions.json"
    sessions = json.loads(dataset.read_text(encoding="utf-8"))

    assert len(sessions) == 1000
    assert sum(session["completed"] for session in sessions) == 590
    assert len({session["payment_method"] for session in sessions}) >= 5
    assert len({session["country"] for session in sessions}) >= 3

