from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from src.api import NSWApiClient
from src.models import Pharmacy
from src.postcodes import nsw_postcodes

log = structlog.get_logger()

POLITE_DELAY = 5.0
JITTER = 1.0
MAX_CONCURRENCY = 1


@dataclass
class ScrapeResult:
    pharmacies: list[Pharmacy] = field(default_factory=list)
    postcodes_queried: int = 0
    postcodes_with_results: int = 0
    records_failed: int = 0
    duplicates_dropped: int = 0


async def _sleep_polite() -> None:
    await asyncio.sleep(POLITE_DELAY + random.uniform(-JITTER, JITTER))


async def scrape(
    snapshot_path: Path,
    postcodes_override: Iterable[str] | None = None,
) -> ScrapeResult:
    result = ScrapeResult()
    seen: dict[str, Pharmacy] = {}
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    postcodes = list(postcodes_override) if postcodes_override else nsw_postcodes()

    async with NSWApiClient() as client:
        async def handle_postcode(pc: str) -> list[dict[str, Any]]:
            async with sem:
                try:
                    loc = await client.resolve_postcode_locationid(pc)
                    if not loc:
                        return []
                    rows = await client.query_pharmacies_by_location_id(loc)
                    await _sleep_polite()
                    log.info("postcode_done", postcode=pc, count=len(rows))
                    return rows
                except Exception as e:
                    log.error("postcode_failed", postcode=pc, error=str(e))
                    result.records_failed += 1
                    return []

        for pc in postcodes:
            rows = await handle_postcode(pc)
            result.postcodes_queried += 1
            if rows:
                result.postcodes_with_results += 1
            for raw in rows:
                try:
                    pharm = Pharmacy.from_api(raw)
                except Exception as e:
                    log.error("parse_failed", error=str(e), raw=raw)
                    result.records_failed += 1
                    continue
                key = pharm.registration_number
                if key in seen:
                    result.duplicates_dropped += 1
                    continue
                seen[key] = pharm
            snapshot_path.write_text(
                json.dumps(
                    [p.model_dump() for p in seen.values()], indent=2, sort_keys=True
                ),
                encoding="utf-8",
            )

    log.info("detail_fetch_start", total=len(seen))
    async with NSWApiClient() as detail_client:
        for i, pharm in enumerate(seen.values(), 1):
            if not pharm.licence_id:
                continue
            try:
                detail = await detail_client.fetch_detail(
                    pharm.licence_type, pharm.licence_id
                )
                pharm.enrich_from_detail(detail)
            except Exception as e:
                log.error(
                    "detail_fetch_failed",
                    reg=pharm.registration_number,
                    error=str(e),
                )
            await _sleep_polite()
            if i % 50 == 0:
                log.info("detail_fetch_progress", done=i, total=len(seen))
                snapshot_path.write_text(
                    json.dumps(
                        [p.model_dump() for p in seen.values()],
                        indent=2,
                        sort_keys=True,
                    ),
                    encoding="utf-8",
                )

    result.pharmacies = list(seen.values())
    snapshot_path.write_text(
        json.dumps(
            [p.model_dump() for p in result.pharmacies], indent=2, sort_keys=True
        ),
        encoding="utf-8",
    )
    return result
