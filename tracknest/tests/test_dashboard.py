from unittest.mock import patch

from bot import dashboard


def _summary(total=0.0, need=0.0, treat=0.0, unknown=0.0, categories=None):
    return {
        "total": total, "need": need, "treat": treat, "unknown": unknown,
        "top_items": [], "top_categories": categories or [],
    }


def sample_data(**overrides) -> dict:
    """A fully-populated build_dashboard_data() result, for render tests."""
    data = {
        "month_label": "September 2026", "day": 23, "days_in_month": 30,
        "spent": 120.0, "projected": 156.5,
        "budget": {"budget": 200.0, "spent": 120.0, "pct": 60.0},
        "mix": {"need": 70.0, "treat": 40.0, "unknown": 10.0},
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


@patch("bot.dashboard.metrics.get_months_with_spending", return_value=[(2026, 8), (2026, 9)])
@patch("bot.dashboard.crud.get_item", return_value=None)
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
    categories = [{"category": f"C{i}", "total": float(20 - i)} for i in range(dashboard._MAX_CATEGORIES + 2)]
    mock_summary.return_value = _summary(total=52.0, treat=20.0, categories=categories)
    mock_pace.return_value = {"days_elapsed": 23, "days_in_month": 30, "projected": 70.0, "spent": 52.0}

    data = dashboard.build_dashboard_data()

    assert len(data["categories"]) == dashboard._MAX_CATEGORIES
    folded = categories[dashboard._MAX_CATEGORIES - 1:]
    assert data["categories"][-1] == {"name": "Other", "total": sum(c["total"] for c in folded)}
    assert data["spent"] == 52.0
    assert data["projected"] == 70.0
    assert data["mix"]["treat"] == 20.0


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
        mix={"need": 0.0, "treat": 0.0, "unknown": 0.0},
        categories=[], trips={"count": 0, "avg_basket": None, "by_store": []},
        daily=[0.0] * 30, rising=[], running_low=[], shopping_list=[],
        health={"checkin_pending": [], "spare_alert_pending": [], "unprofiled": []},
        daily_cost={"items": [], "ready": 0, "tracked": 0},
    ))

    assert "No purchases logged yet" in html
    assert "/set_budget" in html
    assert "All caught up" in html


def test_render_offers_every_month_and_opens_on_the_latest():
    month_keys = ("month_label", "day", "days_in_month", "spent", "projected", "budget",
                  "mix", "categories", "trips", "daily")
    september = {k: v for k, v in sample_data().items() if k in month_keys} | {"key": "2026-09", "is_current": True}
    august = september | {"key": "2026-08", "is_current": False, "month_label": "August 2026",
                          "day": 31, "days_in_month": 31, "spent": 90.0, "projected": None,
                          "daily": [0.0] * 31}
    html = dashboard.render_dashboard_html(sample_data(months=[august, september]))

    assert '<label for="m0">Aug</label>' in html and '<label for="m1">Sep</label>' in html
    assert 'id="m1" class="month-radio" checked' in html
    assert "Spent in August" in html and "Month closed" in html and "August 2026 · closed" in html
    assert "€90.00" in html


def test_largest_category_bar_never_overflows():
    html = dashboard.render_dashboard_html(sample_data(
        categories=[{"name": "Clothing", "total": 35.9}, {"name": "Other", "total": 55.82}],
    ))
    assert "width:100.0%" in html and "width:155" not in html


def test_spare_alerts_are_shopping_and_attention_shows_only_open_questions():
    html = dashboard.render_dashboard_html(sample_data(
        health={"checkin_pending": [], "spare_alert_pending": ["LEERDAMMER CAR.", "Milram Käse Scheiben"],
                "unprofiled": ["X01"]},
        readable={"LEERDAMMER CAR.": "cheese", "Milram Käse Scheiben": "cheese", "X01": "X01"},
    ))
    assert html.count("🔁 cheese") == 1  # one spare per product, by its readable name
    assert "LEERDAMMER" not in html
    assert "Questions waiting for you" in html and "<span class=chip>X01</span>" in html
    assert "Buy a spare" not in html
