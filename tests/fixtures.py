# SPDX-License-Identifier: GPL-3.0-or-later
"""Sample messages shaped like the documented Digitraffic and HSL HFP formats."""

from __future__ import annotations

from typing import Any

DATE = "2026-10-13"


def position(number: Any = 59, lat: float = 60.1987, lon: float = 24.9334,
             ts: str = "2026-10-13T09:00:00.000Z", speed: Any = 108,
             accuracy: Any = 8, date: Any = DATE) -> dict[str, Any]:
    return {
        "trainNumber": number,
        "departureDate": date,
        "timestamp": ts,
        "location": {"type": "Point", "coordinates": [lon, lat]},
        "speed": speed,
        "accuracy": accuracy,
    }


def row(station: str, rtype: str, scheduled: str, actual: Any = None,
        estimate: Any = None, diff: Any = None, stopping: bool = True,
        unknown_delay: bool = False) -> dict[str, Any]:
    r: dict[str, Any] = {
        "stationShortCode": station,
        "stationUICCode": 1,
        "countryCode": "FI",
        "type": rtype,
        "trainStopping": stopping,
        "commercialStop": stopping,
        "cancelled": False,
        "scheduledTime": scheduled,
        "causes": [],
    }
    if actual:
        r["actualTime"] = actual
    if estimate:
        r["liveEstimateTime"] = estimate
    if diff is not None:
        r["differenceInMinutes"] = diff
    if unknown_delay:
        r["unknownDelay"] = True
    return r


def long_distance(number: int = 59, version: int = 100) -> dict[str, Any]:
    return {
        "trainNumber": number,
        "departureDate": DATE,
        "operatorUICCode": 10,
        "operatorShortCode": "vr",
        "trainType": "IC",
        "trainCategory": "Long-distance",
        "commuterLineID": "",
        "runningCurrently": True,
        "cancelled": False,
        "version": version,
        "timetableType": "REGULAR",
        "timetableAcceptanceDate": "2026-01-01T00:00:00.000Z",
        "timeTableRows": [
            row("HKI", "DEPARTURE", "2026-10-13T08:52:00.000Z", actual="2026-10-13T08:53:00.000Z", diff=1),
            row("PSL", "ARRIVAL", "2026-10-13T08:57:00.000Z", estimate="2026-10-13T09:00:00.000Z", diff=3),
            row("PSL", "DEPARTURE", "2026-10-13T08:58:00.000Z", estimate="2026-10-13T09:01:00.000Z", diff=3),
            row("OL", "ARRIVAL", "2026-10-13T15:00:00.000Z"),
        ],
    }


def commuter(number: int = 9655, line: str = "R", operator: str = "vr") -> dict[str, Any]:
    t = long_distance(number)
    t.update(
        {
            "trainType": "HL",
            "trainCategory": "Commuter",
            "commuterLineID": line,
            "operatorShortCode": operator,
        }
    )
    return t


def cargo(number: int = 3021, operator: str = "fenniarail") -> dict[str, Any]:
    t = long_distance(number)
    t.update({"trainType": "T", "trainCategory": "Cargo", "operatorShortCode": operator})
    return t


def composition(number: int = 59) -> dict[str, Any]:
    return {
        "trainNumber": number,
        "departureDate": DATE,
        "operatorShortCode": "vr",
        "trainCategory": "Long-distance",
        "trainType": "IC",
        "version": 5,
        "journeySections": [
            {
                "beginTimeTableRow": {"stationShortCode": "HKI", "scheduledTime": "2026-10-13T08:52:00.000Z"},
                "endTimeTableRow": {"stationShortCode": "OL", "scheduledTime": "2026-10-13T15:00:00.000Z"},
                "locomotives": [{"location": 1, "locomotiveType": "Sr2", "powerType": "Sähkö"}],
                "wagons": [{"location": i, "salesNumber": i} for i in range(1, 8)],
                "totalLength": 189,
                "maximumSpeed": 200,
            }
        ],
    }


STATIONS = [
    {"stationShortCode": "HKI", "stationName": "Helsinki asema", "passengerTraffic": True},
    {"stationShortCode": "PSL", "stationName": "Pasila asema", "passengerTraffic": True},
    {"stationShortCode": "OL", "stationName": "Oulu asema", "passengerTraffic": True},
]

OPERATORS = [
    {"operatorShortCode": "vr", "operatorName": "VR-Yhtymä Oyj", "operatorUICCode": 10},
    {"operatorShortCode": "fenniarail", "operatorName": "Fenniarail Oy", "operatorUICCode": 30},
]


def hsl_vp(line: str = "R", route: str = "3001R", lat: float = 60.1990, lon: float = 24.9340,
           hdg: Any = 10, start: str = "11:52", direction: str = "1",
           oday: str = DATE) -> tuple[str, dict[str, Any]]:
    topic = f"/hfp/v2/journey/ongoing/vp/train/0090/01234/{route}/{direction}/Riihimäki/{start}/1020601/3/60;24/19/93/34"
    payload = {
        "VP": {
            "desi": line, "dir": direction, "oper": 90, "veh": 1234,
            "tst": "2026-10-13T09:00:00.000Z", "spd": 30.0, "hdg": hdg,
            "lat": lat, "long": lon, "oday": oday, "start": start, "route": route,
        }
    }
    return topic, payload
