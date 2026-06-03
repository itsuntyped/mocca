"""Network tool: identify a shipment's carrier and return its official tracking URL.

Mocca stays local-first and deliberately uses **no third-party tracking service
or API key** here. Instead this tool leans on two no-key sources:

  * the pure-Python ``tracking-numbers`` package, which validates a tracking
    number against published carrier formats (checksums) and yields the carrier
    plus that carrier's own official tracking URL - covering USPS, UPS, FedEx,
    DHL, Canada Post and Amazon entirely offline; and
  * a small in-repo ``_CUSTOM_CARRIERS`` table for carriers the package does not
    recognise (Intelcom, AliExpress/Cainiao, Royal Mail, ...), matched by number
    prefix or an explicit ``carrier`` hint and turned into the carrier's official
    tracking URL via a template.

It then makes a *best-effort* fetch of the resolved URL to surface any readable
status text. Most major carriers render tracking in JavaScript, so a plain GET
often returns only a page shell with no live status - the official URL is the
guaranteed result; live status is a bonus when the page is server-rendered.

The fetch reaches the internet, so the tool is ``is_local=False`` and gated
behind the web-search toggle like the other network tools. The ``tracking-numbers``
package is imported lazily inside the resolver, so a missing install never breaks
tool discovery or app startup; the tool just reports it is unavailable. Parsing
is synchronous, so we run it off the event loop with ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx

from .base import Tool, ToolError

log = logging.getLogger("mocca.tools")

# Keep the fetch short - a tool call should not hang a chat turn for long.
_TIMEOUT = 15.0

# The international S10 tracking-number shape (two letters, nine digits, a country
# code) ends in the origin country, so it auto-detects China Post (CN), Royal Mail
# (GB) and Deutsche Post (DE) without the user naming the carrier. The "{cc}" is
# substituted per carrier when building each entry's regex.
_S10 = r"^[A-Z]{2}\d{9}%s$"


def _en(label: Any) -> str:
    """Pull the English string from a {en, fr, nl} label dict (Intelcom uses these)."""
    return str(label.get("en") or "").strip() if isinstance(label, dict) else ""


def _intelcom_event_time(event: dict[str, Any]) -> str:
    """A readable local time for an Intelcom event, best-effort ("" if unknown).

    Prefers the address' ISO ``event_local_time`` (already in the package's local
    zone); falls back to the epoch-ms ``timestamp`` rendered in UTC so we never
    pull in timezone data just for a history line.
    """
    addr = (event.get("package_location") or {}).get("address") or {}
    iso = addr.get("event_local_time")
    if iso:  # e.g. "2026-05-21T10:19:33.558000-04:00" -> "2026-05-21 10:19"
        return str(iso)[:16].replace("T", " ")
    ms = event.get("timestamp")
    if isinstance(ms, (int, float)):
        from datetime import datetime, timezone
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return ""


def _format_eta(eta: Any) -> str:
    """Render Intelcom's ETA (a {from, to, ...} ISO-date dict) as a date range."""
    if isinstance(eta, dict):
        start = str(eta.get("from") or "")[:10]
        end = str(eta.get("to") or "")[:10]
        if start and end:
            return start if start == end else f"{start} to {end}"
        return start or end
    return str(eta).strip() if eta else ""


def _intelcom_event_place(event: dict[str, Any]) -> str:
    """City/province/country for an Intelcom event, best-effort ("" if unknown)."""
    addr = (event.get("package_location") or {}).get("address") or {}
    parts = [addr.get("city"), addr.get("state_province"), addr.get("country_code")]
    return ", ".join(p for p in parts if p)


async def _fetch_intelcom(number: str) -> str:
    """Read Intelcom's real tracking status from its JSON endpoint.

    Intelcom's tracking page is a JavaScript shell that renders from a JSON API,
    so scraping the page only yields boilerplate help text (which a model then
    mistakes for a status). We call that JSON endpoint directly instead for
    accurate current status, location, ETA and event history. Best-effort:
    returns "" on any failure so the caller still shows the official URL.
    """
    api = f"https://intelcom.ca/cfworker/v3/tracking/{number}/"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(api, headers={"User-Agent": "Mocca/0.1 (local AI)"})
            resp.raise_for_status()
            payload = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.debug("Intelcom API fetch failed for %s: %s", number, exc)
        return ""

    result = (payload.get("data") or {}).get("result") or {}
    last = result.get("last_status") or {}
    if not last:
        return ""

    lines: list[str] = []
    current = " - ".join(p for p in (last.get("label") or "",
                                      _en((last.get("labels") or {}).get("shortLabel"))) if p)
    if current:
        lines.append(f"Current status: {current}")
    where_when = ", ".join(p for p in (_intelcom_event_time(last),
                                       _intelcom_event_place(last)) if p)
    if where_when:
        lines.append(f"As of: {where_when}")
    # client.label reads "Package from {client}"; fill in the merchant code.
    merchant = _en((last.get("client") or {}).get("label"))
    code = result.get("client_code")
    if merchant and code:
        lines.append(merchant.replace("{client}", str(code)))
    if not last.get("isDelivered"):
        eta = _format_eta(result.get("public_eta") or result.get("eta"))
        if eta:
            lines.append(f"Estimated delivery: {eta}")
    history = result.get("status_list") or []
    if history:
        lines.append("\nHistory:")
        for ev in history:
            when = _intelcom_event_time(ev)
            label = ev.get("label") or _en((ev.get("labels") or {}).get("shortLabel"))
            lines.append(f"  {when + ' - ' if when else ''}{label}".rstrip())
    return "\n".join(lines).strip()


