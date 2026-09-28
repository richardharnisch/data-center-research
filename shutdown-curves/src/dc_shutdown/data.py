"""Source clients adapted from the earlier price-forecasting project's request settings.

Credentials come from the process environment. Raw responses and request
provenance are cached; tokens never appear in saved request metadata.
"""

import hashlib
import json
import os
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import numpy as np
import pandas as pd
import requests

TZ = "Europe/Amsterdam"
ENTSOE_URL = "https://web-api.tp.entsoe.eu/api"
NED_URL = "https://api.ned.nl/v1/utilizations"
ZONE = "10YNL----------L"
PRICE = "price_eur_per_mwh"
CARBON = "carbon_gco2_per_kwh"


def credentials() -> dict:
    """Return provider credentials loaded into the process environment."""
    return {key: os.environ.get(key, "") for key in ("ENTSOE_API_KEY", "NED_API_KEY")}


def date_bounds(start: str | None, end: str | None) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Local calendar dates, start inclusive and end exclusive."""
    end_ts = pd.Timestamp(end).tz_localize(TZ) if end else pd.Timestamp.now(TZ).normalize()
    start_ts = pd.Timestamp(start).tz_localize(TZ) if start else end_ts - pd.DateOffset(years=1)
    if start_ts != start_ts.normalize() or end_ts != end_ts.normalize() or start_ts >= end_ts:
        raise ValueError("Use midnight calendar dates with start earlier than end.")
    return start_ts, end_ts


class CachedClient:
    def __init__(self, raw_dir: Path, refresh: bool = False):
        self.raw_dir = raw_dir
        self.refresh = refresh
        self.session = requests.Session()
        self.last_ned_request = 0.0
        self.used_files: list[dict] = []

    def get(self, source: str, url: str, params: dict, key: str, extension: str) -> bytes:
        canonical = json.dumps(params, sort_keys=True)
        digest = hashlib.sha256(canonical.encode()).hexdigest()[:20]
        path = self.raw_dir / source / f"{digest}.{extension}"
        metadata_path = path.with_suffix(path.suffix + ".meta.json")
        if not path.exists() or not metadata_path.exists() or self.refresh:
            if not key:
                raise ValueError(
                    f"Missing {source.upper()}_API_KEY; set it in .env or the environment."
                )
            request_params = dict(params)
            headers = {}
            if source == "entsoe":
                request_params["securityToken"] = key
            else:
                headers = {"X-AUTH-TOKEN": key, "Accept": "application/ld+json"}
            for attempt in range(4):
                if source == "ned":
                    time.sleep(max(0, 1.6 - (time.monotonic() - self.last_ned_request)))
                    self.last_ned_request = time.monotonic()
                try:
                    response = self.session.get(
                        url,
                        params=request_params,
                        headers=headers,
                        timeout=(15, 90),
                        allow_redirects=False,
                    )
                except requests.RequestException:
                    # requests exceptions can include the token-bearing request URL.
                    if attempt == 3:
                        raise RuntimeError(
                            f"{source}: network request failed after 4 attempts"
                        ) from None
                    time.sleep(2**attempt)
                    continue
                if response.status_code == 200:
                    break
                if response.status_code not in (429, 500, 502, 503, 504) or attempt == 3:
                    raise RuntimeError(
                        f"{source}: HTTP {response.status_code}; check access and date range"
                    )
                time.sleep(min(45, max(2**attempt, float(response.headers.get("Retry-After", 5)))))
            body = response.content
            # Do not cache malformed error documents as successful datasets.
            if source == "ned":
                if "hydra:member" not in response.json():
                    raise ValueError("NED response does not contain hydra:member")
            elif "TimeSeries" not in response.text:
                raise ValueError("ENTSO-E returned no price TimeSeries for the requested range")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            metadata = {
                "source": source,
                "url": url,
                "parameters": params,
                "retrieved_at_utc": datetime.now(UTC).isoformat(),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
            metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
        metadata = json.loads(metadata_path.read_text())
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != metadata["sha256"]:
            raise ValueError(f"Raw cache checksum mismatch: {path}")
        self.used_files.append(
            {"file": path.relative_to(self.raw_dir.parent).as_posix(), **metadata}
        )
        return body


def parse_entsoe(body: bytes) -> pd.DataFrame:
    root = ET.fromstring(body)
    # Strip namespace only; dotted field names remain intact.
    for element in root.iter():
        element.tag = element.tag.split("}")[-1]
    rows = []
    for series in root.findall("TimeSeries"):
        if (
            series.findtext("currency_Unit.name") != "EUR"
            or series.findtext("price_Measure_Unit.name") != "MWH"
        ):
            raise ValueError("Expected ENTSO-E EUR/MWh prices")
        curve = series.findtext("curveType")
        if curve not in ("A01", "A03"):
            raise ValueError(f"Unsupported ENTSO-E curve type: {curve}")
        for period in series.findall("Period"):
            start = pd.Timestamp(period.findtext("timeInterval/start"))
            end = pd.Timestamp(period.findtext("timeInterval/end"))
            step = pd.Timedelta(period.findtext("resolution"))
            if step not in [pd.Timedelta(minutes=n) for n in (15, 30, 60)]:
                raise ValueError(f"Unsupported ENTSO-E resolution: {step}")
            count = int((end - start) / step)
            points = {}
            for point in period.findall("Point"):
                pos = int(point.findtext("position"))
                if pos in points or not 1 <= pos <= count:
                    raise ValueError("Invalid or duplicate ENTSO-E point position")
                points[pos] = float(point.findtext("price.amount"))
            previous = None
            for pos in range(1, count + 1):
                if pos in points:
                    previous = points[pos]
                elif curve != "A03":
                    previous = None
                if previous is not None:
                    t = start + (pos - 1) * step
                    rows.append((t, t + step, previous))
    if not rows:
        raise ValueError("ENTSO-E document contained no prices")
    return pd.DataFrame(rows, columns=["start", "end", PRICE])


def parse_ned(payload: dict) -> pd.DataFrame:
    rows = []
    expected = {
        "point": "/v1/points/0",
        "type": "/v1/types/27",
        "activity": "/v1/activities/1",
        "classification": "/v1/classifications/2",
        "granularity": "/v1/granularities/5",
    }
    for row in payload["hydra:member"]:
        if any(row.get(k) != v for k, v in expected.items()):
            raise ValueError("NED returned an unexpected signal or classification")
        factor = row.get("emissionfactor")
        if factor is None:
            raise ValueError("Missing NED electricity-mix emission factor; refusing to assume zero")
        factor = float(factor)
        if not np.isfinite(factor) or factor < 0:
            raise ValueError("Invalid NED carbon intensity")
        rows.append((row["validfrom"], row["validto"], factor * 1000))
    return pd.DataFrame(rows, columns=["start", "end", CARBON])


def hourly_intervals(frame: pd.DataFrame, column: str) -> pd.Series:
    """Expand only explicit source intervals; require four quarters in every hour.

    This also handles ENTSO-E's transition to quarter-hourly prices. Identical
    overlaps between adjacent requests are harmless; conflicting values fail.
    """
    values = {}
    for start, end, value in frame[["start", "end", column]].itertuples(index=False, name=None):
        start, end = pd.Timestamp(start), pd.Timestamp(end)
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("Source timestamps must have time zones")
        start, end = start.tz_convert("UTC"), end.tz_convert("UTC")
        duration = end - start
        if duration not in [pd.Timedelta(minutes=m) for m in (15, 30, 60)] or start != start.floor(
            "15min"
        ):
            raise ValueError("Invalid or unaligned source interval")
        if not np.isfinite(value):
            raise ValueError(f"Non-finite {column}")
        for timestamp in pd.date_range(start, end, freq="15min", inclusive="left"):
            if timestamp in values and not np.isclose(values[timestamp], value, rtol=0, atol=1e-9):
                raise ValueError(f"Conflicting {column} values at {timestamp}")
            values[timestamp] = value
    if not values:
        raise ValueError(f"No {column} observations")
    quarters = pd.Series(values, name=column).sort_index()
    hours = quarters.resample("h").mean()
    return hours.where(quarters.resample("h").count() == 4)


def fetch_dataset(
    start: pd.Timestamp, end: pd.Timestamp, data_dir: Path, keys: dict, refresh: bool = False
) -> tuple[pd.DataFrame, dict]:
    client = CachedClient(data_dir / "raw", refresh)
    price_frames, carbon_frames = [], []
    # Month-sized requests stay within both APIs' date-range limits.
    boundaries = [start, *pd.date_range(start, end, freq="MS", inclusive="neither"), end]
    for left, right in pairwise(boundaries):
        print(f"Fetching {left.date()} to {right.date()} (end exclusive)", flush=True)
        params = {
            "documentType": "A44",
            "in_Domain": ZONE,
            "out_Domain": ZONE,
            "periodStart": left.tz_convert("UTC").strftime("%Y%m%d%H%M"),
            "periodEnd": right.tz_convert("UTC").strftime("%Y%m%d%H%M"),
        }
        price_frames.append(
            parse_entsoe(client.get("entsoe", ENTSOE_URL, params, keys["ENTSOE_API_KEY"], "xml"))
        )
        page = 1
        while True:
            # Date-only filters are documented by NED. Request enclosing UTC days,
            # then trim precisely to the common Amsterdam calendar interval.
            params = {
                "point": 0,
                "type": 27,
                "activity": 1,
                "classification": 2,
                "granularity": 5,
                "granularitytimezone": 0,
                "page": page,
                "validfrom[after]": str(left.tz_convert("UTC").date()),
                "validfrom[strictly_before]": str(right.tz_convert("UTC").ceil("D").date()),
            }
            payload = json.loads(client.get("ned", NED_URL, params, keys["NED_API_KEY"], "json"))
            carbon_frames.append(parse_ned(payload))
            if "hydra:next" not in payload.get("hydra:view", {}):
                break
            page += 1
            if page > 100:
                raise ValueError("Unexpected NED pagination length")
    expected = pd.date_range(
        start.tz_convert("UTC"), end.tz_convert("UTC"), freq="h", inclusive="left"
    )
    frame = pd.DataFrame(index=expected)
    for column, frames in [(PRICE, price_frames), (CARBON, carbon_frames)]:
        frame[column] = hourly_intervals(pd.concat(frames, ignore_index=True), column).reindex(
            expected
        )
    frame.index.name = "timestamp_utc"
    missing = {c: [t.isoformat() for t in frame.index[frame[c].isna()]] for c in (PRICE, CARBON)}
    metadata = {
        "start_local_inclusive": start.isoformat(),
        "end_local_exclusive": end.isoformat(),
        "timezone": TZ,
        "expected_hours": len(expected),
        "observed_complete_hours": int(frame.notna().all(axis=1).sum()),
        "missing_hours": missing,
        "source_file_base": "data_directory",
        "sources": client.used_files,
        "price_basis": "ENTSO-E NL day-ahead EUR/MWh, duration-weighted hourly means",
        "carbon_basis": "NED ElectricityMix, Providing, Current; Dutch production CO2 intensity; gCO2/kWh",
        "carbon_scope": "Production-average proxy; no imported-electricity flow tracing or marginal-emissions model",
    }
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "coverage.json").write_text(json.dumps(metadata, indent=2) + "\n")
    if any(missing.values()):
        raise ValueError(
            "Incomplete data; see data/coverage.json. No interpolation or silent row dropping is performed."
        )
    frame.to_csv(data_dir / "hourly.csv")
    metadata["hourly_sha256"] = hashlib.sha256((data_dir / "hourly.csv").read_bytes()).hexdigest()
    (data_dir / "provenance.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return frame, metadata


def load_dataset(data_dir: Path) -> tuple[pd.DataFrame, dict]:
    metadata = json.loads((data_dir / "provenance.json").read_text())
    raw = (data_dir / "hourly.csv").read_bytes()
    if hashlib.sha256(raw).hexdigest() != metadata["hourly_sha256"]:
        raise ValueError("hourly.csv checksum differs from provenance; fetch again before analysis")
    frame = pd.read_csv(data_dir / "hourly.csv", index_col="timestamp_utc")
    frame.index = pd.to_datetime(frame.index, utc=True)
    expected = pd.date_range(
        pd.Timestamp(metadata["start_local_inclusive"]).tz_convert("UTC"),
        pd.Timestamp(metadata["end_local_exclusive"]).tz_convert("UTC"),
        freq="h",
        inclusive="left",
    )
    if not frame.index.equals(expected) or not np.isfinite(frame[[PRICE, CARBON]]).all().all():
        raise ValueError("Dataset must contain every requested hour exactly once and finite values")
    if (frame[CARBON] < 0).any():
        raise ValueError("Carbon intensity must be nonnegative")
    return frame, metadata
