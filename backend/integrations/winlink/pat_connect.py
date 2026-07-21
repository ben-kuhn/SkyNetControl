"""Pure resolution of an operator's connect choice into a PAT connect URL."""
from __future__ import annotations

from backend.integrations.winlink.pat_client import PatClient

VALID_MODES = ("telnet", "ardop", "vara", "varafm", "packet", "pactor")


def resolve_connect_url(request: dict, aliases: dict[str, str]) -> tuple[str, str]:
    """Return (connect_url, method_label). `request` is either {"alias": name}
    or {"mode","gateway","freq"?}."""
    alias = request.get("alias")
    if alias:
        url = aliases.get(alias)
        if not url:
            raise ValueError(f"Unknown connect alias: {alias}")
        return url, f"alias: {alias}"

    mode = (request.get("mode") or "").strip().lower()
    gateway = (request.get("gateway") or "").strip()
    freq = (request.get("freq") or "").strip()
    if mode not in VALID_MODES:
        raise ValueError(f"Unsupported mode: {mode or '(none)'}")
    if not gateway:
        raise ValueError("Gateway is required")
    url = f"{mode}:///{gateway}"
    label = f"{mode} {gateway}"
    if freq:
        url += f"?freq={freq}"
        label += f" @ {freq}"
    return url, label


def build_connect_options(client: PatClient) -> dict:
    aliases = client.connect_aliases()
    gateways = []
    for r in client.rmslist():
        dial = r.get("dial") or r.get("Dial") or 0
        gateways.append({
            "callsign": r.get("callsign") or r.get("Callsign") or "",
            "modes": r.get("modes") or r.get("Modes") or "",
            "freq": str(dial),
        })
    return {
        "aliases": [{"name": k, "url": v} for k, v in aliases.items()],
        "gateways": gateways,
    }
