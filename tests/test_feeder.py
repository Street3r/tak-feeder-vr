# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests. Run: python -m unittest discover -s tests"""

from __future__ import annotations

import time
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from tak_feeder_vr import model, naming
from tak_feeder_vr.config import Settings
from tak_feeder_vr.sources import digitraffic_handler, hsl_handler
from tak_feeder_vr.store import Stats, TrainStore, bearing_deg, distance_m

import fixtures as fx

NOW = datetime(2026, 10, 13, 9, 0, 5, tzinfo=timezone.utc)


def make_store(**kw: object) -> TrainStore:
    s = Settings(**kw)  # type: ignore[arg-type]
    store = TrainStore(s)
    store.set_metadata(model.parse_stations(fx.STATIONS), model.parse_operators(fx.OPERATORS))
    return store


def events(store: TrainStore, now: datetime = NOW) -> list[ET.Element]:
    return [ET.fromstring(e) for e in store.events(now, Stats())]


def remarks(ev: ET.Element) -> str:
    return ev.find("detail/remarks").text or ""  # type: ignore[union-attr]


class ParseTest(unittest.TestCase):
    def test_position(self) -> None:
        p = model.parse_position(fx.position())
        assert p is not None
        self.assertEqual(p.key, (fx.DATE, "59"))
        self.assertAlmostEqual(p.lat, 60.1987)
        self.assertAlmostEqual(p.lon, 24.9334)
        self.assertEqual(p.speed_kmh, 108)
        self.assertEqual(p.accuracy_m, 8)

    def test_position_string_number_missing_fields(self) -> None:
        p = model.parse_position(fx.position(number="59", accuracy=None, speed=None, date=None))
        assert p is not None
        self.assertEqual(p.key, ("", "59"))
        self.assertIsNone(p.accuracy_m)
        self.assertEqual(p.speed_kmh, 0.0)

    def test_bad_position(self) -> None:
        self.assertIsNone(model.parse_position({"trainNumber": 1}))
        self.assertIsNone(model.parse_position({**fx.position(), "timestamp": None}))

    def test_train(self) -> None:
        t = model.parse_train(fx.long_distance())
        assert t is not None
        self.assertEqual((t.operator, t.train_type, t.category), ("vr", "IC", "Long-distance"))
        self.assertEqual(len(t.rows), 4)
        self.assertEqual(t.rows[0].difference_min, 1)

    def test_stations_strip_suffix(self) -> None:
        self.assertEqual(model.parse_stations(fx.STATIONS)["HKI"], "Helsinki")

    def test_composition(self) -> None:
        c = model.parse_composition(fx.composition())
        assert c is not None
        sec = c.section_at(NOW)
        assert sec is not None
        self.assertEqual((sec.locomotives, sec.wagons, sec.length_m, sec.max_speed), (["Sr2"], 7, 189, 200))

    def test_hsl_uid_matches_tak_feeder_hsl(self) -> None:
        topic, payload = fx.hsl_vp()
        trip = model.parse_hsl_vp(topic, payload, 0.0)
        assert trip is not None
        # Same formula as tak-feeder-hsl: HSL-{mode}-{route}-{oday}-{start}-{dir}
        self.assertEqual(trip.uid, f"HSL-train-3001R-{fx.DATE}-11:52-1")
        self.assertEqual(trip.line, "R")


class NamingTest(unittest.TestCase):
    def test_long_distance(self) -> None:
        t = model.parse_train(fx.long_distance())
        key = (fx.DATE, "59")
        self.assertEqual(naming.callsign(key, t, {}), "VR IC 59")
        self.assertEqual(naming.hashtags(key, t, "vr"), "#vr #IC59 #kaukoliikenne #longdistance")

    def test_commuter(self) -> None:
        t = model.parse_train(fx.commuter())
        key = (fx.DATE, "9655")
        self.assertEqual(naming.callsign(key, t, {}), "VR R")
        self.assertEqual(naming.hashtags(key, t, "vr"), "#vr #R #lähiliikenne #commuter")

    def test_other_operator_and_override(self) -> None:
        t = model.parse_train(fx.cargo())
        key = (fx.DATE, "3021")
        self.assertEqual(naming.callsign(key, t, {}), "FENNIARAIL T 3021")
        self.assertEqual(naming.callsign(key, t, {"fenniarail": "FR"}), "FR T 3021")
        self.assertEqual(naming.hashtags(key, t, "vr"), "#vr #T3021 #tavaraliikenne #cargo")

    def test_other_categories(self) -> None:
        for cat, tags in (("Shunting", ["vaihtotyö", "shunting"]),
                          ("On-track machines", ["ratatyökone", "trackmachine"]),
                          ("Locomotive", ["veturi", "locomotive"]),
                          ("New Thing", ["newthing"])):
            t = model.parse_train({**fx.long_distance(), "trainCategory": cat})
            self.assertEqual(naming.category_tags(t), tags)

    def test_without_timetable(self) -> None:
        key = ("", "123")
        self.assertEqual(naming.callsign(key, None, {}), "TRAIN 123")
        self.assertEqual(naming.native_uid(key, "VR"), "VR-x-123")
        self.assertEqual(naming.hashtags(key, None, "vr"), "#vr #123 #tuntematon #unknown")


