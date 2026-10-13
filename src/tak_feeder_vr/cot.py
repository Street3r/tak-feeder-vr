# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Street3r
#
# The CoT structure follows sgofferj's tak-feeder-hsl / trakbridge-plugin-hsl
# and tak-feeder-traffic-fin-ais, Copyright Stefan Gofferje, GPL-3.0-or-later.
"""CoT event and remarks for one train."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from .model import Composition, Position, TimetableRow, TrainInfo
from . import naming


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _station(code: str, stations: dict[str, str]) -> str:
    return stations.get(code, code)


def next_stop(info: TrainInfo) -> Optional[TimetableRow]:
    """First stopping row the train has not reached yet."""
    for row in info.rows:
        if row.actual is None and row.stopping and not row.cancelled:
            return row
    return None


def _delay_text(row: TimetableRow) -> str:
    if row.unknown_delay:
        return " (myöhässä, ei arviota)"
    diff = row.difference_min
    if diff is None or diff == 0:
        return ""
    return f" ({diff:+d} min)"


def remarks(
    pos: Position,
    info: Optional[TrainInfo],
    comp: Optional[Composition],
    stations: dict[str, str],
    operators: dict[str, str],
    settings_callsign: str,
    tags: str,
    source_line: str,
    tz: ZoneInfo,
    now: datetime,
) -> str:
    """Human-readable infobox text."""
    lines = [f"{settings_callsign} · {naming.category_label(info)}"]
    if info:
        if info.cancelled:
            lines.append("PERUTTU")
        if info.rows:
            first = _station(info.rows[0].station, stations)
            last = _station(info.rows[-1].station, stations)
            lines.append(f"{first} → {last}")
        nxt = next_stop(info)
        if nxt:
            when = nxt.estimate or nxt.scheduled
            at = f" {when.astimezone(tz):%H:%M}" if when else ""
            lines.append(f"Seuraava: {_station(nxt.station, stations)}{at}{_delay_text(nxt)}")
    lines.append(f"Nopeus: {pos.speed_kmh:.0f} km/h")
    if comp:
        sec = comp.section_at(now)
        if sec:
            parts = []
            if sec.locomotives:
                parts.append(", ".join(sec.locomotives))
            if sec.wagons:
                parts.append(f"{sec.wagons} vaunua")
            if sec.length_m:
                parts.append(f"{sec.length_m} m")
            if sec.max_speed:
                parts.append(f"max {sec.max_speed} km/h")
            if parts:
                lines.append("Kalusto: " + ", ".join(parts))
    if info and info.operator:
        lines.append(f"Operaattori: {operators.get(info.operator, info.operator)}")
    acc = f" (±{pos.accuracy_m:.0f} m)" if pos.accuracy_m is not None else ""
    lines.append(f"GPS: {pos.timestamp.astimezone(tz):%H:%M:%S}{acc}")
    lines.append(f"Juna {pos.key[1]}, lähtöpäivä {pos.key[0] or '-'}")
    if source_line:
        lines.append(source_line)
    lines.append(tags)
    return "\n".join(lines)


def build_event(
    uid: str,
    cot_type: str,
    callsign: str,
    pos: Position,
    course: float,
    remarks_text: str,
    now: datetime,
    stale_seconds: int,
) -> bytes:
    """CoT XML. Speed is in m/s as the CoT schema requires; ATAK converts it."""
    root = ET.Element("event")
    root.set("version", "2.0")
    root.set("uid", uid)
    root.set("type", cot_type)
    root.set("how", "m-g")
    root.set("time", _iso(now))
    root.set("start", _iso(now))
    root.set("stale", _iso(now + timedelta(seconds=stale_seconds)))

    point = ET.SubElement(root, "point")
    point.set("lat", f"{pos.lat:.6f}")
    point.set("lon", f"{pos.lon:.6f}")
    point.set("hae", "9999999.0")
    point.set("ce", f"{pos.accuracy_m:.1f}" if pos.accuracy_m is not None else "9999999.0")
    point.set("le", "9999999.0")

    detail = ET.SubElement(root, "detail")
    contact = ET.SubElement(detail, "contact")
    contact.set("callsign", callsign)
    track = ET.SubElement(detail, "track")
    track.set("course", f"{course:.1f}")
    track.set("speed", f"{pos.speed_kmh / 3.6:.2f}")
    src = ET.SubElement(detail, "precisionlocation")
    src.set("geopointsrc", "GPS")
    src.set("altsrc", "???")
    rem = ET.SubElement(detail, "remarks")
    rem.text = remarks_text
    # No XML declaration, UTF-8 bytes (ä/ö in remarks).
    return ET.tostring(root, encoding="unicode").encode("utf-8")
