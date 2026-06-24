from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field


class Pharmacy(BaseModel):
    registration_number: str
    licence_id: str
    licence_type: str
    status: str
    granted_date: str | None
    expiry_date: str | None
    trading_name: str
    all_trading_names: str
    address: str
    suburb: str
    postcode: str
    state: str
    licensee: str
    licensee_type: str
    start_date: str
    owners: str
    financial_interests: str
    premises_details: str
    latitude: float
    longitude: float
    historical_registration_numbers: str
    raw_json: str = Field(repr=False)

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Pharmacy:
        stripped = {k: v for k, v in raw.items() if k != "headerLayoutNew"}
        names = raw.get("businessNameList") or [""]
        historical = raw.get("historicalLicenceNumbers") or []
        return cls(
            registration_number=raw["licenceNumber"],
            licence_id=raw.get("licenceId", ""),
            licence_type=raw.get("licenceType", "Pharmacy"),
            status=raw.get("status", ""),
            granted_date=raw.get("granted"),
            expiry_date=raw.get("expires"),
            trading_name=names[0],
            all_trading_names=";".join(names),
            address=raw.get("address", ""),
            suburb=raw.get("suburb", ""),
            postcode=raw.get("postcode", ""),
            state=raw.get("state", ""),
            licensee=raw.get("licensee", ""),
            licensee_type=raw.get("licenseeType", ""),
            start_date="",
            owners="",
            financial_interests="",
            premises_details="",
            latitude=float(raw.get("latitude") or 0),
            longitude=float(raw.get("longitude") or 0),
            historical_registration_numbers=";".join(historical),
            raw_json=json.dumps(stripped, sort_keys=True),
        )

    def enrich_from_detail(self, detail: dict[str, Any]) -> None:
        roles = detail.get("associatedRoles", [])
        owner_parts: list[str] = []
        fi_parts: list[str] = []
        all_dates: list[str] = []
        for role_group in roles:
            role_name = role_group.get("name", "")
            for party in role_group.get("parties", []):
                name = party.get("name", "")
                start = (party.get("start") or "")[:10]
                if start:
                    all_dates.append(start)
                if role_name.lower().startswith("owner"):
                    owner_parts.append(name)
                elif role_name.lower().startswith("financial interest"):
                    # store the individual's name only; the "[related: ...]" list of
                    # their other pharmacies was noise and is intentionally dropped
                    fi_parts.append(name)
        self.start_date = min(all_dates) if all_dates else ""
        self.owners = " | ".join(owner_parts)
        self.financial_interests = " | ".join(fi_parts)

        locations = detail.get("locations", [])
        premises_parts: list[str] = []
        for loc in locations:
            for p in loc.get("premises", []):
                addr = p.get("address", "")
                ptype = p.get("type", "")
                premises_parts.append(f"{addr} ({ptype})" if ptype else addr)
        self.premises_details = " | ".join(premises_parts)