# Carriers the tracking-numbers package does not recognise. Each entry says how
# we detect the carrier and the official tracking URL we build for it. Adding a
# carrier is just one more entry here:
#   name   - display name shown to the user
#   prefix - upper-case number prefix that auto-detects this carrier, or None
#            when the format isn't distinctive enough to match safely (such
#            carriers are reached only via an explicit ``carrier`` hint)
#   regex  - optional compiled pattern that also auto-detects this carrier's
#            numbers (e.g. the S10 country suffix), for when there's no prefix
#   hints  - lower-case carrier names that match a user/model-supplied ``carrier``
#   url    - official tracking URL template; "{n}" is replaced with the number.
#            A template *without* "{n}" is a landing page (no per-number deep
#            link), so we tell the user to open it and enter the number. None
#            means the carrier has no public tracking page at all.
#   api    - optional async fn(number) -> status text from the carrier's own JSON
#            API (real status, not a scraped HTML shell); preferred when present
_CUSTOM_CARRIERS: list[dict[str, Any]] = [
    {
        # Intelcom (Canadian last-mile). Their ids always start with INTL, so we
        # can auto-detect them without a hint. Status comes from their JSON API,
        # since the tracking page itself is a JavaScript shell.
        "name": "Intelcom",
        "prefix": "INTL",
        "hints": ["intelcom"],
        "url": "https://intelcom.ca/en/track-your-package/?tracking-id={n}",
        "api": _fetch_intelcom,
    },
    {
        # AliExpress parcels are carried by Cainiao; their global tracking page
        # takes the number as mailNoList.
        "name": "AliExpress (Cainiao)",
        "prefix": None,
        "hints": ["aliexpress", "ali express", "cainiao"],
        "url": "https://global.cainiao.com/detail.htm?mailNoList={n}",
    },
    {
        # S10 numbers ending in GB are Royal Mail, so we auto-detect them.
        "name": "Royal Mail",
        "prefix": None,
        "regex": re.compile(_S10 % "GB", re.IGNORECASE),
        "hints": ["royal mail", "royalmail"],
        "url": "https://www.royalmail.com/track-your-item#/tracking-results/{n}",
    },
    {
        # China Post / EMS has no clean public per-number URL in English, and the
        # official site is bot-protected (so we can't read status). We give the
        # official tracking page and tell the user to enter the number there.
        # S10 numbers ending in CN auto-detect it without a hint.
        "name": "China Post / EMS",
        "prefix": None,
        "regex": re.compile(_S10 % "CN", re.IGNORECASE),
        "hints": ["china post", "china ems", "chinapost", "epacket"],
        "url": "https://www.ems.com.cn/english/",
    },
    {
        # S10 numbers ending in DE are Deutsche Post, so we auto-detect them.
        "name": "Deutsche Post / DHL Germany",
        "prefix": None,
        "regex": re.compile(_S10 % "DE", re.IGNORECASE),
        "hints": ["deutsche post", "deutschepost", "dhl germany", "dhl de"],
        "url": "https://www.dhl.de/de/privatkunden/dhl-sendungsverfolgung.html?piececode={n}",
    },
    {
        # Shopee Express (SPX) tracking pages are country-specific, so there is no
        # single public URL to build; we recognise the carrier and tell the user.
        "name": "Shopee (SPX)",
        "prefix": None,
        "hints": ["shopee", "spx", "shopee express"],
        "url": None,
    },
    {
        # Wish has no public per-number tracking page; recognise it and say so.
        "name": "Wish",
        "prefix": None,
        "hints": ["wish"],
        "url": None,
    },
]


def _match_custom(number: str, carrier_hint: str) -> dict[str, Any] | None:
    """Find a custom-carrier entry for this number or hint, or None.

    A ``carrier`` hint wins when given (so the user can name a marketplace carrier
    the number format can't reveal); otherwise we auto-detect by number prefix.
    We test ``h in hint`` (not the reverse) so a broad hint like "dhl" does not
    accidentally match the more specific "dhl germany" entry - plain DHL numbers
    should fall through to the tracking-numbers package.
    """
    hint = carrier_hint.strip().lower()
    if hint:
        for entry in _CUSTOM_CARRIERS:
            if any(h in hint for h in entry["hints"]):
                return entry
    upper = number.upper()
    for entry in _CUSTOM_CARRIERS:
        if entry["prefix"] and upper.startswith(entry["prefix"]):
            return entry
        regex = entry.get("regex")
        if regex and regex.match(number):
            return entry
    return None


