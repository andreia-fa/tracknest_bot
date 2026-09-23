from unittest.mock import patch

from bot import dashboard


def _summary(total=0.0, essential=0.0, luxury=0.0, necessity=0.0, unclassified=0.0, categories=None):
    return {
        "total": total, "essential": essential, "luxury": luxury,
        "necessity": necessity, "unclassified": unclassified,
        "top_items": [], "top_categories": categories or [],
    }


def sample_data(**overrides) -> dict:
    """A fully-populated build_dashboard_data() result, for render tests."""
    data = {
        "month_label": "September 2026", "day": 23, "days_in_month": 30,
        "spent": 120.0, "projected": 156.5,
        "budget": {"budget": 200.0, "spent": 120.0, "pct": 60.0},
        "mix": {"essential": 60.0, "luxury": 40.0, "necessity": 10.0, "unclassified": 10.0},
        "categories": [{"name": "Fruits/Veg", "total": 70.0}, {"name": "Snacks", "total": 50.0}],
        "trips": {"count": 4, "avg_basket": 30.0,
                  "by_store": [{"store": "REWE", "trips": 3, "total": 100.0},
                               {"store": None, "trips": 1, "total": 20.0}]},
        "daily": [0.0] * 20 + [50.0, 0.0, 70.0] + [0.0] * 7,
        "rising": [{"name": "Milk", "pct_change": 12.0, "latest_price": 1.12, "avg_price": 1.0}],
        "running_low": [{"name": "Eggs", "days_left": 2, "run_out_date": "2026-09-25", "shelf_life_days": 14}],
        "shopping_list": [{"name": "Broccoli", "quantity": 1, "category": "Fruits/Veg"}],
        "health": {"checkin_pending": [], "spare_alert_pending": [], "unprofiled": ["Sushi"]},
        "daily_cost": {"items": [], "ready": 0, "tracked": 3},
    }
    data.update(overrides)
    return data


@patch("bot.dashboard.metrics.get_daily_cost", return_value={"items": [], "ready": 0, "tracked": 0})
@patch("bot.dashboard.metrics.get_inventory_health")
@patch("bot.dashboard.shopping_list.get_all_items", return_value=[])
@patch("bot.dashboard.metrics.get_price_trends", return_value=[])
@patch("bot.dashboard.metrics.get_running_low", return_value=[])
@patch("bot.dashboard.metrics.get_daily_spend", return_value=[0.0] * 30)
@patch("bot.dashboard.metrics.get_shopping_trips")
@patch("bot.dashboard.metrics.get_budget_status", return_value=None)
@patch("bot.dashboard.metrics.get_month_pace")
@patch("bot.dashboard.metrics.get_spending_summary")
def test_build_dashboard_data_folds_extra_categories_into_other(mock_summary, mock_pace, *_mocks):
    categories = [{"category": f"C{i}", "total": float(10 - i)} for i in range(8)]
    mock_summary.return_value = _summary(total=52.0, luxury=20.0, categories=categories)
    mock_pace.return_value = {"days_elapsed": 23, "days_in_month": 30, "projected": 70.0, "spent": 52.0}

    data = dashboard.build_dashboard_data()

    assert len(data["categories"]) == dashboard._MAX_CATEGORIES
    assert data["categories"][-1] == {"name": "Other", "total": 5.0 + 4.0 + 3.0}
    assert data["spent"] == 52.0
    assert data["projected"] == 70.0
    assert data["mix"]["luxury"] == 20.0


def test_render_shows_the_headline_numbers():
    html = dashboard.render_dashboard_html(sample_data())

    assert "€120.00" in html
    assert "On pace for <strong>€156.50</strong>" in html
    assert "4 shopping trips" in html
    assert "REWE" in html and "Typed in by hand" in html
    assert "▲ 12%" in html
    assert "Broccoli" in html and "2d left" in html
    assert "Sushi" in html


def test_render_escapes_names_from_receipts():
    html = dashboard.render_dashboard_html(sample_data(
        shopping_list=[{"name": "<script>alert(1)</script>", "quantity": 1, "category": None}],
    ))

    assert "<script>alert(1)" not in html
    assert "&lt;script&gt;" in html


def test_render_empty_month_shows_prompts_not_errors():
    html = dashboard.render_dashboard_html(sample_data(
        spent=0.0, projected=None, budget=None,
        mix={"essential": 0.0, "luxury": 0.0, "necessity": 0.0, "unclassified": 0.0},
        categories=[], trips={"count": 0, "avg_basket": None, "by_store": []},
        daily=[0.0] * 30, rising=[], running_low=[], shopping_list=[],
        health={"checkin_pending": [], "spare_alert_pending": [], "unprofiled": []},
        daily_cost={"items": [], "ready": 0, "tracked": 0},
    ))

    assert "No purchases logged yet" in html
    assert "/set_budget" in html
    assert "All caught up" in html
