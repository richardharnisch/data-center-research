"""Export figures, machine-readable curves and a concise research report."""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter

from .analysis import PERIODS, on_fractions, period_coverage
from .data import CARBON, PRICE

COLORS = {"daily": "#2673A6", "weekly": "#B66026", "monthly": "#21836C"}


def write_outputs(frame, curves, metadata: dict, output: Path,
                  power_mw: float, whole_hours: bool) -> None:
    output.mkdir(parents=True, exist_ok=True)
    curves.to_csv(output / "curves.csv", index=False)
    examples = curves[curves.requested_shutdown_fraction.round(2).isin([0.05, 0.10, 0.50])]
    examples.to_csv(output / "examples.csv", index=False)
    config = {"power_mw": power_mw, "whole_hours": whole_hours,
              "periods": list(PERIODS), "perfect_information": True,
              "boundary_periods": "Include first/last partial calendar week/month at their observed duration",
              "tie_break": "earlier UTC timestamp shut down first",
              "period_hours": period_coverage(frame), "data_provenance": metadata}
    (output / "run.json").write_text(json.dumps(config, indent=2) + "\n")
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "axes.titleweight": "bold", "savefig.facecolor": "white"})
    start = metadata["start_local_inclusive"][:10]
    end = metadata["end_local_exclusive"][:10]
    subtitle = f"Netherlands · {start} to {end} (end exclusive) · {power_mw:g} MW when on"
    footer = ("Sources: ENTSO-E day-ahead prices; NED Dutch production CO₂ intensity. "
              "Historical perfect information; zero power when off.\n"
              + ("Whole hours only; downtime rounded down in each period." if whole_hours else
                 "Exact durations; one hour per period may be partly on. Hourly signals held constant.")
              + " Boundary weeks/months use only their observed hours.")
    specs = [("absolute", "electricity_cost_eur", "attributed_co2_kg", "Electricity cost (€)", "Attributed CO₂ (kg)"),
             ("relative", "electricity_cost_fraction", "carbon_fraction", "Electricity cost remaining", "Attributed CO₂ remaining"),
             ("savings", "electricity_saving_fraction", "carbon_saving_fraction", "Electricity cost saved", "Attributed CO₂ saved")]
    for kind, price_column, carbon_column, price_label, carbon_label in specs:
        fig, axes = plt.subplots(1, 2, figsize=(12, 5.6))
        for ax, objective, column, label, title in [
            (axes[0], "price", price_column, price_label, "Shut down the most expensive hours"),
            (axes[1], "carbon", carbon_column, carbon_label, "Shut down the most carbon-intensive hours"),
        ]:
            for period in PERIODS:
                subset = curves[(curves.period == period) & (curves.objective == objective)].sort_values("uptime_fraction")
                ax.plot(subset.uptime_fraction, subset[column], color=COLORS[period],
                        label=period.capitalize(), lw=2.3)
            baseline = curves.iloc[0]["baseline_cost_eur" if objective == "price" else "baseline_attributed_co2_kg"]
            ax.plot([0, 1], [0, baseline] if kind == "absolute" else ([1, 0] if kind == "savings" else [0, 1]),
                    color="#929AA0", ls="--", lw=1.2, label="Uniform reduction")
            ax.set(xlabel="Proportion of time the data center is on", ylabel=label,
                   title=title, xlim=(0, 1))
            ax.xaxis.set_major_formatter(PercentFormatter(1))
            if kind != "absolute":
                ax.yaxis.set_major_formatter(PercentFormatter(1))
            else:
                ax.ticklabel_format(axis="y", style="plain", useOffset=False)
            ax.axhline(0, color="#B8BDC1", lw=0.7)
            ax.grid(alpha=0.15)
            ax.legend(frameon=False, fontsize=9, loc="best")
        fig.suptitle("Data-center shutdown: cost and carbon versus uptime", fontsize=16, fontweight="bold", y=0.98)
        fig.text(0.5, 0.92, subtitle, ha="center", fontsize=10, color="#505960")
        fig.text(0.055, 0.02, footer, fontsize=8, color="#505960", va="bottom")
        fig.tight_layout(rect=(0, 0.12, 1, 0.88))
        for extension in ("png", "svg"):
            fig.savefig(output / f"{kind}_curves.{extension}", dpi=180)
        plt.close(fig)
    # Auditable example schedules: evaluate both outcomes for the same schedule.
    schedules = frame.copy()
    for period in PERIODS:
        for objective in ("price", "carbon"):
            for fraction in (0.05, 0.10, 0.50):
                schedules[f"{period}_{objective}_off_{fraction:.0%}_on_fraction"] = on_fractions(
                    frame, period, objective, fraction, whole_hours)
    schedules.to_csv(output / "example_schedules.csv")
    baseline = curves.iloc[0]
    lines = ["# Dutch data-center shutdown experiment", "",
             f"Study window: **{start} to {end}, end exclusive**, Europe/Amsterdam; **{len(frame):,} complete hours**. Both signals cover every hour. Facility power: **{power_mw:g} MW when on**, zero when off.", "",
             f"Always-on baseline: **€{baseline.baseline_cost_eur:,.2f}** electricity cost and **{baseline.baseline_attributed_co2_kg / 1000:,.2f} tonnes attributed CO₂**. Mean price: €{frame[PRICE].mean():.2f}/MWh; mean production carbon intensity: {frame[CARBON].mean():.2f} gCO₂/kWh. Negative-price hours: {int((frame[PRICE] < 0).sum())}.", "",
             "![Remaining cost and carbon versus uptime](relative_curves.png)", "",
             "## Savings examples", "",
             "Each row below reports price savings from a price-ranked schedule and carbon savings from a separate carbon-ranked schedule. These two savings are not generally achieved by the same schedule. Both outcomes for each individual schedule are in `curves.csv`.", "",
             "| Selection period | Requested time off | Actual time off | Price saving (price-ranked) | CO₂ saving (carbon-ranked) |",
             "| --- | ---: | ---: | ---: | ---: |"]
    for period in PERIODS:
        for fraction in (0.05, 0.10, 0.50):
            subset = examples[(examples.period == period) & (examples.requested_shutdown_fraction == fraction)]
            price = subset[subset.objective == "price"].iloc[0]
            carbon = subset[subset.objective == "carbon"].iloc[0]
            lines.append(f"| {period.capitalize()} | {fraction:.0%} | {1-price.uptime_fraction:.2%} | {price.electricity_saving_fraction:.2%} | {carbon.carbon_saving_fraction:.2%} |")
    lines += ["", "## Interpretation", "",
              "These are retrospective, perfect-information curves for discarded work. They do not model completed jobs, workload recovery, service quality, startup energy, minimum on/off durations, cooling, standby power, or price changes caused by the data center. Uptime is a capacity-availability proxy; it is not measured application performance.", "",
              "Price is wholesale day-ahead energy cost only; taxes, network charges, contracts, and fixed charges are excluded. Negative prices are retained, so cost can be negative and savings can exceed 100% at low uptime. Fractional savings are undefined if the always-on baseline is nonpositive.", "",
              "Carbon is attributed using NED's average Dutch electricity-production CO₂ factor (type 27, Providing, Current). It is not a marginal avoided-emissions estimate, an import-adjusted consumption mix, a full lifecycle CO₂e assessment, or a carbon-price calculation.", "",
              "Periods are local calendar days, ISO Monday–Sunday weeks, and calendar months. The first and last weeks/months can be partial and use their observed hours. DST days contain 23 or 25 actual hours. Monthly and weekly partitions are not nested, so neither curve must dominate the other.", "",
              ("Whole-hour mode floors the requested shutdown hours within each period. The plot uses actual achieved uptime, and tables show both requested and actual time off." if whole_hours else
               "Exact-duration mode shuts down the highest-ranked hours and, when needed, part of one boundary hour per period. For example, 5% of 24 hours is 1 hour 12 minutes. This is binary on/off in time, with hourly price and carbon held constant; it does not model partial-power throttling."), "",
              "## Sources and reproducibility", "",
              "[ENTSO-E Transparency Platform](https://transparency.entsoe.eu/) supplies Dutch day-ahead prices. [NED definitions](https://ned.nl/nl/definities) identify the production-mix signal; [NED API documentation](https://ned.nl/nl/handleiding-api) documents its units and request parameters. Native responses are cached locally and checksummed. `run.json` records source requests, retrieval timestamps, hashes, study bounds, period lengths, and assumptions. `data/provenance.json` identifies the input CSV checksum.", "",
              "See `absolute_curves.png` for total euros and kilograms, `relative_curves.png` for the proportion remaining, and `savings_curves.png` for the proportion saved. SVG versions and the full numeric curves are included.", ""]
    (output / "report.md").write_text("\n".join(lines))