def _resolve(number: str, carrier_hint: str) -> tuple[str, str | None, dict[str, Any] | None]:
    """Resolve (carrier_name, tracking_url, custom_entry) for a number.

    Tries our custom table first (so explicitly-supported carriers and their
    exact URLs always win), then falls back to the tracking-numbers package for
    the carriers it validates (USPS, UPS, FedEx, DHL, Canada Post, Amazon). The
    third element is the matched ``_CUSTOM_CARRIERS`` entry (or None) so the
    caller can use its structured ``api`` fetcher when present. Returns
    ("", None, None) when nothing recognises the number. Runs off-thread, so it
    may raise ImportError when the package is missing - the caller turns that into
    a friendly ToolError.
    """
    custom = _match_custom(number, carrier_hint)
    if custom is not None:
        url = custom["url"].format(n=number) if custom["url"] else None
        return custom["name"], url, custom

    # Lazy import: a missing install must not break discovery or startup, and the
    # custom path above already works without the package installed.
    from tracking_numbers import get_tracking_number

    parsed = get_tracking_number(number)
    if parsed is not None and getattr(parsed, "valid", False):
        name = getattr(getattr(parsed, "courier", None), "name", "") or "Unknown carrier"
        return name, getattr(parsed, "tracking_url", None), None
    return "", None, None


async def _run(args: dict[str, Any]) -> str:
    number = str(args.get("tracking_number", "")).strip()
    if not number:
        raise ToolError("Provide the 'tracking_number' to look up.")
    carrier_hint = str(args.get("carrier", "")).strip()

    try:
        name, url, entry = await asyncio.to_thread(_resolve, number, carrier_hint)
    except ImportError as exc:
        raise ToolError("The tracking-numbers package is not installed.") from exc

    if not name:
        raise ToolError(
            f"Could not identify the carrier for '{number}'. If you know the "
            "carrier, pass it as 'carrier' (e.g. 'royal mail', 'aliexpress')."
        )

    api = entry.get("api") if entry else None
    header = f"Carrier: {name}\nTracking number: {number}"

    # When a carrier exposes a structured status API (real JSON, not a scraped
    # HTML shell), read live status from it. We deliberately do NOT scrape carrier
    # pages otherwise: they are JavaScript shells whose boilerplate a small model
    # mistakes for a real status. For everyone else we return the official link
    # and let the user (or the page they open) see the live status.
    if api:
        status = await api(number)
        if url:
            header += f"\nOfficial tracking page: {url}"
        if status:
            return f"{header}\n\nStatus (from the carrier):\n{status}"
        tail = "open the tracking page above to view it." if url else "check the carrier's page."
        return f"{header}\n\nLive status could not be retrieved right now; {tail}"

    # A custom URL template without "{n}" is a landing page, not a per-number link.
    is_landing = bool(entry and entry["url"] and "{n}" not in entry["url"])
    if url and is_landing:
        # Landing page only (e.g. China Post / EMS): be explicit that the user has
        # to enter the number there themselves - don't imply a direct link.
        return (f"{header}\nThis carrier has no direct tracking link; open "
                f"{url} and enter the tracking number {number} to see its status.")
    if url:
        # A per-number tracking page exists - hand over the deep link. We don't
        # claim a status we can't read; the user opens the link to see it.
        return (f"{header}\nOfficial tracking page: {url}\n"
                "Open it to see the live status.")

    # Known carrier, but no public tracking page at all (e.g. Shopee, Wish).
    return (f"{header}\nNo public tracking URL is available for this carrier; "
            "check the carrier's app or order page for live status.")


TOOL = Tool(
    name="track_shipment",
    description=(
        "Identify a package's carrier from its tracking number and return the "
        "carrier's official tracking page (plus any status readable from it). Use "
        "whenever the user wants to track a package, parcel, or shipment. Pass the "
        "'tracking_number'; if you know the carrier (e.g. Intelcom, AliExpress, "
        "Royal Mail, Shopee), pass it as 'carrier' to help identify marketplace "
        "shipments the number alone can't reveal."
    ),
    category="shipping",
    parameters={
        "type": "object",
        "properties": {
            "tracking_number": {
                "type": "string",
                "description": "The shipment's tracking number.",
            },
            "carrier": {
                "type": "string",
                "description": (
                    "Optional carrier name to help identification (e.g. "
                    "'intelcom', 'aliexpress', 'royal mail', 'shopee')."
                ),
            },
        },
        "required": ["tracking_number"],
    },
    is_local=False,
    run=_run,
)
