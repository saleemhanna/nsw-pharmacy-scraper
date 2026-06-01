import httpx
import pytest
import respx

from src.api import NSWApiClient


@pytest.mark.asyncio
async def test_resolve_postcode_locationid_returns_id() -> None:
    async with respx.mock(base_url="https://verify.licence.nsw.gov.au") as mock:
        mock.post("/publicregisterapi/api/v1/licence/search/areasnoCount").mock(
            return_value=httpx.Response(200, json=[
                {"locationId": "76943", "name": "2000", "locationType": "postcode"},
                {"locationId": "61247", "name": "Sydney, NSW 2000", "locationType": "suburb"},
            ])
        )
        async with NSWApiClient() as c:
            loc_id = await c.resolve_postcode_locationid("2000")
        assert loc_id == "76943"


@pytest.mark.asyncio
async def test_resolve_postcode_returns_none_when_empty() -> None:
    async with respx.mock(base_url="https://verify.licence.nsw.gov.au") as mock:
        mock.post("/publicregisterapi/api/v1/licence/search/areasnoCount").mock(
            return_value=httpx.Response(200, json=[])
        )
        async with NSWApiClient() as c:
            assert await c.resolve_postcode_locationid("9999") is None


@pytest.mark.asyncio
async def test_query_pharmacies_returns_results() -> None:
    payload = {
        "pagingInfo": {"currentPage": 0, "totalPages": 1, "pageSize": 200, "totalRecords": 1},
        "results": [{
            "licenceNumber": "PC1", "businessNameList": ["X"], "status": "Current",
            "address": "", "suburb": "", "postcode": "2000", "state": "NSW",
            "licensee": "X", "licenseeType": "Organisation",
            "latitude": 0, "longitude": 0, "historicalLicenceNumbers": [],
            "granted": "2025-06-30", "expires": "2026-06-30",
            "licenceId": "1", "licenceType": "Pharmacy",
            "licenceTypeFriendly": "Pharmacy", "licenceGroup": "Pharmacies",
        }],
    }
    async with respx.mock(base_url="https://verify.licence.nsw.gov.au") as mock:
        mock.post("/publicregisterapi/api/v1/licence/search/advQuery").mock(
            return_value=httpx.Response(200, json=payload)
        )
        async with NSWApiClient() as c:
            results = await c.query_pharmacies_by_location_id("76943")
        assert len(results) == 1
        assert results[0]["licenceNumber"] == "PC1"
