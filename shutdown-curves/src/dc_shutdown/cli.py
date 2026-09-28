import argparse
from pathlib import Path

from .analysis import make_curves
from .data import credentials, date_bounds, fetch_dataset, load_dataset
from .report import write_outputs


def main():
    parser = argparse.ArgumentParser(
        description="Dutch electricity cost and carbon versus data-center uptime"
    )
    parser.add_argument("command", choices=["fetch", "analyze", "run"], nargs="?", default="run")
    parser.add_argument(
        "--start", help="Inclusive date in Europe/Amsterdam; defaults to one year before end"
    )
    parser.add_argument("--end", help="Exclusive date in Europe/Amsterdam; defaults to today")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output-dir", type=Path, default=Path("results"))
    parser.add_argument(
        "--legacy-keys",
        type=Path,
        help="Read API-key string assignments from the earlier project's api_key.py",
    )
    parser.add_argument(
        "--refresh", action="store_true", help="Refetch raw responses; default reuses the cache"
    )
    parser.add_argument(
        "--power-mw", type=float, default=1.0, help="Total facility power when on (default 1 MW)"
    )
    args = parser.parse_args()
    try:
        if args.command in ("fetch", "run"):
            start, end = date_bounds(args.start, args.end)
            frame, metadata = fetch_dataset(
                start, end, args.data_dir, credentials(args.legacy_keys), args.refresh
            )
            print(f"Validated {len(frame):,} complete hours; saved {args.data_dir / 'hourly.csv'}")
        else:
            if args.start or args.end or args.refresh or args.legacy_keys:
                parser.error(
                    "analyze reads the saved dataset; date, refresh, and credential options apply to fetch/run"
                )
            frame, metadata = load_dataset(args.data_dir)
        if args.command in ("analyze", "run"):
            curves = make_curves(frame, args.power_mw)
            write_outputs(frame, curves, metadata, args.output_dir, args.power_mw)
            print(f"Saved curves and report: {args.output_dir / 'report.md'}")
    except (ValueError, RuntimeError, OSError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