class GeoTest(unittest.TestCase):
    def test_bearing(self) -> None:
        self.assertAlmostEqual(bearing_deg(60.0, 25.0, 60.01, 25.0), 0.0, places=3)
        self.assertAlmostEqual(bearing_deg(60.0, 25.0, 60.0, 25.01), 90.0, delta=0.1)
        self.assertAlmostEqual(bearing_deg(60.01, 25.0, 60.0, 25.0), 180.0, places=3)

    def test_distance(self) -> None:
        self.assertAlmostEqual(distance_m(60.0, 25.0, 60.01, 25.0), 1112, delta=2)


class EventTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = make_store(hsl_mode="off")
        handle = digitraffic_handler(self.store)
        handle("trains/2026-10-13/59", fx.long_distance())
        handle("compositions/2026-10-13/59", fx.composition())
        handle("train-locations/2026-10-13/59", fx.position())

    def test_cot_fields(self) -> None:
        (ev,) = events(self.store)
        self.assertEqual(ev.get("uid"), f"VR-{fx.DATE}-59")
        self.assertEqual(ev.get("type"), "a-f-G-E-V-C-M")
        self.assertEqual(ev.find("detail/contact").get("callsign"), "VR IC 59")  # type: ignore[union-attr]
        # 108 km/h -> 30 m/s
        self.assertEqual(ev.find("detail/track").get("speed"), "30.00")  # type: ignore[union-attr]
        self.assertEqual(ev.find("point").get("ce"), "8.0")  # type: ignore[union-attr]
        stale = datetime.fromisoformat(ev.get("stale", "").replace("Z", "+00:00"))
        self.assertEqual(stale - NOW.replace(microsecond=0), timedelta(seconds=120))

    def test_remarks(self) -> None:
        (ev,) = events(self.store)
        text = remarks(ev)
        self.assertIn("VR IC 59 · Long-distance", text)
        self.assertIn("Helsinki → Oulu", text)
        self.assertIn("Next: Pasila 12:00 (+3 min)", text)  # local time, UTC+3
        self.assertIn("Rolling stock: Sr2, 7 cars, 189 m, max 200 km/h", text)
        self.assertIn("Operator: VR-Yhtymä Oyj", text)
        self.assertIn("GPS: 12:00:00 (±8 m)", text)
        self.assertIn("Source: Fintraffic / digitraffic.fi, CC BY 4.0", text)
        self.assertTrue(text.endswith("#vr #IC59 #kaukoliikenne #longdistance"))

    def test_utf8_without_declaration(self) -> None:
        raw = self.store.events(NOW, Stats())[0]
        self.assertTrue(raw.startswith(b"<event "))
        self.assertIn("· Long-distance".encode("utf-8"), raw)

    def test_resend_only_on_change_or_refresh(self) -> None:
        self.assertEqual(len(events(self.store)), 1)
        self.assertEqual(len(events(self.store, NOW + timedelta(seconds=5))), 0)
        self.assertEqual(len(events(self.store, NOW + timedelta(seconds=31))), 1)
        self.store.update_position(model.parse_position(fx.position(ts="2026-10-13T09:00:06.000Z", lat=60.2)))  # type: ignore[arg-type]
        self.assertEqual(len(events(self.store, NOW + timedelta(seconds=33))), 1)

    def test_old_position_dropped(self) -> None:
        self.assertEqual(events(self.store, NOW + timedelta(seconds=200)), [])

    def test_out_of_order_ignored(self) -> None:
        self.store.update_position(model.parse_position(fx.position(ts="2026-10-13T08:59:00.000Z", lat=61.0)))  # type: ignore[arg-type]
        (ev,) = events(self.store)
        self.assertEqual(ev.find("point").get("lat"), "60.198700")  # type: ignore[union-attr]

    def test_course_from_movement(self) -> None:
        events(self.store)
        north = fx.position(ts="2026-10-13T09:00:06.000Z", lat=60.1997)
        self.store.update_position(model.parse_position(north))  # type: ignore[arg-type]
        (ev,) = events(self.store, NOW + timedelta(seconds=6))
        self.assertEqual(ev.find("detail/track").get("course"), "0.0")  # type: ignore[union-attr]
        # A few metres of jitter at a stop must not change the course
        jitter = fx.position(ts="2026-10-13T09:00:12.000Z", lat=60.1997, lon=24.93345)
        self.store.update_position(model.parse_position(jitter))  # type: ignore[arg-type]
        (ev,) = events(self.store, NOW + timedelta(seconds=12))
        self.assertEqual(ev.find("detail/track").get("course"), "0.0")  # type: ignore[union-attr]

    def test_category_filter(self) -> None:
        store = make_store(hsl_mode="off", categories=["Commuter"])
        digitraffic_handler(store)("trains/x", fx.long_distance())
        digitraffic_handler(store)("train-locations/x", fx.position())
        self.assertEqual(events(store), [])

    def test_no_timetable_still_sent(self) -> None:
        store = make_store(hsl_mode="off")
        digitraffic_handler(store)("train-locations/x", fx.position(number=77777))
        (ev,) = events(store)
        self.assertEqual(ev.find("detail/contact").get("callsign"), "TRAIN 77777")  # type: ignore[union-attr]
        self.assertEqual(store.missing(time.time(), 10), [("train", (fx.DATE, "77777")), ("composition", (fx.DATE, "77777"))])
        self.assertEqual(store.missing(time.time(), 10), [])  # not asked again right away

    def test_prune(self) -> None:
        self.store.prune(NOW + timedelta(hours=2))
        self.assertEqual(self.store.counts()["tracked"], 0)
        self.assertEqual(self.store.counts()["trains"], 1)  # today's train kept
        self.store.prune(NOW + timedelta(days=3))
        self.assertEqual(self.store.counts()["trains"], 0)


