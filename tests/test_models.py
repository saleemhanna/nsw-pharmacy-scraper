import json

from src.models import Pharmacy

SAMPLE_RAW = {
    "licenceId": "0cEW20000000OEcMAM",
    "licenceNumber": "PC0024595",
    "licenceType": "Pharmacy",
    "licenceTypeFriendly": "Pharmacy",
    "licenceGroup": "Pharmacies",
    "businessNameList": ["3 Beaches Chemist"],
    "status": "Current",
    "granted": "2025-06-30",
    "expires": "2026-06-30",
    "licensee": "3 Beaches Chemist",
    "licenseeType": "Organisation",
    "suburb": "HALLIDAYS POINT",
    "state": "NSW",
    "postcode": "2430",
    "address": "Shop 2 Hallidays Point Village Square 85 High Street HALLIDAYS POINT NSW 2430",
    "latitude": 0,
    "longitude": 0,
    "historicalLicenceNumbers": [],
    "headerLayoutNew": {"fields": []},
}


def test_pharmacy_parses_api_response() -> None:
    p = Pharmacy.from_api(SAMPLE_RAW)
    assert p.registration_number == "PC0024595"
    assert p.licence_id == "0cEW20000000OEcMAM"
    assert p.licence_type == "Pharmacy"
    assert p.trading_name == "3 Beaches Chemist"
    assert p.status == "Current"
    assert p.postcode == "2430"
    assert p.licensee_type == "Organisation"
    assert p.owners == ""
    assert p.financial_interests == ""
    assert p.premises_details == ""


def test_enrich_from_detail() -> None:
    p = Pharmacy.from_api(SAMPLE_RAW)
    detail = {
        "associatedRoles": [
            {
                "name": "Owner",
                "parties": [
                    {"name": "Test Pty Ltd", "start": "2022-11-04T00:00:00", "partyType": "Organisation", "licences": []},
                ],
            },
            {
                "name": "Financial interest",
                "parties": [
                    {
                        "name": "John Smith",
                        "start": "2022-11-04T00:00:00",
                        "partyType": "Organisation",
                        "licences": [
                            {"licenceNumber": "PC0018566", "licensee": "Other Pharmacy", "status": "Current", "licenceType": "Pharmacy", "licenceID": "abc"},
                        ],
                    },
                ],
            },
        ],
        "locations": [
            {"premises": [{"address": "1 Foo St SYDNEY NSW 2000", "type": "Business"}]},
        ],
    }
    p.enrich_from_detail(detail)
    assert p.owners == "Test Pty Ltd (start: 2022-11-04)"
    assert "John Smith" in p.financial_interests
    assert "Other Pharmacy - PC0018566" in p.financial_interests
    assert p.premises_details == "1 Foo St SYDNEY NSW 2000 (Business)"


def test_pharmacy_raw_json_strips_layout() -> None:
    p = Pharmacy.from_api(SAMPLE_RAW)
    parsed = json.loads(p.raw_json)
    assert "headerLayoutNew" not in parsed
    assert parsed["licenceNumber"] == "PC0024595"


def test_pharmacy_multiple_trading_names() -> None:
    raw = {**SAMPLE_RAW, "businessNameList": ["Foo Chemist", "Foo Pharmacy"]}
    p = Pharmacy.from_api(raw)
    assert p.trading_name == "Foo Chemist"
    assert p.all_trading_names == "Foo Chemist;Foo Pharmacy"
