from unittest.mock import patch

from bot import dashboard


def _summary(total=0.0, essential=0.0, luxury=0.0, necessity=0.0, unclassified=0.0):
    return {
        "total": total, "essential": essential, "luxury": luxury,
        "necessity": necessity, "unclassified": unclassified,
        "top_items": [], "top_categories": [],
    }


@patch("bot.dashboard.metrics.get_price_trends", return_value=[])
@patch("bot.dashboard.metrics.get_running_low", return_value=[])
@patch("bot.dashboard.metrics.get_budget_status")
@patch("bot.dashboard.metrics.get_month_pace")
@patch("bot.dashboard.metrics.get_spending_summary")
def test_build_dashboard_data_converts_to_percentages(
    mock_summary, mock_pace, mock_budget, _mock_low, _mock_trends
):
    mock_summary.return_value = _summary(total=100.0, essential=55.0, luxury=33.0, necessity=12.0)
    mock_pace.return_value = {"days_elapsed": 22, "days_in_month": 30}
    mock_budget.return_value = {"budget": 80.0, "spent": 65.6, "pct": 82.0}

    data = dashboard.build_dashboard_data()

    assert data["budget_pct"] == 82.0
    assert round(data["month_pct"], 1) == round(22 / 30 * 100, 1)
    assert {m["label"]: round(m["pct"]) for m in data["mix"]} == {
        "Essential": 55, "Treats": 33, "Necessity": 12,
    }
    assert data["unclassified_pct"] == 0.0


@patch("bot.dashboard.metrics.get_price_trends", return_value=[])
@patch("bot.dashboard.metrics.get_running_low", return_value=[])
@patch("bot.dashboard.metrics.get_budget_status", return_value=None)
@patch("bot.dashboard.metrics.get_month_pace")
@patch("bot.dashboard.metrics.get_spending_summary")
def test_build_dashboard_data_no_budget_and_unclassified_spend(
    mock_summary, mock_pace, _mock_budget, _mock_low, _mock_trends
):
    mock_summary.return_value = _summary(total=10.0, unclassified=10.0)
    mock_pace.return_value = {"days_elapsed": 1, "days_in_month": 30}

    data = dashboard.build_dashboard_data()

    assert data["budget_pct"] is None
    assert data["mix"] == []
    assert data["unclassified_pct"] == 100.0


@patch("bot.dashboard.metrics.get_price_trends", return_value=[])
@patch("bot.dashboard.metrics.get_budget_status", return_value=None)
@patch("bot.dashboard.metrics.get_month_pace", return_value={"days_elapsed": 1, "days_in_month": 30})
@patch("bot.dashboard.metrics.get_spending_summary", return_value=_summary())
@patch("bot.dashboard.metrics.get_running_low")
def test_build_dashboard_data_computes_shelf_life_remaining_pct(mock_low, *_mocks):
    mock_low.return_value = [
        {"name": "Milk", "days_left": 2, "run_out_date": "2026-10-01", "shelf_life_days": 8},
    ]
    data = dashboard.build_dashboard_data()
    assert data["running_low"] == [{"name": "Milk", "pct_remaining": 25.0}]


def test_render_dashboard_html_handles_empty_sections():
    html = dashboard.render_dashboard_html({
        "month_abbr": "SEP", "budget_pct": None, "month_pct": 50.0,
        "mix": [], "unclassified_pct": None, "rising": [], "running_low": [],
    })
    assert "TrackNest" in html
    assert "Nothing classified yet" in html
    assert "Nothing creeping up" in html
    assert "Nothing running low" in html