class HslMatchTest(unittest.TestCase):
    def build(self, mode: str, **vp: object) -> TrainStore:
        store = make_store(hsl_mode=mode)
        dt = digitraffic_handler(store)
        dt("trains/x", fx.commuter())
        dt("train-locations/x", fx.position(number=9655))
        topic, payload = fx.hsl_vp(**vp)  # type: ignore[arg-type]
        hsl_handler(store)(topic, payload)
        return store

    def test_uid_mode_uses_hsl_uid(self) -> None:
        store = self.build("uid")
        (ev,) = events(store, _now_like_hsl(store))
        self.assertEqual(ev.get("uid"), f"HSL-train-3001R-{fx.DATE}-11:52-1")

    def test_skip_mode_sends_nothing(self) -> None:
        store = self.build("skip")
        self.assertEqual(events(store, _now_like_hsl(store)), [])

    def test_other_line_not_matched(self) -> None:
        store = self.build("uid", line="I", route="3001I")
        (ev,) = events(store, _now_like_hsl(store))
        self.assertEqual(ev.get("uid"), f"VR-{fx.DATE}-9655")

    def test_too_far_not_matched(self) -> None:
        store = self.build("uid", lat=60.3)
        (ev,) = events(store, _now_like_hsl(store))
        self.assertEqual(ev.get("uid"), f"VR-{fx.DATE}-9655")

    def test_one_to_one_and_heading(self) -> None:
        """Two R trains passing each other near one HSL vehicle heading north:
        only the northbound one may take its UID."""
        store = make_store(hsl_mode="uid")
        dt = digitraffic_handler(store)
        dt("trains/a", fx.commuter(9655))
        dt("trains/b", fx.commuter(9600))
        # 9655 northbound, 9600 southbound, both near the HSL position
        dt("train-locations/x", fx.position(number=9655, lat=60.1970, ts="2026-10-13T08:59:54.000Z", speed=60))
        dt("train-locations/x", fx.position(number=9655, lat=60.1985, ts="2026-10-13T09:00:00.000Z", speed=60))
        dt("train-locations/x", fx.position(number=9600, lat=60.2010, ts="2026-10-13T08:59:54.000Z", speed=60))
        dt("train-locations/x", fx.position(number=9600, lat=60.1993, ts="2026-10-13T09:00:00.000Z", speed=60))
        topic, payload = fx.hsl_vp(lat=60.1992, hdg=5)
        hsl_handler(store)(topic, payload)
        uids = {ev.find("detail/remarks").text.split("Train ")[1].split(",")[0]: ev.get("uid")  # type: ignore[union-attr]
                for ev in events(store, _now_like_hsl(store))}
        self.assertEqual(uids["9655"], f"HSL-train-3001R-{fx.DATE}-11:52-1")
        self.assertEqual(uids["9600"], f"VR-{fx.DATE}-9600")

    def test_match_is_sticky(self) -> None:
        store = self.build("uid")
        now = _now_like_hsl(store)
        events(store, now)
        store.hsl_trips.clear()  # HSL message missing for a moment
        tr = next(iter(store.tracked.values()))
        newer = (tr.pos.timestamp + timedelta(seconds=6)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        store.update_position(model.parse_position(fx.position(number=9655, ts=newer)))  # type: ignore[arg-type]
        (ev,) = events(store, now + timedelta(seconds=6))
        self.assertTrue(ev.get("uid", "").startswith("HSL-"))


class MqttSourceTest(unittest.TestCase):
    def test_wss_setup_and_dispatch(self) -> None:
        import gzip
        import json
        from types import SimpleNamespace

        from tak_feeder_vr.sources import MqttSource

        got: list[tuple[str, object]] = []
        src = MqttSource("t", "wss://rata.digitraffic.fi:443/mqtt", ("a/#",),
                         lambda t, d: got.append((t, d)), headers={"Digitraffic-User": "x"})
        self.assertEqual((src.host, src.port), ("rata.digitraffic.fi", 443))
        plain = json.dumps({"a": 1}).encode()
        src._on_message(src.client, None, SimpleNamespace(topic="a/1", payload=plain))  # type: ignore[arg-type]
        src._on_message(src.client, None, SimpleNamespace(topic="a/2", payload=gzip.compress(plain)))  # type: ignore[arg-type]
        src._on_message(src.client, None, SimpleNamespace(topic="a/3", payload=b"not json"))  # type: ignore[arg-type]
        self.assertEqual(got, [("a/1", {"a": 1}), ("a/2", {"a": 1})])

    def test_plain_mqtt_url(self) -> None:
        from tak_feeder_vr.sources import MqttSource

        src = MqttSource("h", "mqtt://mqtt.hsl.fi:1883", ("x",), lambda t, d: None)
        self.assertEqual((src.host, src.port), ("mqtt.hsl.fi", 1883))


class ConfigTest(unittest.TestCase):
    def test_env(self) -> None:
        s = Settings.from_env({
            "COT_URL": "ssl://takserver:8090", "CLIENT_CERT": "/data/cert.pem",
            "PYTAK_TLS_CLIENT_KEY": "/data/key.pem", "PYTAK_TLS_DONT_VERIFY": "1",
            "OPERATOR_CALLSIGNS": "fenniarail=FR, operail = OP", "CATEGORIES": "Commuter,Long-distance",
            "HSL_MODE": "SKIP",
        })
        self.assertEqual(s.tls_client_cert, "/data/cert.pem")
        self.assertEqual(s.operator_callsigns, {"fenniarail": "FR", "operail": "OP"})
        self.assertEqual(s.categories, ["Commuter", "Long-distance"])
        self.assertEqual(s.hsl_mode, "skip")
        cfg = s.pytak_config()
        self.assertEqual(cfg["PYTAK_TLS_DONT_VERIFY"], "1")
        self.assertNotIn("DEBUG", cfg)  # pytak treats "0" as true

    def test_bad_hsl_mode(self) -> None:
        with self.assertRaises(ValueError):
            Settings.from_env({"HSL_MODE": "maybe"})


def _now_like_hsl(store: TrainStore) -> datetime:
    """HSL trips are stamped with time.time(); make the GPS fix fresh relative
    to that by moving the stored position timestamps to 'now'."""
    real_now = datetime.now(timezone.utc)
    for tr in store.tracked.values():
        shift = real_now - NOW
        tr.pos.timestamp = tr.pos.timestamp + shift
    return real_now + timedelta(seconds=5)


if __name__ == "__main__":
    unittest.main()
