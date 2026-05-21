from dataclasses import dataclass
from xml.etree import ElementTree

import httpx


@dataclass
class CallbookResult:
    callsign: str
    name: str | None
    city: str | None
    county: str | None
    state: str | None
    country: str | None
    latitude: float | None
    longitude: float | None
    source: str


def _parse_float(text: str | None) -> float | None:
    if text is None:
        return None
    try:
        return float(text)
    except (ValueError, TypeError):
        return None


class HamQTHProvider:
    """HamQTH.com XML callbook provider."""

    BASE_URL = "https://www.hamqth.com/xml.php"

    def authenticate(self, username: str, password: str) -> str:
        resp = httpx.get(
            self.BASE_URL,
            params={"u": username, "p": password},
            timeout=15,
        )
        root = ElementTree.fromstring(resp.text)
        ns = {"h": "https://www.hamqth.com"}
        session_id = root.find(".//h:session_id", ns)
        if session_id is None:
            session_id = root.find(".//session_id")
        if session_id is None or not session_id.text:
            raise ValueError("HamQTH authentication failed")
        return session_id.text

    def lookup(self, callsign: str, session_token: str) -> CallbookResult | None:
        resp = httpx.get(
            self.BASE_URL,
            params={"id": session_token, "callsign": callsign, "prg": "SkyNetControl"},
            timeout=15,
        )
        root = ElementTree.fromstring(resp.text)
        ns = {"h": "https://www.hamqth.com"}

        error = root.find(".//h:error", ns)
        if error is None:
            error = root.find(".//error")
        if error is not None:
            return None

        search = root.find(".//h:search", ns)
        if search is None:
            search = root.find(".//search")
        if search is None:
            return None

        def _get(tag: str) -> str | None:
            el = search.find(f"h:{tag}", ns)
            if el is None:
                el = search.find(tag)
            return el.text if el is not None else None

        name = _get("nick") or _get("adr_name")

        return CallbookResult(
            callsign=callsign.upper(),
            name=name,
            city=_get("adr_city"),
            county=_get("us_county"),
            state=_get("us_state"),
            country=_get("adr_country"),
            latitude=_parse_float(_get("latitude")),
            longitude=_parse_float(_get("longitude")),
            source="hamqth",
        )


class QRZProvider:
    """QRZ.com XML callbook provider."""

    BASE_URL = "https://xmldata.qrz.com/xml/current/"

    def authenticate(self, username: str, password: str) -> str:
        resp = httpx.get(
            self.BASE_URL,
            params={"username": username, "password": password},
            timeout=15,
        )
        root = ElementTree.fromstring(resp.text)
        ns = {"q": "http://xmldata.qrz.com"}
        key = root.find(".//q:Key", ns)
        if key is None:
            key = root.find(".//Key")
        if key is None or not key.text:
            raise ValueError("QRZ authentication failed")
        return key.text

    def lookup(self, callsign: str, session_token: str) -> CallbookResult | None:
        resp = httpx.get(
            self.BASE_URL,
            params={"s": session_token, "callsign": callsign},
            timeout=15,
        )
        root = ElementTree.fromstring(resp.text)
        ns = {"q": "http://xmldata.qrz.com"}

        error = root.find(".//q:Error", ns)
        if error is None:
            error = root.find(".//Error")
        if error is not None:
            return None

        record = root.find(".//q:Callsign", ns)
        if record is None:
            record = root.find(".//Callsign")
        if record is None:
            return None

        def _get(tag: str) -> str | None:
            el = record.find(f"q:{tag}", ns)
            if el is None:
                el = record.find(tag)
            return el.text if el is not None else None

        fname = _get("fname") or ""
        lname = _get("name") or ""
        full_name = f"{fname} {lname}".strip() or None

        return CallbookResult(
            callsign=callsign.upper(),
            name=full_name,
            city=_get("addr2"),
            county=_get("county"),
            state=_get("state"),
            country=_get("country"),
            latitude=_parse_float(_get("lat")),
            longitude=_parse_float(_get("lon")),
            source="qrz",
        )
