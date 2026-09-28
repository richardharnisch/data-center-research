"""Export figures, machine-readable curves, and example schedules."""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter

from .analysis import PERIODS, on_fractions, period_coverage

COLORS = {"daily": "#2673A6", "weekly": "#B66026", "monthly": "#21836C"}


def write_outputs(frame, curves, metadata: dict, output: Path, power_mw: float) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / ".gitignore").write_text("*\n")
    curves.to_csv(output / "curves.csv", index=False)
    examples = curves[curves.requested_shutdown_fraction.round(2).isin([0.05, 0.10, 0.50])]
    examples.to_csv(output / "examples.csv", index=False)
    config = {
        "power_mw": power_mw,
        "periods": list(PERIODS),
        "perfect_information": True,
        "boundary_periods": "Include first/last partial calendar week/month at their observed duration",
        "tie_break": "earlier UTC timestamp shut down first",
        "period_hours": period_coverage(frame),
        "data_provenance": metadata,
    }
    (output / "run.json").write_text(json.dumps(config, indent=2) + "\n")
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 11,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.titleweight": "bold",
            "savefig.facecolor": "white",
        }
    )
    start = metadata["start_local_inclusive"][:10]
    end = metadata["end_local_exclusive"][:10]
    subtitle = f"Netherlands · {start} to {end} (end exclusive) · {power_mw:g} MW when on"
    footer = (
        "Sources: ENTSO-E day-ahead prices; NED Dutch production CO₂ intensity. "
        "Historical perfect information; zero power when off.\n"
        "Exact durations; one hour per period may be partly on. Hourly signals held constant."
        " Boundary weeks/months use only their observed hours."
    )
    specs = [
        (
            "absolute",
            "electricity_cost_eur",
            "attributed_co2_kg",
            "Electricity cost (€)",
            "Attributed CO₂ (kg)",
        ),
        (
            "relative",
            "electricity_cost_fraction",
            "carbon_fraction",
            "Electricity cost remaining",
            "Attributed CO₂ remaining",
        ),
        (
            "savings",
            "electricity_saving_fraction",
            "carbon_saving_fraction",
            "Electricity cost saved",
            "Attributed CO₂ saved",
        ),
    ]
    for kind, price_column, carbon_column, price_label, carbon_label in specs:
        fig, axes = plt.subplots(1, 2, figsize=(12, 5.6))
        for ax, objective, column, label, title in [
            (axes[0], "price", price_column, price_label, "Shut down the most expensive hours"),
            (
                axes[1],
                "carbon",
                carbon_column,
                carbon_label,
                "Shut down the most carbon-intensive hours",
            ),
        ]:
            for period in PERIODS:
                subset = curves[
                    (curves.period == period) & (curves.objective == objective)
                ].sort_values("uptime_fraction")
                ax.plot(
                    subset.uptime_fraction,
                    subset[column],
                    color=COLORS[period],
                    label=period.capitalize(),
                    lw=2.3,
                )
            baseline = curves.iloc[0][
                "baseline_cost_eur" if objective == "price" else "baseline_attributed_co2_kg"
            ]
            ax.plot(
                [0, 1],
                [0, baseline] if kind == "absolute" else ([1, 0] if kind == "savings" else [0, 1]),
                color="#929AA0",
                ls="--",
                lw=1.2,
                label="Uniform reduction",
            )
            ax.set(
                xlabel="Proportion of time the data center is on",
                ylabel=label,
                title=title,
                xlim=(0, 1),
            )
            ax.xaxis.set_major_formatter(PercentFormatter(1))
            if kind != "absolute":
                ax.yaxis.set_major_formatter(PercentFormatter(1))
            else:
                ax.ticklabel_format(axis="y", style="plain", useOffset=False)
            ax.axhline(0, color="#B8BDC1", lw=0.7)
            ax.grid(alpha=0.15)
            ax.legend(frameon=False, fontsize=9, loc="best")
        fig.suptitle(
            "Data-center shutdown: cost and carbon versus uptime",
            fontsize=16,
            fontweight="bold",
            y=0.98,
        )
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
                    frame, period, objective, fraction
                )
    schedules.to_csv(output / "example_schedules.csv")
