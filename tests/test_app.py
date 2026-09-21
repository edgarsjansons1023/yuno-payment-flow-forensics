from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_dashboard_renders_without_exceptions():
    app_path = Path(__file__).parents[1] / "app.py"
    dashboard = AppTest.from_file(app_path, default_timeout=20).run()

    assert not dashboard.exception
    assert [tab.label for tab in dashboard.tabs] == [
        "Overview",
        "Segments",
        "Root causes",
        "Anomalies",
        "Session drill-down",
    ]
    assert [metric.value for metric in dashboard.metric[:5]] == ["1,000", "59.0%", "41.0%", "211", "28"]
