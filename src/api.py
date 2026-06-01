from __future__ import annotations

from typing import Any, Self

import httpx
import structlog
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

log = structlog.get_logger()

BASE_URL = "https://verify.licence.nsw.gov.au"
USER_AGENT = (
    "Mozilla/5.0 (compatible; nsw-pharmacy-scraper/0.1; "
    "+https://github.com/<owner>/<repo>)"
)


class NSWApiClient:
    def __init__(self, timeout: float = 30.0) -> None:
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def _request_with_retry(
        self, method: str, path: str, json_body: dict[str, Any] | None = None
    ) -> Any:  # noqa: ANN401
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(7),
            wait=wait_exponential(multiplier=5, min=10, max=300),
            retry=retry_if_exception_type((httpx.HTTPError,)),
            reraise=True,
        ):
            with attempt:
                resp = await self._client.request(method, path, json=json_body)
                if resp.status_code == 429 or resp.status_code >= 500:
                    log.warning("retryable_status", status=resp.status_code, path=path)
                    raise httpx.HTTPStatusError(
                        f"{resp.status_code}", request=resp.request, response=resp
                    )
                resp.raise_for_status()
                return resp.json()
        return None

    async def _post_with_retry(self, path: str, json_body: dict[str, Any]) -> Any:  # noqa: ANN401
        return await self._request_with_retry("POST", path, json_body)

    async def resolve_postcode_locationid(self, postcode: str) -> str | None:
        data = await self._post_with_retry(
            "/publicregisterapi/api/v1/licence/search/areasnoCount",
            {"licenceGroup": "Pharmacies", "SuburbLGAText": postcode},
        )
        for entry in data or []:
            if entry.get("locationType") == "postcode":
                return str(entry["locationId"])
        return None

    async def query_pharmacies_by_location_id(self, location_id: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        page = 0
        while True:
            payload = {
                "licenceGroup": "Pharmacies",
                "location": [location_id],
                "pageNumber": page,
                "pageSize": 200,
            }
            data = await self._post_with_retry(
                "/publicregisterapi/api/v1/licence/search/advQuery", payload
            )
            batch = data.get("results", [])
            results.extend(batch)
            paging = data.get("pagingInfo", {})
            total_pages = paging.get("totalPages", 1)
            if page + 1 >= total_pages or not batch:
                if paging.get("totalRecordsLimitExceeded"):
                    log.warning("location_cap_exceeded", location_id=location_id, page=page)
                break
            page += 1
        return results

    async def search_by_name(self, term: str) -> tuple[list[dict[str, Any]], bool]:
        data = await self._post_with_retry(
            "/publicregisterapi/api/v1/licence/search/advQuery",
            {"licenceGroup": "Pharmacies", "search": term, "pageNumber": 0, "pageSize": 200},
        )
        results = data.get("results", []) if data else []
        exceeded = data.get("pagingInfo", {}).get("totalRecordsLimitExceeded", False) if data else False
        return results, exceeded

    async def fetch_detail(self, licence_type: str, licence_id: str) -> dict[str, Any]:
        data = await self._request_with_retry(
            "GET",
            f"/publicregisterapi/api/v1/licence/search/details/{licence_type}/{licence_id}",
        )
        return data.get("componentData", {}) if data else {}
