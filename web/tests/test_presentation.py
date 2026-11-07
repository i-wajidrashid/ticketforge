"""Pure presentation helpers: template filters and the sales chart geometry."""

from __future__ import annotations

import itertools
from datetime import date, timedelta

import pytest
from django.utils import timezone

from web.events.templatetags import tf
from web.organizers.charts import MAX_DAYS, daily_revenue_chart


@pytest.mark.parametrize(
    ("cents", "currency", "expected"),
    [
        (4500, "USD", "$45"),
        (4550, "USD", "$45.50"),
        (123456789, "USD", "$1,234,567.89"),
        (-500, "USD", "-$5"),
        (1000, "eur", "EUR 10"),
        (None, "USD", ""),
        ("not a number", "USD", ""),
    ],
)
def test_money(cents, currency, expected):
    assert tf.money(cents, currency) == expected


def test_percent_and_poster_variant():
    assert tf.percent(0.256) == "26%"
    assert tf.percent(None) == "0%"
    assert tf.poster_variant(7) == 3
    assert tf.poster_variant("x") == 0


def test_ago_reads_naturally():
    now = timezone.now()
    assert tf.ago(None) == ""
    assert tf.ago(now - timedelta(seconds=5)) == "just now"
    assert tf.ago(now - timedelta(minutes=3, seconds=10)) == "3\xa0minutes ago"
    assert tf.ago(now - timedelta(days=2, hours=5)) == "2\xa0days ago"


def test_icons_are_decorative_unless_labelled():
    assert 'aria-hidden="true"' in tf.icon("calendar")
    labelled = tf.icon("alert", "Warning")
    assert 'role="img"' in labelled and 'aria-label="Warning"' in labelled
    assert tf.icon("does-not-exist") == ""


# ------------------------------------------------------------------- chart


def _day(offset: int, cents: int, orders: int = 1) -> dict:
    return {
        "day": (date(2026, 11, 20) + timedelta(days=offset)).isoformat(),
        "revenue_cents": cents,
        "orders": orders,
    }


def test_no_sales_means_no_chart():
    assert daily_revenue_chart([], date(2026, 11, 20)) is None


def test_quiet_days_are_zero_height_slots_not_gaps():
    chart = daily_revenue_chart([_day(-3, 5000), _day(0, 2000)], date(2026, 11, 20))
    assert chart is not None
    assert [bar.value for bar in chart["bars"]] == [5000, 0, 0, 2000]
    assert [bool(bar.path) for bar in chart["bars"]] == [True, False, False, True]
    assert chart["total_cents"] == 7000
    # The top gridline is a round number at or above the tallest bar.
    assert chart["gridlines"][-1]["value"] >= 5000
    assert chart["gridlines"][0]["value"] == 0


def test_bars_are_anchored_to_the_baseline():
    chart = daily_revenue_chart([_day(0, 9000)], date(2026, 11, 20))
    assert chart is not None
    (bar,) = chart["bars"]
    assert bar.y + bar.height == pytest.approx(chart["baseline"])
    assert bar.center == pytest.approx(bar.x + bar.width / 2)
    assert bar.show_label


def test_long_histories_are_capped_to_the_last_month():
    sales = [_day(-offset, 1000) for offset in range(60)]
    chart = daily_revenue_chart(sales, date(2026, 11, 20))
    assert chart is not None
    assert len(chart["bars"]) == MAX_DAYS
    assert chart["bars"][-1].day == date(2026, 11, 20)
    labelled = [bar for bar in chart["bars"] if bar.show_label]
    assert 2 <= len(labelled) <= 7  # sparse axis labels, never one per bar


def test_today_is_always_labelled_without_colliding():
    sales = [_day(-offset, 1000) for offset in range(27)]
    chart = daily_revenue_chart(sales, date(2026, 11, 20))
    assert chart is not None
    labelled = [index for index, bar in enumerate(chart["bars"]) if bar.show_label]
    assert labelled[-1] == len(chart["bars"]) - 1
    gaps = [b - a for a, b in itertools.pairwise(labelled)]
    assert min(gaps) >= 3
