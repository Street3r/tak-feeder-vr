# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Street3r
"""Track works and traffic restrictions (Fintraffic RUMA) as a TAK mission.

Digitraffic publishes the notifications as GeoJSON. Each notification is one
feature with an approximate location and the notification fields, plus one
feature per location (`notificationId`) whose geometry is the real place:
points, track lines or work area polygons. Active notifications become one
marker each and their lines and areas become ATAK shapes, all in one mission.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import time
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

from .common import cot_time, remarks_text
from .sources import http_get_json

LOG = logging.getLogger("tak_feeder_vr.railworks")

# ATAK colours (signed ARGB)
RED = "-65536"  # FFFF0000
ORANGE = "-23296"  # FFFFA500
YELLOW = "-256"  # FFFFFF00
GREY = "-7829368"  # FF888888

# limitation -> (Finnish, English, colour, tag)
LIMITATIONS: dict[str, tuple[str, str, str, str]] = {
    "CLOSED_FROM_TRAFFIC": ("Rata suljettu", "Closed to traffic", RED, "suljettu"),
    "CLOSED_FROM_ELECTRIC_ROLLING_STOCK": (
        "Suljettu sähköjunilta", "Closed to electric trains", YELLOW, "sähkökatko"),
    "TEMPORARY_SPEED_LIMIT": (
        "Tilapäinen nopeusrajoitus", "Temporary speed limit", ORANGE, "nopeusrajoitus"),
    "AXLE_WEIGHT_MAX": ("Akselipainorajoitus", "Axle weight limit", ORANGE, "akselipaino"),
    "ATP_CONSTRUCTION_ZONE": (
        "JKV-rakennusalue", "ATP construction zone", ORANGE, "jkv"),
    "SWITCH_LOCKED": ("Vaihde lukittu", "Switch locked", ORANGE, "vaihde"),
    "FIREWORK_DANGER_ZONE": ("Tulityövaara", "Fire work danger zone", RED, "tulityö"),
    "OTHER": ("Liikennerajoitus", "Traffic restriction", ORANGE, "rajoitus"),
}
TRACKWORK = ("Ratatyö", "Track work", GREY, "ratatyö")

ICON = "6d781afb-89a6-4c07-b2b9-a89748b6a38f/Transport/caution.png"
KINDS = ("restrictions", "trackworks")


def _bool(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _host_of(url: str) -> str:
    """TAK server host of a COT_URL like ssl://takserver:8090 (not of UDP or
    multicast outputs, which have no API)."""
    parts = urllib.parse.urlsplit(url)
    return (parts.hostname or "") if parts.scheme in ("ssl", "tls", "tcp") else ""


@dataclass
class RailworksSettings:
    """RAILWORKS_* settings (see .env.example)."""

    enabled: bool = True
    api_host: str = ""
    cert: str = ""
    key: str = ""
    api_url: str = "https://rata.digitraffic.fi/api/v1"
    digitraffic_user: str = "tak-feeder-vr"
    mission_name: str = "RailworksFin"
    groups: list[str] = field(default_factory=list)
    include: tuple[str, ...] = KINDS
    creator_uid: str = "vr-railworks"
    update_interval: float = 600.0
    retry_interval: float = 300.0
    refresh_interval: float = 24 * 3600.0
    lookback_days: int = 90
    max_points: int = 250
    source_line: str = "Source: Fintraffic / digitraffic.fi, CC BY 4.0"
    timezone: str = "Europe/Helsinki"

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "RailworksSettings":
        """Settings from the environment. API_HOST defaults to the COT_URL host."""
        d = cls()

        def get(name: str, default: str) -> str:
            value = env.get(name)
            return value if value not in (None, "") else default

        include = tuple(k for k in (p.strip().lower() for p in
                                    get("RAILWORKS_INCLUDE", ",".join(KINDS)).split(","))
                        if k in KINDS)
        cert = get("PYTAK_TLS_CLIENT_CERT", env.get("CLIENT_CERT", ""))
        key = get("PYTAK_TLS_CLIENT_KEY", env.get("CLIENT_KEY", ""))
        return cls(
            enabled=_bool(get("RAILWORKS", "1")),
            api_host=get("API_HOST", _host_of(get("COT_URL", ""))),
            cert=os.path.abspath(cert) if cert else "",
            key=os.path.abspath(key) if key else "",
            api_url=get("DIGITRAFFIC_API_URL", d.api_url).rstrip("/"),
            digitraffic_user=get("DIGITRAFFIC_USER", d.digitraffic_user),
            mission_name=get("RAILWORKS_MISSION_NAME", d.mission_name),
            groups=[g.strip() for g in env.get("RAILWORKS_MISSION_GROUP", "").split(",")
                    if g.strip()],
            include=include,
            creator_uid=get("RAILWORKS_UID", d.creator_uid),
            update_interval=max(60.0, float(get("RAILWORKS_UPDATE_INTERVAL",
                                                str(d.update_interval)))),
            retry_interval=float(get("RAILWORKS_RETRY_INTERVAL", str(d.retry_interval))),
            refresh_interval=float(get("RAILWORKS_REFRESH_HOURS", "24")) * 3600.0,
            lookback_days=int(get("RAILWORKS_LOOKBACK_DAYS", str(d.lookback_days))),
            max_points=max(2, int(get("RAILWORKS_MAX_POINTS", str(d.max_points)))),
            source_line=get("SOURCE_LINE", d.source_line),
            timezone=get("TIMEZONE", d.timezone),
        )


# ------------------------------------------------------------------ parsing
def _parse_time(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class Notice:
    """One track work or traffic restriction notification."""

    kind: str  # "restriction" or "trackwork"
    id: str
    props: dict[str, Any]
    point: Optional[tuple[float, float]]  # (lon, lat)
    geometries: list[dict[str, Any]] = field(default_factory=list)

    @property
    def start(self) -> Optional[datetime]:
        return _parse_time(self.props.get("startDate"))

    @property
    def end(self) -> Optional[datetime]:
        return _parse_time(self.props.get("endDate"))

    def active(self, now: datetime) -> bool:
        """Restrictions: started, not ended or finished. Track works are asked
        for as ACTIVE already."""
        if self.kind == "trackwork":
            return str(self.props.get("state", "ACTIVE")).upper() == "ACTIVE"
        if self.props.get("finished") or str(self.props.get("state", "")).upper() in (
                "FINISHED", "REMOVED", "DRAFT"):
            return False
        start, end = self.start, self.end
        return (start is None or start <= now) and (end is None or end > now)


def _first_point(geometry: Any) -> Optional[tuple[float, float]]:
    """First coordinate of any geometry, as (lon, lat)."""
    if not isinstance(geometry, dict):
        return None
    if geometry.get("type") == "GeometryCollection":
        for g in geometry.get("geometries") or []:
            p = _first_point(g)
            if p:
                return p
        return None
    coords: Any = geometry.get("coordinates")
    while isinstance(coords, list) and coords and isinstance(coords[0], list):
        coords = coords[0]
    if isinstance(coords, list) and len(coords) >= 2:
        try:
            return float(coords[0]), float(coords[1])
        except (TypeError, ValueError):
            return None
    return None


def parse_notices(fc: Any, kind: str) -> list[Notice]:
    """Group a FeatureCollection into notices: the feature with `id` is the
    notice, features with `notificationId` are its locations."""
    features = fc.get("features") if isinstance(fc, dict) else None
    if not isinstance(features, list):
        return []
    notices: dict[str, Notice] = {}
    locations: list[tuple[str, dict[str, Any]]] = []
    for f in features:
        if not isinstance(f, dict):
            continue
        props = f.get("properties") or {}
        geom = f.get("geometry")
        if props.get("notificationId"):
            if isinstance(geom, dict):
                locations.append((str(props["notificationId"]), geom))
        elif props.get("id"):
            nid = str(props["id"])
            notices[nid] = Notice(kind, nid, props, _first_point(geom))
    for nid, geom in locations:
        if nid in notices:
            notices[nid].geometries.append(geom)
    for n in notices.values():
        if n.point is None:
            n.point = next((p for p in map(_first_point, n.geometries) if p), None)
    return [n for n in notices.values() if n.point is not None]


def parse_stations(data: Any) -> list[tuple[float, float, str]]:
    """(lat, lon, name) of every operating point in the station list."""
    out = []
    for item in data if isinstance(data, list) else []:
        try:
            lat, lon = float(item["latitude"]), float(item["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        name = str(item.get("stationName") or "")
        if name.endswith(" asema"):
            name = name[: -len(" asema")]
        if name:
            out.append((lat, lon, name))
    return out


def nearest_station(lat: float, lon: float,
                    stations: list[tuple[float, float, str]]) -> str:
    """Name of the closest station (flat-earth distance is enough here)."""
    best, best_d = "", math.inf
    k = math.cos(math.radians(lat))
    for s_lat, s_lon, name in stations:
        d = (s_lat - lat) ** 2 + ((s_lon - lon) * k) ** 2
        if d < best_d:
            best, best_d = name, d
    return best


# ---------------------------------------------------------------------- CoT
def _thin(coords: list[Any], max_points: int) -> list[Any]:
    """Keep at most max_points, always the first and last (TAK drops CoT over
    about 64 KB)."""
    if len(coords) <= max_points:
        return coords
    step = (len(coords) - 1) / (max_points - 1)
    return [coords[round(i * step)] for i in range(max_points)]


def _shapes(geometry: dict[str, Any]) -> list[tuple[str, list[Any]]]:
    """("line"|"area", [[lon, lat], ...]) parts of a geometry. Points are
    left out, the notice marker covers them."""
    gtype = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if gtype == "LineString":
        return [("line", coords)]
    if gtype == "MultiLineString":
        return [("line", c) for c in coords]
    if gtype == "Polygon":
        return [("area", coords[0])] if coords else []
    if gtype == "MultiPolygon":
        return [("area", c[0]) for c in coords if c]
    if gtype == "GeometryCollection":
        return [s for g in geometry.get("geometries") or [] for s in _shapes(g)]
    return []


def _label(n: Notice) -> tuple[str, str, str, str]:
    if n.kind == "trackwork":
        return TRACKWORK
    return LIMITATIONS.get(str(n.props.get("limitation") or "OTHER"), LIMITATIONS["OTHER"])


def _local(dt: Optional[datetime], tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%d.%m. %H:%M") if dt else "?"


def notice_remarks(n: Notice, near: str, s: RailworksSettings) -> str:
    fi, en, _colour, tag = _label(n)
    tz = ZoneInfo(s.timezone)
    lines = [f"{fi} · {en}"]
    if n.kind == "restriction":
        if n.start or n.end:
            lines.append(f"Valid: {_local(n.start, tz)} – "
                         f"{_local(n.end, tz) if n.end else 'until further notice'}")
        if n.props.get("axleWeightMax"):
            lines.append(f"Max axle weight: {n.props['axleWeightMax']} t")
    if near:
        lines.append(f"Near: {near}")
    if n.props.get("organization"):
        lines.append(f"Organization: {n.props['organization']}")
    version = f", version {n.props['version']}" if n.props.get("version") else ""
    lines.append(f"Notice: {n.id}{version}")
    if n.props.get("trackWorkNotificationId"):
        lines.append(f"Track work: {n.props['trackWorkNotificationId']}")
    tags = ["rata", "railway"]
    tags += (["ratatyö", "trackwork"] if n.kind == "trackwork"
             else ["rajoitus", "restriction", tag])
    return remarks_text(lines, s.source_line, tags)


def _event(uid: str, cot_type: str, lat: float, lon: float, stale: datetime,
           now: datetime, mission: str, callsign: str, remarks: str) -> tuple[ET.Element, ET.Element]:
    root = ET.Element("event", {
        "version": "2.0", "type": cot_type, "uid": uid, "how": "h-e",
        "time": cot_time(now), "start": cot_time(now), "stale": cot_time(stale)})
    ET.SubElement(root, "point", {"lat": f"{lat:.6f}", "lon": f"{lon:.6f}", "hae": "0",
                                  "ce": "9999999", "le": "9999999"})
    detail = ET.SubElement(root, "detail")
    marti = ET.SubElement(detail, "marti")
    ET.SubElement(marti, "dest", {"mission": mission})
    ET.SubElement(detail, "contact", {"callsign": callsign})
    ET.SubElement(detail, "remarks").text = remarks
    return root, detail


def build_cot_files(notices: list[Notice], stations: list[tuple[float, float, str]],
                    s: RailworksSettings, now: Optional[datetime] = None) -> dict[str, bytes]:
    """uid -> CoT of every marker and shape."""
    now = now or datetime.now(timezone.utc)
    files: dict[str, bytes] = {}
    for n in notices:
        fi, _en, colour, _tag = _label(n)
        lon, lat = n.point  # type: ignore[misc]
        near = nearest_station(lat, lon, stations) if stations else ""
        callsign = f"{fi}: {near}" if near else fi
        remarks = notice_remarks(n, near, s)
        end = n.end if n.kind == "restriction" else None
        stale = end if end and end > now else now + timedelta(seconds=2 * s.refresh_interval)
        prefix = "RUMA-" + ("TR" if n.kind == "restriction" else "TW") + f"-{n.id}"

        root, detail = _event(prefix, "a-n-G", lat, lon, stale, now, s.mission_name,
                              callsign, remarks)
        ET.SubElement(detail, "usericon", {"iconsetpath": ICON})
        ET.SubElement(detail, "color", {"argb": colour})
        files[prefix] = ET.tostring(root)

        parts = [p for g in n.geometries for p in _shapes(g)]
        for i, (kind, coords) in enumerate(parts):
            pts = [c for c in coords if isinstance(c, list) and len(c) >= 2]
            if len(pts) < 2:
                continue
            pts = _thin(pts, s.max_points)
            c_lon, c_lat = pts[len(pts) // 2][:2]
            root, detail = _event(f"{prefix}.{i}", "u-d-f", float(c_lat), float(c_lon), stale,
                                  now, s.mission_name, callsign, remarks)
            for p_lon, p_lat in (p[:2] for p in pts):
                ET.SubElement(detail, "link", {"point": f"{float(p_lat):.6f},{float(p_lon):.6f}"})
            ET.SubElement(detail, "strokeColor", {"value": colour})
            ET.SubElement(detail, "strokeWeight", {"value": "4" if kind == "line" else "2"})
            ET.SubElement(detail, "strokeStyle", {"value": "solid"})
            if kind == "area":
                # same colour, about 25 % opaque
                fill = (int(colour) & 0x00FFFFFF) | 0x40000000
                ET.SubElement(detail, "fillColor", {"value": str(fill)})
            ET.SubElement(detail, "labels_on", {"value": "false"})
            files[f"{prefix}.{i}"] = ET.tostring(root)
    return files


# --------------------------------------------------------------------- run
class Railworks:
    """Fetches the notices and keeps the mission in sync."""

    def __init__(self, s: RailworksSettings, mission: Any) -> None:
        self.s = s
        self.mission = mission
        self.stations: list[tuple[float, float, str]] = []
        self._stations_at = 0.0
        self._refreshed_at = time.monotonic()

    def _get(self, path: str, params: Optional[dict[str, str]] = None) -> Any:
        url = f"{self.s.api_url}/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return http_get_json(url, self.s.digitraffic_user)

    def _fetch_window(self, path: str, state: str, now: datetime) -> Any:
        """Notices changed within the look-back window. Digitraffic limits the
        window and the answer size, so a refused long window is retried shorter."""
        last_error: Optional[Exception] = None
        for days in dict.fromkeys((self.s.lookback_days, 30, 7)):
            params = {"state": state,
                      "start": (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "end": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
            try:
                data = self._get(path, params)
            except urllib.error.HTTPError as exc:
                last_error = exc
                LOG.debug("%s %d days: HTTP %s", path, days, exc.code)
                continue
            if isinstance(data, dict) and isinstance(data.get("features"), list):
                return data
            LOG.debug("%s %d days: no features (%s)", path, days, str(data)[:200])
        raise RuntimeError(f"{path}: no usable answer ({last_error})")

    def fetch(self, now: datetime) -> list[Notice]:
        """Active notices of the kinds in RAILWORKS_INCLUDE."""
        if time.monotonic() - self._stations_at > 24 * 3600 or not self.stations:
            try:
                self.stations = parse_stations(self._get("metadata/stations"))
                self._stations_at = time.monotonic()
            except Exception as exc:  # noqa: BLE001  pylint: disable=broad-exception-caught
                LOG.warning("Station list: %s", exc)
        notices: list[Notice] = []
        if "restrictions" in self.s.include:
            fc = self._fetch_window("trafficrestriction-notifications.geojson", "SENT", now)
            notices += parse_notices(fc, "restriction")
        if "trackworks" in self.s.include:
            fc = self._fetch_window("trackwork-notifications.geojson", "ACTIVE", now)
            notices += parse_notices(fc, "trackwork")
        return [n for n in notices if n.active(now)]

    async def run_once(self) -> bool:
        now = datetime.now(timezone.utc)
        notices = await asyncio.to_thread(self.fetch, now)
        files = build_cot_files(notices, self.stations, self.s, now)
        force = time.monotonic() - self._refreshed_at > self.s.refresh_interval
        uploaded = await self.mission.sync(files, force=force)
        if force:
            self._refreshed_at = time.monotonic()
        LOG.info("Railworks: %d active notices, %d items", len(notices), len(files))
        return bool(uploaded)

    async def run(self) -> None:
        while True:
            try:
                await self.run_once()
                delay = self.s.update_interval
            except Exception as exc:  # noqa: BLE001  pylint: disable=broad-exception-caught
                LOG.warning("Railworks: %s; retrying in %.0f s", exc, self.s.retry_interval)
                delay = self.s.retry_interval
            await asyncio.sleep(delay)


async def run(s: RailworksSettings) -> None:
    """Connect to the TAK API and sync the mission forever. Errors never stop
    the train feed: the connection is retried after RAILWORKS_RETRY_INTERVAL."""
    # pylint: disable=import-outside-toplevel
    from python_takserver_api import Server, build_mission_package  # type: ignore

    from .missions import MissionSync

    while True:
        try:
            server = Server(s.api_host, s.cert, s.key)
            try:
                mission = MissionSync(server, build_mission_package, s.api_host,
                                      s.mission_name, s.creator_uid, s.groups)
                await Railworks(s, mission).run()
            finally:
                await server.close()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001  pylint: disable=broad-exception-caught
            LOG.warning("Railworks: TAK API %s: %s; retrying in %.0f s", s.api_host, exc,
                        s.retry_interval)
            await asyncio.sleep(s.retry_interval)
