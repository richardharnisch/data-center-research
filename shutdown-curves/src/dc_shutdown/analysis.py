"""Historical perfect-information shutdown schedules at fixed facility power."""

import numpy as np
import pandas as pd

from .data import CARBON, PRICE, TZ

PERIODS = ("daily", "weekly", "monthly")
OBJECTIVES = {"price": PRICE, "carbon": CARBON}


def period_labels(index: pd.DatetimeIndex, period: str) -> np.ndarray:
    local = index.tz_convert(TZ)
    if period == "daily":
        return np.asarray(local.strftime("%Y-%m-%d"))
    if period == "weekly":
        iso = local.isocalendar()
        return np.asarray(iso.year.astype(str) + "-W" + iso.week.astype(str).str.zfill(2))
    if period == "monthly":
        return np.asarray(local.strftime("%Y-%m"))
    raise ValueError(f"Unknown period: {period}")


def on_fractions(
    frame: pd.DataFrame,
    period: str,
    objective: str,
    shutdown_fraction: float,
) -> np.ndarray:
    """Remove highest-value hours per calendar period, with stable chronological ties.

    A fractional boundary hour denotes full shutdown for part of that hour, not
    reduced facility power.
    """
    if not 0 <= shutdown_fraction <= 1:
        raise ValueError("Shutdown fraction must lie between zero and one")
    labels = period_labels(frame.index, period)
    signal = frame[OBJECTIVES[objective]].to_numpy()
    on = np.ones(len(frame))
    for label in pd.unique(labels):
        positions = np.flatnonzero(labels == label)
        ranked = positions[np.argsort(-signal[positions], kind="stable")]
        off_hours = shutdown_fraction * len(positions)
        full = int(np.floor(off_hours + 1e-10))
        on[ranked[:full]] = 0
        if full < len(ranked):
            on[ranked[full]] = 1 - max(0, off_hours - full)
    return on


def make_curves(frame: pd.DataFrame, power_mw: float = 1.0) -> pd.DataFrame:
    if not np.isfinite(power_mw) or power_mw <= 0:
        raise ValueError("Facility power must be finite and positive")
    if (
        frame.empty
        or frame.index.tz is None
        or not frame.index.is_monotonic_increasing
        or frame.index.has_duplicates
    ):
        raise ValueError(
            "Need a nonempty, ordered, timezone-aware hourly dataset without duplicates"
        )
    if len(frame) > 1 and not ((frame.index[1:] - frame.index[:-1]) == pd.Timedelta(hours=1)).all():
        raise ValueError("Dataset must be hourly and complete")
    if not np.isfinite(frame[[PRICE, CARBON]].to_numpy()).all() or (frame[CARBON] < 0).any():
        raise ValueError("Invalid price or carbon values")
    prices = frame[PRICE].to_numpy() * power_mw  # EUR/MWh * MW * 1 h
    carbon = frame[CARBON].to_numpy() * power_mw  # g/kWh numerically equals kg/MWh
    baseline_cost, baseline_carbon = prices.sum(), carbon.sum()
    rows = []
    for period in PERIODS:
        for objective in OBJECTIVES:
            for percentage in range(101):
                shutdown = percentage / 100
                on = on_fractions(frame, period, objective, shutdown)
                cost, emissions = float(on @ prices), float(on @ carbon)
                cost_fraction = cost / baseline_cost if baseline_cost > 0 else np.nan
                carbon_fraction = emissions / baseline_carbon if baseline_carbon > 0 else np.nan
                rows.append(
                    {
                        "period": period,
                        "objective": objective,
                        "requested_shutdown_fraction": shutdown,
                        "uptime_fraction": float(on.mean()),
                        "on_hours": float(on.sum()),
                        "shutdown_hours": float(len(frame) - on.sum()),
                        "electricity_cost_eur": cost,
                        "attributed_co2_kg": emissions,
                        "electricity_cost_fraction": cost_fraction,
                        "carbon_fraction": carbon_fraction,
                        "electricity_saving_fraction": 1 - cost_fraction,
                        "carbon_saving_fraction": 1 - carbon_fraction,
                        "baseline_cost_eur": float(baseline_cost),
                        "baseline_attributed_co2_kg": float(baseline_carbon),
                    }
                )
    return pd.DataFrame(rows)


def period_coverage(frame: pd.DataFrame) -> dict:
    result = {}
    for period in PERIODS:
        counts = pd.Series(period_labels(frame.index, period)).value_counts(sort=False)
        result[period] = {str(k): int(v) for k, v in counts.items()}
    return result
