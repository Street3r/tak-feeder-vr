# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Street3r
"""
Data model and parsers for Digitraffic rail JSON.

Field names follow the REST/MQTT responses documented at
https://www.digitraffic.fi/rautatieliikenne/ (types "Junat", "Kokoonpanot",
"GPS-sijainnit", "Liikennepaikat", "Operaattorit"). Everything is reduced to
the fields the feeder needs so that a full day of trains stays small in memory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

TrainKey = tuple[str, str]  # (departureDate, trainNumber); date may be ""


def parse_time(value: Any) -> Optional[datetime]:
    """Parse a Digitraffic ISO-8601 UTC timestamp ("...Z")."""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except ValueError:
        return None


def train_key(departure_date: Any, train_number: Any) -> TrainKey:
    """Key shared by positions, trains and compositions."""
    return (str(departure_date or ""), str(train_number))


@dataclass
class Position:
    """Latest GPS fix of one train."""

    key: TrainKey
    lat: float
    lon: float
    timestamp: datetime
    speed_kmh: float
    accuracy_m: Optional[float]


def parse_position(data: dict[str, Any]) -> Optional[Position]:
    """Parse one element of /train-locations or an MQTT train-locations message."""
    try:
        coords = data["location"]["coordinates"]
        lon, lat = float(coords[0]), float(coords[1])
        number = data["trainNumber"]
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    ts = parse_time(data.get("timestamp"))
    if ts is None:
        return None
    speed = data.get("speed")
    accuracy = data.get("accuracy")
    return Position(
        key=train_key(data.get("departureDate"), number),
        lat=lat,
        lon=lon,
        timestamp=ts,
        speed_kmh=float(speed) if isinstance(speed, (int, float)) else 0.0,
        accuracy_m=float(accuracy) if isinstance(accuracy, (int, float)) else None,
    )


@dataclass
class TimetableRow:
    """One arrival or departure on a train's route."""

    station: str
    type: str  # ARRIVAL / DEPARTURE
    stopping: bool
    commercial: Optional[bool]
    scheduled: Optional[datetime]
    estimate: Optional[datetime]
    actual: Optional[datetime]
    difference_min: Optional[int]
    unknown_delay: bool
    cancelled: bool


@dataclass
class TrainInfo:
    """Timetable and classification of one train."""

    key: TrainKey
    operator: str
    train_type: str
    category: str
    commuter_line: str
    running: bool
    cancelled: bool
    version: int
    rows: list[TimetableRow] = field(default_factory=list)


def parse_train(data: dict[str, Any]) -> Optional[TrainInfo]:
    """Parse one element of /trains, /live-trains or an MQTT trains message."""
    try:
        number = data["trainNumber"]
    except (KeyError, TypeError):
        return None
    rows = []
    for row in data.get("timeTableRows") or []:
        diff = row.get("differenceInMinutes")
        rows.append(
            TimetableRow(
                station=str(row.get("stationShortCode") or ""),
                type=str(row.get("type") or ""),
                stopping=bool(row.get("trainStopping")),
                commercial=row.get("commercialStop"),
                scheduled=parse_time(row.get("scheduledTime")),
                estimate=parse_time(row.get("liveEstimateTime")),
                actual=parse_time(row.get("actualTime")),
                difference_min=diff if isinstance(diff, int) else None,
                unknown_delay=bool(row.get("unknownDelay")),
                cancelled=bool(row.get("cancelled")),
            )
        )
    version = data.get("version")
    return TrainInfo(
        key=train_key(data.get("departureDate"), number),
        operator=str(data.get("operatorShortCode") or ""),
        train_type=str(data.get("trainType") or ""),
        category=str(data.get("trainCategory") or ""),
        # REST uses commuterLineID, GraphQL commuterLineid
        commuter_line=str(
            data.get("commuterLineID") or data.get("commuterLineid") or ""
        ),
        running=bool(data.get("runningCurrently")),
        cancelled=bool(data.get("cancelled")) or bool(data.get("deleted")),
        version=version if isinstance(version, int) else 0,
        rows=rows,
    )


