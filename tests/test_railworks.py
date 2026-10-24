# SPDX-License-Identifier: GPL-3.0-or-later
"""Track works and traffic restrictions (RUMA) as a mission."""

from __future__ import annotations

import asyncio
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any
from unittest import mock

from tak_feeder_vr import missions, railworks

NOW = datetime(2026, 10, 24, 21, 0, tzinfo=timezone.utc)

# Shortened from the live answer of 2026-10-24
TRACKWORKS = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [25.440306, 65.317779]},
     "properties": {"id": "1.2.246.586.7.1.383787", "state": "ACTIVE",
                    "organization": "Kreate Oy", "version": 5}},
    {"type": "Feature", "geometry": {"type": "GeometryCollection", "geometries": [
        {"type": "Point", "coordinates": [25.441902, 65.318819]},
        {"type": "Polygon", "coordinates": [[[25.437054, 65.310072], [25.443801, 65.316101],
                                             [25.445275, 65.325692], [25.437054, 65.310072]]]}]},
     "properties": {"notificationId": "1.2.246.586.7.1.383787", "workPartIndex": 1,
                    "locationType": "WORK"}},
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [27.262245, 61.462105]},
     "properties": {"id": "1.2.246.586.7.1.384342", "state": "ACTIVE",
                    "organization": "Destia Oy", "version": 2}},
    {"type": "Feature", "geometry": {"type": "MultiLineString", "coordinates": [
        [[26.924063, 61.487737], [26.923966, 61.488173]],
        [[26.923947, 61.488257], [26.923931, 61.48864], [26.923917, 61.488821]]]},
     "properties": {"notificationId": "1.2.246.586.7.1.384342"}},
]}

RESTRICTIONS = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [26.7, 60.87]},
     "properties": {"id": "R1", "state": "SENT", "limitation": "CLOSED_FROM_TRAFFIC",
                    "organization": "Väylävirasto", "startDate": "2026-10-24T06:00:00Z",
                    "endDate": "2026-10-26T16:00:00Z", "finished": False, "version": 1}},
    {"type": "Feature", "geometry": {"type": "LineString",
                                     "coordinates": [[26.70, 60.87], [26.75, 60.80]]},
     "properties": {"notificationId": "R1"}},
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [24.9, 60.2]},
     "properties": {"id": "R2", "state": "SENT", "limitation": "TEMPORARY_SPEED_LIMIT",
                    "startDate": "2026-09-01T00:00:00Z", "endDate": "2026-10-01T00:00:00Z"}},
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [24.9, 60.2]},
     "properties": {"id": "R3", "state": "SENT", "limitation": "SWITCH_LOCKED",
                    "startDate": "2026-10-20T00:00:00Z", "endDate": None, "finished": True}},
]}

STATIONS = [
    {"stationName": "Kouvola asema", "latitude": 60.866, "longitude": 26.705},
    {"stationName": "Oulu asema", "latitude": 65.012, "longitude": 25.483},
    {"stationName": "Mikkeli asema", "latitude": 61.689, "longitude": 27.272},
    {"stationName": "Kerimäki", "latitude": 61.49, "longitude": 26.92},
]


def settings(**kw: Any) -> railworks.RailworksSettings:
    return railworks.RailworksSettings(mission_name="RailworksFin", **kw)


