#!/usr/bin/env python3
"""Build Tableau-friendly market feed CSVs and optionally publish them to Google Drive."""
from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import sys
from pathlib import Path
from typing import Iterable

from common_market_io import ensure_dir, log, write_text_if_changed

MARKET_CODE = "JPX"
UNIVERSE_CODE = "NIKKEI225"
INDEX_NAME = "Nikkei 225"
PRICE_OUTPUT = "exports/tableau/fact_market_prices_daily.csv"
INDEX_OUTPUT = "exports/tableau/fact_market_indexes_daily.csv"
PRICE_FIELDNAMES = [
    "trade_date",
    "market_code",
    "universe_code",
    "symbol",
    "ticker_local",
    "company_name",
    "sector",
    "currency",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "source",
    "source_file",
    "fetched_at",
]
INDEX_FIELDNAMES = [
    "trade_date",
    "market_code",
    "universe_code",
    "index_name",
    "open",
    "high",
    "low",
    "close",
    "source",
    "source_file",
    "fetched_at",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Tableau export files and optionally publish them")
    parser.add_argument("--repo-root", default=".", help="Repository root")
    parser.add_argument(
        "--publish-destination",
        choices=("local", "google-drive"),
        default="local",
        help="Where to publish generated files",
    )
    return parser.parse_args()


def iter_csv_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return ()
    return sorted(path for path in root.rglob("*.csv") if path.is_file())


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def load_constituent_metadata(repo_root: Path) -> dict[str, dict[str, str]]:
    metadata: dict[str, dict[str, str]] = {}
    for path in iter_csv_files(repo_root / "data/constituents"):
        for row in read_csv_rows(path):
            symbol = (row.get("code") or "").strip()
            if not symbol:
                continue
            current = metadata.setdefault(symbol, {})
            for key in ("ticker_tse", "company_name", "sector", "as_of_date"):
                value = (row.get(key) or "").strip()
                if value:
                    current[key] = value

    latest_panel = repo_root / "data/panels/nikkei225_current_constituents_latest.csv"
    if latest_panel.exists():
        for row in read_csv_rows(latest_panel):
            symbol = (row.get("code") or "").strip()
            if not symbol:
                continue
            current = metadata.setdefault(symbol, {})
            for key in ("ticker_tse", "company_name", "sector"):
                value = (row.get(key) or "").strip()
                if value and not current.get(key):
                    current[key] = value
    return metadata


def build_price_row(row: dict[str, str], rel_path: Path, metadata: dict[str, dict[str, str]]) -> dict[str, str]:
    symbol = (row.get("code") or rel_path.stem).strip()
    meta = metadata.get(symbol, {})
    ticker_local = (row.get("ticker_tse") or meta.get("ticker_tse") or symbol).strip() or symbol
    return {
        "trade_date": (row.get("date") or "").strip(),
        "market_code": MARKET_CODE,
        "universe_code": UNIVERSE_CODE,
        "symbol": symbol,
        "ticker_local": ticker_local,
        "company_name": (meta.get("company_name") or "").strip(),
        "sector": (meta.get("sector") or "").strip(),
        "currency": "JPY",
        "open": (row.get("open") or "").strip(),
        "high": (row.get("high") or "").strip(),
        "low": (row.get("low") or "").strip(),
        "close": (row.get("close") or "").strip(),
        "volume": (row.get("volume") or "").strip(),
        "source": (row.get("source") or "").strip(),
        "source_file": str(rel_path),
        "fetched_at": (row.get("fetched_at") or "").strip(),
    }


def build_index_row(row: dict[str, str], rel_path: Path) -> dict[str, str]:
    return {
        "trade_date": (row.get("date") or "").strip(),
        "market_code": MARKET_CODE,
        "universe_code": UNIVERSE_CODE,
        "index_name": INDEX_NAME,
        "open": (row.get("open") or "").strip(),
        "high": (row.get("high") or "").strip(),
        "low": (row.get("low") or "").strip(),
        "close": (row.get("close") or "").strip(),
        "source": (row.get("source") or "").strip(),
        "source_file": str(rel_path),
        "fetched_at": (row.get("fetched_at") or "").strip(),
    }


def write_csv_export(
    repo_root: Path,
    output_path: Path,
    fieldnames: list[str],
    row_iterable: Iterable[dict[str, str]],
    label: str,
) -> int:
    ensure_dir(output_path.parent)
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    count = 0
    with temp_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in row_iterable:
            if not row.get("trade_date"):
                raise ValueError(f"Found row without trade_date while building {label}")
            writer.writerow(row)
            count += 1
    changed = write_text_if_changed(output_path, temp_path.read_text(encoding="utf-8"), encoding="utf-8")
    temp_path.unlink(missing_ok=True)
    log(f"wrote {output_path.relative_to(repo_root)} rows={count} changed={changed}")
    if count == 0:
        raise ValueError(f"No rows found for {label}")
    return count


def generate_price_rows(repo_root: Path, metadata: dict[str, dict[str, str]]) -> Iterable[dict[str, str]]:
    for path in iter_csv_files(repo_root / "data/prices"):
        rel_path = path.relative_to(repo_root)
        with path.open("r", newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                yield build_price_row(row, rel_path, metadata)


def generate_index_rows(repo_root: Path) -> Iterable[dict[str, str]]:
    for path in iter_csv_files(repo_root / "data/indexes"):
        rel_path = path.relative_to(repo_root)
        with path.open("r", newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                yield build_index_row(row, rel_path)


def build_drive_service_from_env():
    folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip()
    encoded_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_BASE64", "").strip()
    missing = [
        name
        for name, value in (
            ("GOOGLE_DRIVE_FOLDER_ID", folder_id),
            ("GOOGLE_SERVICE_ACCOUNT_JSON_BASE64", encoded_json),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Google Drive publishing requires these environment variables: " + ", ".join(missing)
        )

    try:
        service_account_info = json.loads(base64.b64decode(encoded_json).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON_BASE64 could not be decoded into valid JSON") from exc

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError as exc:
        raise RuntimeError(
            "Google Drive publishing dependencies are missing. Install google-api-python-client and google-auth."
        ) from exc

    credentials = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=["https://www.googleapis.com/auth/drive.file"],
    )
    return build("drive", "v3", credentials=credentials, cache_discovery=False), folder_id, HttpError


def upload_files_to_google_drive(paths: list[Path]) -> None:
    service, folder_id, http_error_cls = build_drive_service_from_env()
    try:
        from googleapiclient.http import MediaFileUpload
    except ImportError as exc:
        raise RuntimeError(
            "Google Drive publishing dependencies are missing. Install google-api-python-client and google-auth."
        ) from exc

    try:
        for path in paths:
            filename = path.name
            escaped_name = filename.replace("'", "\\'")
            query = f"name = '{escaped_name}' and '{folder_id}' in parents and trashed = false"
            response = (
                service.files()
                .list(
                    q=query,
                    spaces="drive",
                    fields="files(id, name)",
                    pageSize=10,
                    includeItemsFromAllDrives=True,
                    supportsAllDrives=True,
                )
                .execute()
            )
            existing_files = response.get("files", [])
            media = MediaFileUpload(str(path), mimetype="text/csv", resumable=False)

            if existing_files:
                file_id = existing_files[0]["id"]
                service.files().update(
                    fileId=file_id,
                    media_body=media,
                    supportsAllDrives=True,
                ).execute()
                log(f"updated Google Drive file {filename} ({file_id})")
            else:
                metadata = {"name": filename, "parents": [folder_id]}
                created = service.files().create(
                    body=metadata,
                    media_body=media,
                    fields="id",
                    supportsAllDrives=True,
                ).execute()
                log(f"created Google Drive file {filename} ({created.get('id', 'unknown')})")
    except http_error_cls as exc:
        message = str(exc)
        if "Service Accounts do not have storage quota" in message or "storageQuotaExceeded" in message:
            raise RuntimeError(
                "Google Drive upload failed because service accounts do not have personal Drive storage quota. "
                "Use a folder inside a shared drive and share that shared drive with the service account, "
                "or switch to user OAuth / domain-wide delegation."
            ) from exc
        if "File not found:" in message or "notFound" in message:
            raise RuntimeError(
                "Google Drive upload failed because GOOGLE_DRIVE_FOLDER_ID was not found for this service account. "
                "Check that the secret contains only the folder ID (not the full URL), that the folder still exists, "
                "and that the shared drive or folder is shared with the service account email."
            ) from exc
        raise


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    try:
        outputs = [repo_root / PRICE_OUTPUT, repo_root / INDEX_OUTPUT]
        metadata = load_constituent_metadata(repo_root)
        price_count = write_csv_export(repo_root, outputs[0], PRICE_FIELDNAMES, generate_price_rows(repo_root, metadata), "price export")
        index_count = write_csv_export(repo_root, outputs[1], INDEX_FIELDNAMES, generate_index_rows(repo_root), "index export")

        if args.publish_destination == "google-drive":
            upload_files_to_google_drive(outputs)
        else:
            log("publish destination is local; skipping remote publish")

        log(f"tableau feed build complete prices={price_count} indexes={index_count}")
        return 0
    except Exception as exc:  # noqa: BLE001
        log(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
