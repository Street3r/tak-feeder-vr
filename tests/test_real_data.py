# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests on an excerpt of real Digitraffic responses
(tests/real_sample_2026-10-13.json)."""

from __future__ import annotations

import json
import time
import unittest
import xml.etree.ElementTree as ET
from datetime import timedelta
from pathlib import Path

from tak_feeder_vr import model
from tak_feeder_vr.config import Settings
from tak_feeder_vr.store import Stats, TrainStore

DATA = json.loads((Path(__file__).parent / "real_sample_2026-10-13.json").read_text("utf-8"))


def load_store() -> TrainStore:
    store = TrainStore(Settings(hsl_mode="off"))
    store.set_metadata(model.parse_stations(DATA["stations"]), model.parse_operators(DATA["operators"]))
    for item in DATA["trains"]:
        info = model.parse_train(item)
        assert info is not None
        store.update_train(info)
    for item in DATA["compositions"]:
        comp = model.parse_composition(item)
        assert comp is not None
        store.update_composition(comp)
    for item in DATA["locations"]:
        pos = model.parse_position(item)
        assert pos is not None
        store.update_position(pos)
    return store


def events() -> dict[str, ET.Element]:
    store = load_store()
    latest = max(t.pos.timestamp for t in store.tracked.values())
    evs = [ET.fromstring(e) for e in store.events(latest + timedelta(seconds=5), Stats())]
    return {e.find("detail/contact").get("callsign"): e for e in evs}  # type: ignore[union-attr]


class RealDataTest(unittest.TestCase):
    def test_all_parse(self) -> None:
        store = load_store()
        self.assertEqual(len(store.tracked), 5)
        self.assertTrue(all(k in store.trains for k in store.tracked))

    def test_accuracy_zero_is_unknown(self) -> None:
        # Digitraffic sends accuracy 0 for every train
        self.assertTrue(all(x["accuracy"] == 0 for x in DATA["locations"]))
        pos = model.parse_position(DATA["locations"][0])
        assert pos is not None
        self.assertIsNone(pos.accuracy_m)
        for ev in events().values():
            self.assertEqual(ev.find("point").get("ce"), "9999999.0")  # type: ignore[union-attr]
            self.assertNotIn("±", ev.find("detail/remarks").text or "")  # type: ignore[union-attr]

    def test_not_gps(self) -> None:
        self.assertIsNone(model.parse_position({**DATA["locations"][0], "isGpsLocation": False}))

    def test_callsigns(self) -> None:
        self.assertEqual(set(events()), {"VR IC 12", "HSL A", "VR SAA 60012", "VR T 2228", "VR HV 10149"})

    def test_long_distance_remarks(self) -> None:
        text = events()["VR IC 12"].find("detail/remarks").text or ""  # type: ignore[union-attr]
        self.assertIn("Joensuu → Helsinki", text)
        self.assertIn("Rolling stock: Sr2, 6 cars, 177 m, max 200 km/h", text)
        self.assertTrue(text.endswith("#vr #IC12 #kaukoliikenne #longdistance #vryhtymä"))

    def test_commuter_remarks(self) -> None:
        text = events()["HSL A"].find("detail/remarks").text or ""  # type: ignore[union-attr]
        self.assertIn("Next: Huopalahti 23:33", text)
        self.assertIn("Sm5, 1 car, 75 m", text)
        self.assertIn("Authority: HSL", text)

    def test_shunting_without_actual_times(self) -> None:
        # Shunting trains get no actual times: a stop long past is not "next"
        text = events()["VR SAA 60012"].find("detail/remarks").text or ""  # type: ignore[union-attr]
        self.assertNotIn("Next:", text)

    def test_empty_stock_train(self) -> None:
        # Line V / type HV: stock moved between depot and station, no passengers
        ev = events()["VR HV 10149"]
        text = ev.find("detail/remarks").text or ""  # type: ignore[union-attr]
        self.assertTrue(text.startswith("VR HV 10149 · Empty stock"))
        self.assertNotIn("Authority:", text)
        self.assertTrue(text.endswith("#vr #HV10149 #tyhjävaunujuna #emptystock #vryhtymä"))

    def test_empty_stock_category_filter(self) -> None:
        store = load_store()
        store.s.categories = ["Commuter", "Long-distance"]
        latest = max(t.pos.timestamp for t in store.tracked.values())
        calls = {ET.fromstring(e).find("detail/contact").get("callsign")  # type: ignore[union-attr]
                 for e in store.events(latest + timedelta(seconds=5), Stats())}
        self.assertEqual(calls, {"VR IC 12", "HSL A"})

    def test_cargo_has_no_composition_lookup(self) -> None:
        store = load_store()
        kinds = store.missing(time.time(), 50)
        # Only passenger trains have compositions in Digitraffic
        self.assertEqual(kinds, [])


if __name__ == "__main__":
    unittest.main()