class ParseTest(unittest.TestCase):
    def test_trackwork_groups_locations(self) -> None:
        notices = railworks.parse_notices(TRACKWORKS, "trackwork")
        self.assertEqual([n.id for n in notices],
                         ["1.2.246.586.7.1.383787", "1.2.246.586.7.1.384342"])
        self.assertEqual(len(notices[0].geometries), 1)
        self.assertEqual(notices[0].point, (25.440306, 65.317779))

    def test_restriction_active(self) -> None:
        notices = railworks.parse_notices(RESTRICTIONS, "restriction")
        active = [n.id for n in notices if n.active(NOW)]
        self.assertEqual(active, ["R1"])  # R2 ended, R3 finished

    def test_bad_input(self) -> None:
        self.assertEqual(railworks.parse_notices({"message": "error"}, "trackwork"), [])
        self.assertEqual(railworks.parse_notices(None, "trackwork"), [])

    def test_nearest_station(self) -> None:
        st = railworks.parse_stations(STATIONS)
        self.assertEqual(railworks.nearest_station(60.87, 26.7, st), "Kouvola")
        self.assertEqual(st[0][2], "Kouvola")

    def test_settings(self) -> None:
        s = railworks.RailworksSettings.from_env({
            "COT_URL": "ssl://takserver:8090", "RAILWORKS_MISSION_GROUP": "VR, ALL-FEEDS",
            "RAILWORKS_INCLUDE": "restrictions,bogus"})
        self.assertEqual(s.api_host, "takserver")
        self.assertEqual(s.groups, ["VR", "ALL-FEEDS"])
        self.assertEqual(s.include, ("restrictions",))
        self.assertTrue(s.enabled)
        self.assertFalse(railworks.RailworksSettings.from_env({"RAILWORKS": "0"}).enabled)
        self.assertEqual(railworks.RailworksSettings.from_env(
            {"COT_URL": "udp://239.2.3.1:6969"}).api_host, "")
        self.assertEqual(railworks.RailworksSettings.from_env(
            {"COT_URL": "ssl://a:8090", "API_HOST": "b"}).api_host, "b")


class CotTest(unittest.TestCase):
    def build(self) -> dict[str, bytes]:
        notices = [n for n in railworks.parse_notices(RESTRICTIONS, "restriction")
                   + railworks.parse_notices(TRACKWORKS, "trackwork") if n.active(NOW)]
        return railworks.build_cot_files(notices, railworks.parse_stations(STATIONS),
                                         settings(), NOW)

    def test_items(self) -> None:
        files = self.build()
        self.assertEqual(sorted(files), [
            "RUMA-TR-R1", "RUMA-TR-R1.0",
            "RUMA-TW-1.2.246.586.7.1.383787", "RUMA-TW-1.2.246.586.7.1.383787.0",
            "RUMA-TW-1.2.246.586.7.1.384342", "RUMA-TW-1.2.246.586.7.1.384342.0",
            "RUMA-TW-1.2.246.586.7.1.384342.1"])

    def test_restriction_marker(self) -> None:
        ev = ET.fromstring(self.build()["RUMA-TR-R1"])
        self.assertEqual(ev.get("type"), "a-n-G")
        self.assertEqual(ev.get("stale"), "2026-10-26T16:00:00.000Z")  # end date
        self.assertEqual(ev.find("detail/contact").get("callsign"),  # type: ignore[union-attr]
                         "Rata suljettu: Kouvola")
        self.assertEqual(ev.find("detail/marti/dest").get("mission"),  # type: ignore[union-attr]
                         "RailworksFin")
        self.assertEqual(ev.find("detail/color").get("argb"), railworks.RED)  # type: ignore[union-attr]
        rem = ev.find("detail/remarks").text or ""  # type: ignore[union-attr]
        self.assertIn("Rata suljettu · Closed to traffic", rem)
        self.assertIn("Valid: 24.10. 09:00 – 26.10. 18:00", rem)  # summer time ends 25.10.
        self.assertIn("Source: Fintraffic / digitraffic.fi, CC BY 4.0", rem)
        self.assertIn("#rata #railway #rajoitus #restriction #suljettu", rem)

    def test_line_and_area(self) -> None:
        files = self.build()
        line = ET.fromstring(files["RUMA-TR-R1.0"])
        self.assertEqual(line.get("type"), "u-d-f")
        self.assertEqual(len(line.findall("detail/link")), 2)
        self.assertIsNone(line.find("detail/fillColor"))
        area = ET.fromstring(files["RUMA-TW-1.2.246.586.7.1.383787.0"])
        self.assertEqual(len(area.findall("detail/link")), 4)
        self.assertEqual(area.find("detail/strokeColor").get("value"),  # type: ignore[union-attr]
                         railworks.GREY)
        self.assertIsNotNone(area.find("detail/fillColor"))

    def test_thin(self) -> None:
        coords = [[i, i] for i in range(1000)]
        out = railworks._thin(coords, 250)  # pylint: disable=protected-access
        self.assertEqual(len(out), 250)
        self.assertEqual((out[0], out[-1]), (coords[0], coords[-1]))