@dataclass
class JourneySection:
    """Part of a journey driven with one composition."""

    begin: Optional[datetime]
    end: Optional[datetime]
    locomotives: list[str]
    wagons: int
    length_m: Optional[int]
    max_speed: Optional[int]


@dataclass
class Composition:
    """Rolling stock of one train."""

    key: TrainKey
    sections: list[JourneySection]

    def section_at(self, when: datetime) -> Optional[JourneySection]:
        """Return the section in use at `when`, or the first one."""
        for sec in self.sections:
            if sec.begin and sec.end and sec.begin <= when <= sec.end:
                return sec
        return self.sections[0] if self.sections else None


def parse_composition(data: dict[str, Any]) -> Optional[Composition]:
    """Parse one element of /compositions or an MQTT compositions message."""
    try:
        number = data["trainNumber"]
    except (KeyError, TypeError):
        return None
    sections = []
    for sec in data.get("journeySections") or []:
        locos: list[str] = []
        for loco in sec.get("locomotives") or []:
            ltype = loco.get("locomotiveType")
            if ltype and ltype not in locos:
                locos.append(str(ltype))
        length = sec.get("totalLength")
        speed = sec.get("maximumSpeed")
        sections.append(
            JourneySection(
                begin=parse_time((sec.get("beginTimeTableRow") or {}).get("scheduledTime")),
                end=parse_time((sec.get("endTimeTableRow") or {}).get("scheduledTime")),
                locomotives=locos,
                wagons=len(sec.get("wagons") or []),
                length_m=length if isinstance(length, int) else None,
                max_speed=speed if isinstance(speed, int) else None,
            )
        )
    return Composition(key=train_key(data.get("departureDate"), number), sections=sections)


def parse_stations(data: Any) -> dict[str, str]:
    """Map station short code -> display name ("Helsinki asema" -> "Helsinki")."""
    result: dict[str, str] = {}
    for item in data if isinstance(data, list) else []:
        code = item.get("stationShortCode")
        name = item.get("stationName")
        if not code or not name:
            continue
        name = str(name)
        if name.endswith(" asema"):
            name = name[: -len(" asema")]
        result[str(code)] = name
    return result


def parse_operators(data: Any) -> dict[str, str]:
    """Map operator short code -> operator name."""
    result: dict[str, str] = {}
    for item in data if isinstance(data, list) else []:
        code = item.get("operatorShortCode")
        if code:
            result[str(code)] = str(item.get("operatorName") or code)
    return result


@dataclass
class HslTrip:
    """A train trip seen on HSL's HFP feed (used to match UIDs with tak-feeder-hsl)."""

    uid: str
    line: str  # passenger-visible line, e.g. "I"
    oday: str
    start: str
    lat: float
    lon: float
    heading: Optional[float]
    seen: float  # time.time()


def parse_hsl_vp(topic: str, payload: dict[str, Any], now: float) -> Optional[HslTrip]:
    """
    Parse an HSL HFP vehicle position on a train topic.

    The UID is built exactly like sgofferj's tak-feeder-hsl and
    trakbridge-plugin-hsl do it, so both feeders produce the same marker:
    HSL-<mode>-<route>-<oday>-<start>-<dir>, mode and route taken from the topic.
    """
    parts = topic.split("/")
    # /hfp/v2/journey/ongoing/vp/<mode>/<oper>/<veh>/<route>/...
    if len(parts) < 10:
        return None
    mode, route = parts[6], parts[9]
    vp = payload.get("VP") if isinstance(payload, dict) else None
    if not vp:
        return None
    lat, lon = vp.get("lat"), vp.get("long")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    trip = f"{mode}-{route}-{vp.get('oday')}-{vp.get('start')}-{vp.get('dir')}"
    hdg = vp.get("hdg")
    return HslTrip(
        uid=f"HSL-{trip}",
        line=str(vp.get("desi") or ""),
        oday=str(vp.get("oday") or ""),
        start=str(vp.get("start") or ""),
        lat=float(lat),
        lon=float(lon),
        heading=float(hdg) if isinstance(hdg, (int, float)) else None,
        seen=now,
    )
