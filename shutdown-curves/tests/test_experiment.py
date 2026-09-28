import numpy as np
import pandas as pd
import pytest

from dc_shutdown.analysis import make_curves, on_fractions, period_labels
from dc_shutdown.data import (
    CARBON,
    PRICE,
    TZ,
    date_bounds,
    hourly_intervals,
    parse_entsoe,
    parse_ned,
)


def sample_frame(prices, carbon=None, start="2025-01-01"):
    index = pd.date_range(start, periods=len(prices), freq="h", tz=TZ).tz_convert("UTC")
    return pd.DataFrame(
        {PRICE: prices, CARBON: carbon if carbon is not None else [100] * len(prices)}, index=index
    )


def xml_period(curve="A03", resolution="PT15M", points=((1, 10), (3, 30))):
    encoded = "".join(
        f"<Point><position>{p}</position><price.amount>{v}</price.amount></Point>"
        for p, v in points
    )
    return f"""<Publication_MarketDocument xmlns="urn:test"><TimeSeries>
      <currency_Unit.name>EUR</currency_Unit.name><price_Measure_Unit.name>MWH</price_Measure_Unit.name>
      <curveType>{curve}</curveType><Period><timeInterval><start>2025-01-01T23:00Z</start>
      <end>2025-01-02T00:00Z</end></timeInterval><resolution>{resolution}</resolution>
      {encoded}</Period></TimeSeries></Publication_MarketDocument>""".encode()


def test_full_utc_time_and_variable_blocks():
    parsed = parse_entsoe(xml_period())
    assert parsed.start.iloc[0] == pd.Timestamp("2025-01-01T23:00Z")
    assert parsed[PRICE].tolist() == [10, 10, 30, 30]
    hours = hourly_intervals(parsed, PRICE)
    assert hours.iloc[0] == 20
    assert hours.index[0].tz_convert(TZ) == pd.Timestamp("2025-01-02T00:00", tz=TZ)


def test_missing_a01_points_are_not_forward_filled():
    parsed = parse_entsoe(xml_period(curve="A01"))
    assert len(parsed) == 2
    assert hourly_intervals(parsed, PRICE).isna().all()


def test_all_periods_are_parsed():
    root = xml_period().decode()
    period = root[root.index("<Period>") : root.index("</Period>") + len("</Period>")]
    second = period.replace("2025-01-01T23:00Z", "2025-01-02T00:00Z").replace(
        "<end>2025-01-02T00:00Z", "<end>2025-01-02T01:00Z"
    )
    assert len(parse_entsoe(root.replace("</TimeSeries>", second + "</TimeSeries>").encode())) == 8


def test_identical_overlaps_deduplicated_but_conflicts_fail():
    parsed = parse_entsoe(xml_period())
    assert hourly_intervals(pd.concat([parsed, parsed]), PRICE).iloc[0] == 20
    conflict = parsed.copy()
    conflict.loc[0, PRICE] = 99
    with pytest.raises(ValueError, match="Conflicting"):
        hourly_intervals(pd.concat([parsed, conflict]), PRICE)


def test_ned_units_and_null_rejection():
    row = {
        "point": "/v1/points/0",
        "type": "/v1/types/27",
        "activity": "/v1/activities/1",
        "classification": "/v1/classifications/2",
        "granularity": "/v1/granularities/5",
        "validfrom": "2025-01-01T00:00Z",
        "validto": "2025-01-01T01:00Z",
        "emissionfactor": 0.250,
    }
    assert parse_ned({"hydra:member": [row]})[CARBON].iloc[0] == 250
    row["emissionfactor"] = None
    with pytest.raises(ValueError, match="Missing"):
        parse_ned({"hydra:member": [row]})


def test_exact_savings_and_cross_objective_outcomes():
    frame = sample_frame([10, 20, 30, 40], [400, 300, 200, 100])
    curves = make_curves(frame)
    subset = curves[(curves.period == "daily") & (curves.requested_shutdown_fraction == 0.5)]
    price = subset[subset.objective == "price"].iloc[0]
    carbon = subset[subset.objective == "carbon"].iloc[0]
    assert price.electricity_cost_eur == 30
    assert price.attributed_co2_kg == 700
    assert price.electricity_saving_fraction == pytest.approx(0.7)
    assert carbon.electricity_cost_eur == 70
    assert carbon.attributed_co2_kg == 300
    assert carbon.carbon_saving_fraction == pytest.approx(0.7)


def test_exact_duration_budget():
    frame = sample_frame(list(range(24)))
    exact = on_fractions(frame, "daily", "price", 0.05)
    assert (1 - exact).sum() == pytest.approx(1.2)
    assert exact[-1] == 0
    assert exact[-2] == pytest.approx(0.8)


def test_daily_and_weekly_have_distinct_constraints():
    # Monday and Tuesday: all expensive hours happen Monday.
    frame = sample_frame([100] * 24 + [10] * 24, start="2025-01-06")
    daily = on_fractions(frame, "daily", "price", 0.5)
    weekly = on_fractions(frame, "weekly", "price", 0.5)
    assert daily[:24].sum() == daily[24:].sum() == 12
    assert weekly[:24].sum() == 0
    assert weekly[24:].sum() == 24
    assert daily @ frame[PRICE] == 1320
    assert weekly @ frame[PRICE] == 240


def test_calendar_groups_and_dst_durations():
    start, end = date_bounds("2025-09-28", "2026-09-28")
    idx = pd.date_range(start, end, freq="h", inclusive="left").tz_convert("UTC")
    counts = pd.Series(period_labels(idx, "daily")).value_counts()
    assert len(idx) == 8760
    assert counts["2025-10-26"] == 25
    assert counts["2026-03-29"] == 23
    assert period_labels(idx[:1], "weekly")[0] == "2025-W39"
    assert period_labels(idx[:1], "monthly")[0] == "2025-09"
    frame = pd.DataFrame({PRICE: np.arange(len(idx)), CARBON: 100}, index=idx)
    for period in ("daily", "weekly", "monthly"):
        assert on_fractions(frame, period, "price", 0.1).sum() == pytest.approx(0.9 * len(idx))


def test_negative_prices_endpoints_and_power_scaling():
    frame = sample_frame([-20, 10, 30, 80])
    curves = make_curves(frame)
    daily = curves[(curves.period == "daily") & (curves.objective == "price")]
    low = daily[daily.requested_shutdown_fraction == 0.75].iloc[0]
    assert low.electricity_cost_eur == -20
    assert low.electricity_saving_fraction == pytest.approx(1.2)
    assert daily.iloc[0].electricity_cost_fraction == 1
    assert daily.iloc[-1].electricity_cost_eur == 0
    assert daily.iloc[-1].uptime_fraction == 0
    bigger = make_curves(frame, power_mw=10)
    np.testing.assert_allclose(bigger.electricity_cost_eur, 10 * curves.electricity_cost_eur)
    np.testing.assert_allclose(bigger.attributed_co2_kg, 10 * curves.attributed_co2_kg)
    np.testing.assert_allclose(
        bigger.electricity_saving_fraction, curves.electricity_saving_fraction
    )


def test_nonpositive_baseline_has_undefined_cost_ratio():
    curves = make_curves(sample_frame([-10, 10]))
    assert curves.electricity_saving_fraction.isna().all()


def test_missing_hour_is_rejected():
    frame = sample_frame([10, 20, 30]).drop(sample_frame([10, 20, 30]).index[1])
    with pytest.raises(ValueError, match="complete"):
        make_curves(frame)
