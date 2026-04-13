#!/usr/bin/env python3
"""Fetch daily prices for fixed Nikkei 225 constituents.

Inputs:
- data/constituents/nikkei225/current.csv

Outputs:
- data/prices/stooq/jp/<code>.csv
- data/panels/nikkei225_current_constituents_latest.csv
- data/panels/nikkei225_current_constituents_close_wide_260d.csv
- runtime/update_nikkei225_prices_status.json
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from common_market_io import (
    csv_text,
    decode_bytes,
    ensure_dir,
    fetch_bytes,
    log,
    now_iso,
    write_status,
    write_text_if_changed,
)

STOOQ_CSV_URL = "https://stooq.com/q/d/l/?s={symbol}&i=d"
YAHOO_CHART_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    "?period1=0&period2={period2}&interval=1d&events=history&includeAdjustedClose=false"
)
STOOQ_DAILY_HITS_LIMIT_MESSAGE = "Exceeded the daily hits limit"

PRICE_FIELDNAMES = [
    "code",
    "ticker_tse",
    "symbol_stooq",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "source",
    "fetched_at",
]

LATEST_PANEL_FIELDNAMES = [
    "code",
    "ticker_tse",
    "company_name",
    "sector",
    "date",
    "close",
    "volume",
    "price_file",
]


class StooqRateLimitError(RuntimeError):
    """Raised when Stooq returns a daily hits limit response."""


def parse_unix_timestamp(value: int) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Nikkei 225 constituent prices")
    parser.add_argument("--repo-root", default=".", help="Repository root")
    parser.add_argument("--constituents-csv", default="data/constituents/nikkei225/current.csv")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--sleep-seconds", type=float, default=0.15)
    parser.add_argument("--max-symbols", type=int, default=0, help="For testing; 0 means all")
    parser.add_argument("--min-success-ratio", type=float, default=0.90)
    parser.add_argument(
        "--price-provider",
        choices=("yahoo", "stooq"),
        default="yahoo",
        help="Primary upstream provider. yahoo falls back to stooq per symbol on fetch/parse failure.",
    )
    parser.add_argument(
        "--rate-limit-cooldown-seconds",
        type=float,
        default=120.0,
        help="How long to wait before retrying after a Stooq daily hits limit response",
    )
    parser.add_argument(
        "--rate-limit-max-retries",
        type=int,
        default=1,
        help="How many times to retry a symbol after Stooq returns a daily hits limit response",
    )
    return parser.parse_args()


def read_constituents(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(
            f"Constituents file not found: {path}. "
            "Commit data/constituents/nikkei225/current.csv first."
        )
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    required = {"code", "ticker_tse", "symbol_stooq", "company_name", "sector"}
    if not rows:
        raise ValueError("Constituents CSV is empty")

    missing_cols = required - set(rows[0].keys())
    if missing_cols:
        raise ValueError(f"Constituents CSV missing columns: {sorted(missing_cols)}")

    rows = [r for r in rows if r.get("code") and r.get("symbol_stooq")]
    if not rows:
        raise ValueError("Constituents CSV has no usable rows")
    return rows


def parse_number(value: str) -> str:
    value = value.strip().replace(",", "")
    if not value:
        return ""
    return f"{float(value):.2f}"


def parse_volume(value: str) -> str:
    value = value.strip().replace(",", "")
    if not value:
        return ""
    return str(int(float(value)))


def normalize_price_csv(
    raw_text: str,
    *,
    code: str,
    ticker_tse: str,
    symbol_stooq: str,
    fetched_at: str,
) -> list[dict[str, str]]:
    if STOOQ_DAILY_HITS_LIMIT_MESSAGE in raw_text:
        raise StooqRateLimitError(f"Stooq rate limit hit for {symbol_stooq}: {STOOQ_DAILY_HITS_LIMIT_MESSAGE}")

    reader = csv.DictReader(raw_text.splitlines())
    required = {"Date", "Open", "High", "Low", "Close", "Volume"}
    if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
        raise ValueError(f"Unexpected Stooq CSV header for {symbol_stooq}: {reader.fieldnames}")

    rows: list[dict[str, str]] = []
    for rec in reader:
        date = rec.get("Date", "").strip()
        if not date:
            continue
        try:
            date = datetime.strptime(date, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            continue

        close_val = rec.get("Close", "").strip()
        if close_val in {"", "-"}:
            continue

        rows.append(
            {
                "code": code,
                "ticker_tse": ticker_tse,
                "symbol_stooq": symbol_stooq,
                "date": date,
                "open": parse_number(rec.get("Open", "")),
                "high": parse_number(rec.get("High", "")),
                "low": parse_number(rec.get("Low", "")),
                "close": parse_number(close_val),
                "volume": parse_volume(rec.get("Volume", "")),
                "source": STOOQ_CSV_URL.format(symbol=symbol_stooq),
                "fetched_at": fetched_at,
            }
        )

    if not rows:
        raise ValueError(f"No price rows parsed for {symbol_stooq}")

    rows.sort(key=lambda r: r["date"])
    return rows


def normalize_yahoo_chart_json(
    raw_text: str,
    *,
    code: str,
    ticker_tse: str,
    symbol_stooq: str,
    fetched_at: str,
) -> list[dict[str, str]]:
    payload = json.loads(raw_text)
    result = payload.get("chart", {}).get("result")
    if not result:
        raise ValueError(f"Yahoo chart payload is empty for {ticker_tse}")
    chart = result[0]
    timestamps = chart.get("timestamp") or []
    quote_rows = chart.get("indicators", {}).get("quote") or []
    if not timestamps or not quote_rows:
        raise ValueError(f"Yahoo chart payload missing timestamps/quote for {ticker_tse}")

    quote = quote_rows[0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []
    period2 = int(datetime.now(tz=timezone.utc).timestamp())

    rows: list[dict[str, str]] = []
    for idx, ts in enumerate(timestamps):
        close_val = closes[idx] if idx < len(closes) else None
        if close_val is None:
            continue
        rows.append(
            {
                "code": code,
                "ticker_tse": ticker_tse,
                "symbol_stooq": symbol_stooq,
                "date": parse_unix_timestamp(int(ts)),
                "open": parse_number(str(opens[idx])) if idx < len(opens) and opens[idx] is not None else "",
                "high": parse_number(str(highs[idx])) if idx < len(highs) and highs[idx] is not None else "",
                "low": parse_number(str(lows[idx])) if idx < len(lows) and lows[idx] is not None else "",
                "close": parse_number(str(close_val)),
                "volume": parse_volume(str(volumes[idx])) if idx < len(volumes) and volumes[idx] is not None else "",
                "source": YAHOO_CHART_URL.format(symbol=ticker_tse, period2=period2),
                "fetched_at": fetched_at,
            }
        )

    if not rows:
        raise ValueError(f"No price rows parsed from Yahoo payload for {ticker_tse}")

    rows.sort(key=lambda r: r["date"])
    return rows


def fetch_symbol_price_rows(
    *,
    code: str,
    ticker_tse: str,
    symbol_stooq: str,
    fetched_at: str,
    timeout: int,
    retries: int,
    sleep_seconds: float,
    rate_limit_cooldown_seconds: float,
    rate_limit_max_retries: int,
    price_provider: str,
) -> list[dict[str, str]]:
    if price_provider == "yahoo":
        try:
            period2 = int(datetime.now(tz=timezone.utc).timestamp())
            url = YAHOO_CHART_URL.format(symbol=ticker_tse, period2=period2)
            raw_text = decode_bytes(
                fetch_bytes(
                    url,
                    timeout=timeout,
                    retries=retries,
                    sleep_seconds=sleep_seconds,
                )
            )
            return normalize_yahoo_chart_json(
                raw_text,
                code=code,
                ticker_tse=ticker_tse,
                symbol_stooq=symbol_stooq,
                fetched_at=fetched_at,
            )
        except Exception as yahoo_exc:  # noqa: BLE001
            log(
                f"Yahoo fetch failed for {ticker_tse} ({yahoo_exc}); "
                f"falling back to Stooq symbol {symbol_stooq}"
            )

    url = STOOQ_CSV_URL.format(symbol=symbol_stooq)
    attempts = rate_limit_max_retries + 1

    for attempt in range(1, attempts + 1):
        raw_text = decode_bytes(
            fetch_bytes(
                url,
                timeout=timeout,
                retries=retries,
                sleep_seconds=sleep_seconds,
            )
        )
        try:
            return normalize_price_csv(
                raw_text,
                code=code,
                ticker_tse=ticker_tse,
                symbol_stooq=symbol_stooq,
                fetched_at=fetched_at,
            )
        except StooqRateLimitError:
            if attempt >= attempts:
                raise RuntimeError(
                    f"Stooq rate limit persisted for {symbol_stooq}. "
                    "Wait and rerun the workflow later, or increase --sleep-seconds."
                )
            log(
                f"Stooq rate limit hit for {symbol_stooq}; "
                f"waiting {rate_limit_cooldown_seconds:.0f}s before retry {attempt + 1}/{attempts}"
            )
            time.sleep(rate_limit_cooldown_seconds)

    raise RuntimeError(f"Unreachable rate limit handling state for {symbol_stooq}")


def build_latest_panel(
    constituents: list[dict[str, str]],
    file_map: dict[str, Path],
    repo_root: Path,
) -> list[dict[str, str]]:
    constituent_map = {row["code"]: row for row in constituents}
    latest_rows: list[dict[str, str]] = []

    for code, path in sorted(file_map.items()):
        with path.open("r", newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            continue
        last = rows[-1]
        meta = constituent_map[code]
        latest_rows.append(
            {
                "code": code,
                "ticker_tse": meta["ticker_tse"],
                "company_name": meta["company_name"],
                "sector": meta["sector"],
                "date": last["date"],
                "close": last["close"],
                "volume": last["volume"],
                "price_file": str(path.relative_to(repo_root)),
            }
        )

    return latest_rows


def build_close_wide_recent(
    file_map: dict[str, Path],
    lookback_days: int = 260,
) -> tuple[list[str], list[dict[str, str]]]:
    closes_by_date: dict[str, dict[str, str]] = defaultdict(dict)
    all_codes = sorted(file_map)
    all_dates: set[str] = set()

    for code, path in file_map.items():
        with path.open("r", newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        for rec in rows[-lookback_days:]:
            date = rec["date"]
            closes_by_date[date][code] = rec["close"]
            all_dates.add(date)

    fieldnames = ["date"] + all_codes
    wide_rows: list[dict[str, str]] = []

    for date in sorted(all_dates):
        row = {"date": date}
        for code in all_codes:
            row[code] = closes_by_date[date].get(code, "")
        wide_rows.append(row)

    return fieldnames, wide_rows


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    constituents_path = repo_root / args.constituents_csv
    status_path = repo_root / "runtime/update_nikkei225_prices_status.json"
    prices_root = repo_root / "data/prices/stooq/jp"
    latest_panel_path = repo_root / "data/panels/nikkei225_current_constituents_latest.csv"
    close_wide_path = repo_root / "data/panels/nikkei225_current_constituents_close_wide_260d.csv"

    ensure_dir(prices_root)
    ensure_dir(latest_panel_path.parent)
    fetched_at = now_iso()

    try:
        constituents = read_constituents(constituents_path)
        if args.max_symbols > 0:
            constituents = constituents[: args.max_symbols]

        changed_files = 0
        reused_existing_files = 0
        failures: list[dict[str, str]] = []
        file_map: dict[str, Path] = {}
        existing_file_map = {
            path.stem: path
            for path in prices_root.glob("*.csv")
            if path.is_file()
        }

        for idx, row in enumerate(constituents, start=1):
            code = row["code"]
            symbol_stooq = row["symbol_stooq"]
            ticker_tse = row["ticker_tse"]

            log(f"[{idx}/{len(constituents)}] fetching {symbol_stooq}")
            try:
                price_rows = fetch_symbol_price_rows(
                    code=code,
                    ticker_tse=ticker_tse,
                    symbol_stooq=symbol_stooq,
                    fetched_at=fetched_at,
                    timeout=args.timeout,
                    retries=args.retries,
                    sleep_seconds=args.sleep_seconds,
                    rate_limit_cooldown_seconds=args.rate_limit_cooldown_seconds,
                    rate_limit_max_retries=args.rate_limit_max_retries,
                    price_provider=args.price_provider,
                )
                price_file = prices_root / f"{code}.csv"
                changed = write_text_if_changed(price_file, csv_text(price_rows, PRICE_FIELDNAMES))
                if changed:
                    changed_files += 1
                file_map[code] = price_file
            except Exception as exc:  # noqa: BLE001
                failures.append({"code": code, "symbol_stooq": symbol_stooq, "error": str(exc)})
                fallback_file = existing_file_map.get(code)
                if fallback_file and fallback_file.exists():
                    file_map[code] = fallback_file
                    reused_existing_files += 1
                    log(
                        f"WARN {symbol_stooq}: fetch failed ({exc}); "
                        f"fallback to existing file {fallback_file.name}"
                    )
                else:
                    log(f"ERROR {symbol_stooq}: {exc}")

        if not file_map:
            raise RuntimeError("No price files were written")
        if len(failures) == len(constituents):
            raise RuntimeError(
                "All symbol fetches failed; upstream source is likely blocked or now requires authentication. "
                "No fresh price rows were downloaded."
            )

        success_ratio = len(file_map) / len(constituents)
        if success_ratio < args.min_success_ratio:
            raise RuntimeError(
                f"Too many price fetch failures: success_ratio={success_ratio:.3f}, "
                f"succeeded={len(file_map)}, requested={len(constituents)}"
            )

        latest_panel_rows = build_latest_panel(constituents, file_map, repo_root)
        latest_panel_changed = write_text_if_changed(
            latest_panel_path,
            csv_text(latest_panel_rows, LATEST_PANEL_FIELDNAMES),
        )

        close_wide_fields, close_wide_rows = build_close_wide_recent(file_map)
        close_wide_changed = write_text_if_changed(
            close_wide_path,
            csv_text(close_wide_rows, close_wide_fields),
        )

        status = {
            "ok": True,
            "fetched_at": fetched_at,
            "symbols_requested": len(constituents),
            "symbols_succeeded": len(file_map),
            "symbols_failed": len(failures),
            "success_ratio": round(success_ratio, 6),
            "price_files_changed": changed_files,
            "price_files_reused": reused_existing_files,
            "latest_panel_changed": latest_panel_changed,
            "close_wide_changed": close_wide_changed,
            "latest_panel_output": str(latest_panel_path.relative_to(repo_root)),
            "close_wide_output": str(close_wide_path.relative_to(repo_root)),
            "failures": failures[:20],
            "source_template": YAHOO_CHART_URL if args.price_provider == "yahoo" else STOOQ_CSV_URL,
            "price_provider": args.price_provider,
        }
        write_status(status_path, status)
        log(
            f"prices success={len(file_map)} failures={len(failures)} "
            f"success_ratio={success_ratio:.3f} changed_files={changed_files}"
        )
        return 0

    except Exception as exc:  # noqa: BLE001
        write_status(
            status_path,
            {
                "ok": False,
                "fetched_at": fetched_at,
                "error": str(exc),
                "source_template": YAHOO_CHART_URL if args.price_provider == "yahoo" else STOOQ_CSV_URL,
                "price_provider": args.price_provider,
            },
        )
        log(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
