# Dutch data-center shutdown curves

This experiment asks how much electricity cost or attributed CO₂ remains when a constant-power data center switches off during the highest-price or highest-carbon hours. It compares **daily, weekly, and monthly** selection over the **latest complete year**. Start with [the generated report](results/report.md), [total-cost curves](results/absolute_curves.png), or [normalized curves](results/relative_curves.png).

## Reproduce the delivered result

Run these commands from the repository root:

```bash
cd shutdown-curves
uv sync --locked
uv run shutdown-curves analyze
```

The included `data/hourly.csv` contains the actual collected data, so analysis requires no credentials or network access. The default facility consumes 1 MW when on and zero when off. Set `--power-mw 10` to scale absolute euros and kilograms to a 10 MW facility; percentage savings do not change.

For a fresh download, supply `ENTSOE_API_KEY` and `NED_API_KEY` as environment variables. You can also copy `.env.example` to `.env` and fill in your keys; `.env` is ignored by Git. From the experiment directory, download the same period with:

```bash
uv run --env-file .env shutdown-curves run \
  --start 2025-09-28 --end 2026-09-28
```

Omit `--env-file .env` if the keys are already exported in your shell. The optional `--legacy-keys` argument accepts a credential file you choose; no location is assumed. Environment variables take precedence over that file. Only literal string assignments are read; the file is never executed. Keys are not copied to generated files or logged.

Omitting dates selects the year ending at the start of today in Europe/Amsterdam. `--end` is exclusive. The delivered interval is 28 September 2025 through 27 September 2026. Raw responses are reused by default; add `--refresh` to retrieve revisions from the providers. If a provider has not yet published the last required day, fetching fails with a coverage report; choose an explicit earlier `--end` to obtain a full year of complete data. There is no silent shortening or filling of the study period.

Shutdowns use exact durations. For example, 5% of a 24-hour day means one hour and twelve minutes off, assuming both signals are constant within an hour. The facility is fully off during those twelve minutes; it is not partially throttled.

## Data and reuse

The source settings are adapted from the download module and API constants in the earlier **price-forecasting** project:

- **ENTSO-E:** document A44, Dutch bidding zone `10YNL----------L`, prices in EUR/MWh. The new parser preserves full UTC start times, reads every Period, expands A03 variable blocks only within their declared interval, and averages complete sets of quarter-hours to an hourly price. It retains negative prices.
- **NED:** point 0 (Netherlands), type 27 (ElectricityMix), activity 1 (Providing), classification 2 (Current), granularity 5 (hour), timezone 0 (UTC). The API's `emissionfactor` in kg CO₂/kWh is multiplied by 1,000 to obtain g CO₂/kWh. We use the provider's total-mix factor directly rather than summing potentially overlapping production categories.

The old project's timestamp conversion used only the date portion of the ENTSO-E Period start, discarding its hour. Its request settings are reused here, but its timestamp conversion and CSV appending are replaced. The old project is unchanged.

Official references: [ENTSO-E Transparency Platform](https://transparency.entsoe.eu/), [NED API documentation](https://ned.nl/nl/handleiding-api), [NED definitions](https://ned.nl/nl/definities), and [NED data catalogue](https://ned.nl/nl/datacatalogus). NED documents this as electricity **production** and CO₂, so the report does not relabel it as flow-traced consumption or lifecycle CO₂e.

Both source series must cover the same complete set of hours. Duplicate identical source intervals are deduplicated; conflicting values, missing intervals, missing carbon factors, and unexpected units are errors. Missing values are neither interpolated nor dropped. Local calendar grouping handles the 23-hour spring day and 25-hour autumn day. ISO weeks begin on Monday. The first and last calendar weeks/months may be partial: each receives the requested shutdown fraction of its observed hours, which keeps every curve on the same study interval. `run.json` enumerates every period's hour count. Weeks and months are not nested partitions, so neither is guaranteed to outperform the other.

## Calculation and interpretation

Within each selection period, sort hours from highest to lowest price (or carbon intensity), and shut down the requested fraction of the period. Ties shut down earlier timestamps first. This uses perfect historical information over the entire day/week/month. It measures the opportunity with hindsight; it is not a forecast-driven operational policy.

For each hourly observation, electricity cost equals `power_MW × on_fraction × price_EUR_per_MWh`. Attributed carbon equals `power_MW × on_fraction × intensity_gCO2_per_kWh` in kilograms (g/kWh and kg/MWh have the same numeric value). Sum over time. The saving fraction equals `1 - scheduled_total / always_on_total`. Reported uptime equals total on-hours divided by total hours. Daily savings are therefore aggregated by their actual cost/emissions, not averaged as daily percentages. Savings ratios are undefined for nonpositive always-on totals.

Price-ranked and carbon-ranked schedules are **separate experiments**. The two primary panels show each objective optimized independently. `curves.csv` also gives the price and carbon outcome of each individual schedule, so the trade-off can be inspected without assuming that independently optimal savings happen together. There is no combined optimization or monetary carbon price in this first experiment.

This is a fixed-load, zero-off-power baseline. Switching off discards available compute time; work is not deferred and recovered later. Uptime is a proxy for available capacity, not a measured throughput or latency result. Startup, cooling, standby load, minimum run durations, and service penalties are absent. Prices are wholesale energy prices, not a retail bill. Carbon uses average domestic production intensity as a proxy; it does not estimate the causal grid-emissions reduction from turning off a particular facility, account for imports through flow tracing, or include full lifecycle emissions.

## Files

Every generated output directory receives a `.gitignore` containing only `*`, so its contents are ignored by Git regardless of the directory name. The raw response cache is also ignored. Recreate the reports and figures with the analysis command above. Saved source-file references are relative to the configured data directory, so metadata remains portable even when `--data-dir` is an absolute path.

| File | Contents |
| --- | --- |
| `data/hourly.csv` | Validated UTC hourly price and carbon series |
| `data/provenance.json` | Date range, units, source requests, retrieval timestamps, SHA-256 hashes |
| `data/coverage.json` | Completeness audit, including exact missing timestamps if collection fails |
| `data/raw/` | Cached native XML/JSON and request metadata; ignored by Git |
| `results/report.md` | Findings and interpretation |
| `results/absolute_curves.*` | Total electricity cost and attributed carbon versus uptime, PNG/SVG |
| `results/relative_curves.*` | Fractions of always-on cost and carbon remaining, PNG/SVG |
| `results/savings_curves.*` | Fractions saved versus uptime, PNG/SVG |
| `results/curves.csv` | Both outcomes at each 1% shutdown increment for all six strategies |
| `results/examples.csv` | Requested shutdowns of 5%, 10%, and 50% |
| `results/example_schedules.csv` | Per-hour on-fractions for the example schedules |
| `results/run.json` | Analysis configuration, calendar period lengths, input provenance |

## Verification

```bash
uv sync --locked --group dev
uv run pytest
uv run ruff check src tests
```

Tests cover timezone preservation, variable-block and quarter-hour parsing, missing/duplicate intervals, carbon units, calendar grouping and DST, independently computed savings, negative-price behavior, exact shutdown durations, and scaling with facility power. Source data completeness is also checked on every fetch and offline analysis.
