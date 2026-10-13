# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Street3r
"""
Thread-safe state shared by the MQTT callbacks and the send loop.

Positions, timetables and compositions arrive separately (MQTT, plus REST at
startup) and are joined by (departureDate, trainNumber).
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from . import cot, naming
from .config import Settings
from .model import Composition, HslTrip, Position, TrainInfo, TrainKey

EARTH_RADIUS_M = 6371000.0
MIN_MOVE_FOR_COURSE_M = 15.0


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _angle_diff(a: float, b: float) -> float:
    """Smallest difference between two bearings, 0-180."""
    d = abs(a - b) % 360.0
    return 360.0 - d if d > 180.0 else d


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing from point 1 to point 2, 0-360 degrees."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


@dataclass
class Tracked:
    """A train with a GPS position."""

    pos: Position
    course: float = 0.0
    anchor_lat: Optional[float] = None  # last point used for the course
    anchor_lon: Optional[float] = None
    sent_ts: Optional[datetime] = None  # GPS timestamp last sent
    sent_at: float = 0.0  # time.time() of last send
    hsl_uid: str = ""  # UID of the matched HSL trip
    hsl_until: float = 0.0  # keep using hsl_uid until this time


@dataclass
class Stats:
    """Counters for the periodic log line."""

    sent: int = 0
    hsl_uid: int = 0
    hsl_skipped: int = 0
    no_timetable: int = 0
    filtered: int = 0


class TrainStore:
    """All feeder state."""

    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.tz = ZoneInfo(settings.timezone)
        self._lock = threading.Lock()
        self.tracked: dict[TrainKey, Tracked] = {}
        self.trains: dict[TrainKey, TrainInfo] = {}
        self.compositions: dict[TrainKey, Composition] = {}
        self.hsl_trips: dict[str, HslTrip] = {}
        self.stations: dict[str, str] = {}
        self.operators: dict[str, str] = {}
        self.missing_since: dict[tuple[str, TrainKey], float] = {}

    # ------------------------------------------------------------- updates
    def update_position(self, pos: Position) -> None:
        """Store a GPS fix and update the course when the train has moved."""
        with self._lock:
            tr = self.tracked.get(pos.key)
            if tr is None:
                self.tracked[pos.key] = Tracked(pos, anchor_lat=pos.lat, anchor_lon=pos.lon)
                return
            if pos.timestamp < tr.pos.timestamp:
                return  # out of order
            if tr.anchor_lat is not None and tr.anchor_lon is not None:
                moved = distance_m(tr.anchor_lat, tr.anchor_lon, pos.lat, pos.lon)
                if moved >= MIN_MOVE_FOR_COURSE_M:
                    tr.course = bearing_deg(tr.anchor_lat, tr.anchor_lon, pos.lat, pos.lon)
                    tr.anchor_lat, tr.anchor_lon = pos.lat, pos.lon
            tr.pos = pos

    def update_train(self, info: TrainInfo) -> None:
        """Store a timetable, keeping the newest version."""
        with self._lock:
            old = self.trains.get(info.key)
            if old is None or info.version >= old.version:
                self.trains[info.key] = info

    def update_composition(self, comp: Composition) -> None:
        """Store rolling stock."""
        with self._lock:
            self.compositions[comp.key] = comp

    def update_hsl(self, trip: HslTrip) -> None:
        """Store a train trip seen on HSL's feed."""
        with self._lock:
            self.hsl_trips[trip.uid] = trip

    def set_metadata(self, stations: dict[str, str], operators: dict[str, str]) -> None:
        """Replace station and operator names (empty dicts are ignored)."""
        with self._lock:
            if stations:
                self.stations = stations
            if operators:
                self.operators = operators

    def missing(self, now: float, limit: int, retry_after: float = 600) -> list[tuple[str, TrainKey]]:
        """
        ("train"|"composition", key) for tracked trains still lacking that
        data, at most `limit`, each asked again only after `retry_after` s.
        Timetables come first; trains without a departure date have neither.
        """
        with self._lock:
            result: list[tuple[str, TrainKey]] = []
            for kind, have in (("train", self.trains), ("composition", self.compositions)):
                for key in self.tracked:
                    if len(result) >= limit:
                        return result
                    if key in have or not key[0]:
                        continue
                    last = self.missing_since.get((kind, key))
                    if last is None or now - last >= retry_after:
                        self.missing_since[(kind, key)] = now
                        result.append((kind, key))
            return result

    def prune(self, now: datetime) -> None:
        """Forget old positions, trains and HSL trips."""
        local_today = now.astimezone(self.tz).date()
        keep_from = (local_today - timedelta(days=1)).isoformat()
        pos_cutoff = now - timedelta(hours=1)
        hsl_cutoff = time.time() - 600
        with self._lock:
            self.tracked = {k: v for k, v in self.tracked.items() if v.pos.timestamp >= pos_cutoff}
            self.trains = {k: v for k, v in self.trains.items() if k[0] >= keep_from or k in self.tracked}
            self.compositions = {
                k: v for k, v in self.compositions.items() if k[0] >= keep_from or k in self.tracked
            }
            self.hsl_trips = {k: v for k, v in self.hsl_trips.items() if v.seen >= hsl_cutoff}
            self.missing_since = {k: v for k, v in self.missing_since.items() if k[1] in self.tracked}

    # ------------------------------------------------------------ matching
    def _assign_hsl(self, now: datetime) -> dict[TrainKey, HslTrip]:
        """
        Pair commuter trains with trips on HSL's feed: same line letter and
        operating day, both seen recently, within the match distance, and not
        heading in opposite directions (trains of the same line pass each
        other). Pairs are assigned one-to-one, closest first.
        Caller holds the lock.
        """
        now_ts = now.timestamp()
        max_age = timedelta(seconds=self.s.max_position_age)
        trips = [t for t in self.hsl_trips.values() if now_ts - t.seen <= self.s.hsl_max_age]
        if not trips:
            return {}
        pairs: list[tuple[float, TrainKey, HslTrip]] = []
        for key, tr in self.tracked.items():
            info = self.trains.get(key)
            if not naming.is_commuter(info) or now - tr.pos.timestamp > max_age:
                continue
            assert info is not None
            for trip in trips:
                if trip.line != info.commuter_line:
                    continue
                if trip.oday and key[0] and trip.oday != key[0]:
                    continue
                d = distance_m(tr.pos.lat, tr.pos.lon, trip.lat, trip.lon)
                if d > self.s.hsl_match_distance:
                    continue
                moving = tr.pos.speed_kmh >= 10 and tr.anchor_lat is not None
                if moving and trip.heading is not None and _angle_diff(tr.course, trip.heading) > 90:
                    continue
                pairs.append((d, key, trip))
        pairs.sort(key=lambda p: p[0])
        result: dict[TrainKey, HslTrip] = {}
        used: set[str] = set()
        for _, key, trip in pairs:
            if key in result or trip.uid in used:
                continue
            result[key] = trip
            used.add(trip.uid)
        return result

    # ---------------------------------------------------------------- output
    def events(self, now: datetime, stats: Stats) -> list[bytes]:
        """CoT events for trains whose position changed or needs refreshing."""
        now_ts = now.timestamp()
        max_age = timedelta(seconds=self.s.max_position_age)
        out: list[bytes] = []
        with self._lock:
            hsl = self._assign_hsl(now) if self.s.hsl_mode != "off" else {}
            for key, tr in self.tracked.items():
                pos = tr.pos
                if now - pos.timestamp > max_age:
                    continue
                matched = hsl.get(key)
                if matched is not None:
                    tr.hsl_uid = matched.uid
                    tr.hsl_until = now_ts + self.s.hsl_max_age
                hsl_uid = tr.hsl_uid if tr.hsl_uid and now_ts <= tr.hsl_until else ""
                changed = tr.sent_ts != pos.timestamp
                if not changed and now_ts - tr.sent_at < self.s.refresh_interval:
                    continue
                info = self.trains.get(key)
                if info is None:
                    stats.no_timetable += 1
                if self.s.categories and (info is None or info.category not in self.s.categories):
                    stats.filtered += 1
                    continue

                uid = naming.native_uid(key, self.s.uid_prefix)
                if hsl_uid:
                    if self.s.hsl_mode == "skip":
                        stats.hsl_skipped += 1
                        tr.sent_ts, tr.sent_at = pos.timestamp, now_ts
                        continue
                    uid = hsl_uid
                    stats.hsl_uid += 1

                brands = self.s.commuter_brands
                call = naming.callsign(key, info, self.s.operator_callsigns, brands)
                tags = naming.hashtags(key, info, self.s.feed_tag, brands)
                text = cot.remarks(
                    pos,
                    info,
                    self.compositions.get(key),
                    self.stations,
                    self.operators,
                    call,
                    naming.commuter_brand(info, brands),
                    tags,
                    self.s.source_line,
                    self.tz,
                    now,
                )
                out.append(
                    cot.build_event(
                        uid, self.s.cot_type, call, pos, tr.course, text, now, self.s.stale_seconds
                    )
                )
                tr.sent_ts, tr.sent_at = pos.timestamp, now_ts
                stats.sent += 1
        return out

    def counts(self) -> dict[str, int]:
        """Sizes for logging."""
        with self._lock:
            return {
                "tracked": len(self.tracked),
                "trains": len(self.trains),
                "compositions": len(self.compositions),
                "hsl_trips": len(self.hsl_trips),
                "stations": len(self.stations),
            }


def utcnow() -> datetime:
    """Current time, UTC."""
    return datetime.now(timezone.utc)