class FakeMission:
    def __init__(self, uids: list[str], stored: str = "") -> None:
        self.uids = uids
        self.stored = stored
        self.created = 0
        self.removed: list[str] = []
        self.packages: list[dict[str, Any]] = []

    async def create_mission_subscription(self, name: str, uid: str) -> tuple[int, Any]:
        return 201, {"data": {"token": "tok"}}

    async def create_mission(self, *a: Any, **kw: Any) -> tuple[int, Any]:
        self.created += 1
        return 201, {"data": [{"token": "tok"}]}

    async def get_mission(self, name: str) -> tuple[int, Any]:
        return 200, {"data": [{"uids": [{"data": u} for u in self.uids]}]}

    async def get_mission_cot(self, name: str) -> tuple[int, Any]:
        return 200, self.stored

    async def remove_mission_content(self, name: str, uid: str, *a: Any) -> tuple[int, Any]:
        self.removed.append(uid)
        return 200, {}

    async def add_mission_package(self, *a: Any) -> tuple[int, Any]:
        return 200, {}


def make_sync(fake: FakeMission, packages: list[dict[str, Any]]) -> missions.MissionSync:
    server = mock.Mock(mission=fake)

    def build_package(**kw: Any) -> bytes:
        packages.append(kw)
        return b"zip"

    return missions.MissionSync(server, build_package, "takserver", "RailworksFin",
                                "vr-railworks", ["VR"])


class MissionSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(missions, "REMOVE_PAUSE", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_remove_and_upload_changed(self) -> None:
        files = CotTest().build()
        keep = "RUMA-TR-R1"
        stored = "<events>" + files[keep].decode() + "</events>"
        fake = FakeMission([keep, "RUMA-TR-OLD"], stored)
        packages: list[dict[str, Any]] = []
        uploaded = asyncio.run(make_sync(fake, packages).sync(files))
        self.assertTrue(uploaded)
        self.assertEqual(fake.removed, ["RUMA-TR-OLD"])
        self.assertNotIn(keep, packages[0]["cot_files"])
        self.assertEqual(len(packages[0]["cot_files"]), len(files) - 1)
        self.assertEqual(packages[0]["groups"], ["VR"])
        self.assertEqual(packages[0]["mission_server"], "takserver:8443:ssl")

    def test_no_changes_and_force(self) -> None:
        files = CotTest().build()
        stored = "<events>" + "".join(f.decode() for f in files.values()) + "</events>"
        fake = FakeMission(list(files), stored)
        packages: list[dict[str, Any]] = []
        sync = make_sync(fake, packages)
        self.assertFalse(asyncio.run(sync.sync(files)))
        self.assertTrue(asyncio.run(sync.sync(files, force=True)))
        self.assertEqual(len(packages[0]["cot_files"]), len(files))


class FetchTest(unittest.TestCase):
    def test_window_falls_back(self) -> None:
        rw = railworks.Railworks(settings(), mission=None)
        answers = [{"message": "too long"}, TRACKWORKS]
        calls: list[dict[str, str]] = []

        def fake_get(path: str, params: Any = None) -> Any:
            calls.append(params)
            return answers.pop(0)

        with mock.patch.object(rw, "_get", side_effect=fake_get):
            fc = rw._fetch_window(  # pylint: disable=protected-access
                "trackwork-notifications.geojson", "ACTIVE", NOW)
        self.assertEqual(fc, TRACKWORKS)
        self.assertEqual(calls[0]["start"], "2026-07-26T21:00:00Z")  # 90 days
        self.assertEqual(calls[1]["start"], "2026-09-24T21:00:00Z")  # 30 days
        self.assertEqual(calls[1]["state"], "ACTIVE")

    def test_fetch_filters(self) -> None:
        rw = railworks.Railworks(settings(), mission=None)

        def fake_get(path: str, params: Any = None) -> Any:
            if path == "metadata/stations":
                return STATIONS
            return RESTRICTIONS if path.startswith("trafficrestriction") else TRACKWORKS

        with mock.patch.object(rw, "_get", side_effect=fake_get):
            notices = rw.fetch(NOW)
        self.assertEqual(sorted(n.id for n in notices),
                         ["1.2.246.586.7.1.383787", "1.2.246.586.7.1.384342", "R1"])
        self.assertEqual(len(rw.stations), 4)


if __name__ == "__main__":
    unittest.main()
