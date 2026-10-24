# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Street3r
#
# Adapted from the mission sync of tak-feeder-traffic-fin-roads (Copyright
# Stefan Gofferje and Street3r, GPL-3.0-or-later).
"""Keeps one TAK mission (Data Sync) in sync with a set of CoT items."""

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from typing import Any, Callable, Optional

LOG = logging.getLogger("tak_feeder_vr.missions")

# ATAK Data Sync needs time to process a package before the next one.
UPLOAD_PAUSE = 30.0
REMOVE_PAUSE = 3.0


def _points(values: list[tuple[str, str]]) -> tuple[tuple[float, float], ...]:
    out = []
    for lat, lon in values:
        try:
            out.append((round(float(lat), 5), round(float(lon), 5)))
        except ValueError:
            continue
    return tuple(out)


def cot_signature(event: ET.Element) -> tuple[Any, ...]:
    """What ATAK shows of a CoT item: type, position, label, remarks, icon,
    colours and line points. Times are left out, they change every round."""
    point = event.find("point")
    detail = event.find("detail")

    def attr(path: str, name: str) -> str:
        el = detail.find(path) if detail is not None else None
        return el.get(name, "") if el is not None else ""

    remarks = detail.find("remarks") if detail is not None else None
    links = detail.findall("link") if detail is not None else []
    return (
        event.get("type", ""),
        _points([(point.get("lat", ""), point.get("lon", ""))]) if point is not None else (),
        attr("contact", "callsign"),
        (remarks.text or "").strip() if remarks is not None else "",
        attr("usericon", "iconsetpath"),
        attr("color", "argb"),
        attr("strokeColor", "value"),
        attr("fillColor", "value"),
        _points([tuple(l.get("point", ",").split(",", 1)) for l in links]),  # type: ignore[misc]
    )


def parse_signatures(xml: Any) -> Optional[dict[str, tuple[Any, ...]]]:
    """uid -> signature of the CoT items TAK stores for a mission, None if the
    response cannot be read."""
    if isinstance(xml, bytes):
        xml = xml.decode("utf-8", "replace")
    if not isinstance(xml, str) or not xml.strip():
        return None
    try:
        root = ET.fromstring(xml.strip())
    except ET.ParseError:
        return None
    return {e.get("uid", ""): cot_signature(e) for e in root.iter("event")}


class MissionSync:
    """Creates the mission when missing, removes items that are gone and
    uploads new or changed ones as a mission package."""

    def __init__(self, server: Any, build_package: Callable[..., bytes], api_host: str,
                 mission_name: str, creator_uid: str, groups: list[str]) -> None:
        self.server = server
        self.build_package = build_package
        self.api_host = api_host
        self.mission_name = mission_name
        self.creator_uid = creator_uid
        self.groups = groups
        self.token = ""

    async def _create(self) -> None:
        group: Any = self.groups or ""  # a list becomes repeated group=
        status, result = await self.server.mission.create_mission(
            self.mission_name, self.creator_uid, group=group,
            default_role="MISSION_READONLY_SUBSCRIBER", classification="unclassified")
        if status >= 400:
            raise RuntimeError(f"cannot create mission {self.mission_name}: HTTP {status}")
        try:
            self.token = result["data"][0]["token"]
        except (KeyError, IndexError, TypeError):
            self.token = result.get("data", {}).get("token", "")
        LOG.info("Mission %s created (groups: %s)", self.mission_name,
                 ",".join(self.groups) or "default")

    async def _subscribe(self) -> None:
        if self.token:
            return
        status, result = await self.server.mission.create_mission_subscription(
            self.mission_name, self.creator_uid)
        if status == 201:
            self.token = result["data"]["token"]
        elif status == 404:
            await self._create()
        else:
            raise RuntimeError(f"subscription to {self.mission_name} failed: HTTP {status}")

    async def _changed(self, files: dict[str, bytes], uids_differ: bool) -> dict[str, bytes]:
        """Items that are new or differ from what TAK stores. If the stored
        items cannot be read, everything is sent when the set of items changed."""
        if not files:
            return {}
        stored: Optional[dict[str, tuple[Any, ...]]] = None
        try:
            status, body = await self.server.mission.get_mission_cot(self.mission_name)
            if status == 200:
                stored = parse_signatures(body)
        except Exception as exc:  # noqa: BLE001  pylint: disable=broad-exception-caught
            LOG.debug("%s: cannot read stored items: %s", self.mission_name, exc)
        if stored is None:
            return dict(files) if uids_differ else {}
        changed = {}
        for uid, cot in files.items():
            try:
                sig: Optional[tuple[Any, ...]] = cot_signature(ET.fromstring(cot))
            except ET.ParseError:
                sig = None
            if stored.get(uid) != sig:
                changed[uid] = cot
        return changed

    async def sync(self, files: dict[str, bytes], force: bool = False) -> bool:
        """One round. force = upload every item (renews stale times).
        True if a package was uploaded."""
        await self._subscribe()
        status, mission = await self.server.mission.get_mission(self.mission_name)
        if status == 404:
            self.token = ""
            await self._create()
            status, mission = await self.server.mission.get_mission(self.mission_name)
        if status != 200:
            raise RuntimeError(f"cannot get mission {self.mission_name}: HTTP {status}")
        data = mission["data"][0]
        wanted = set(files)
        present = {u.get("data") for u in data.get("uids", []) if isinstance(u, dict)}
        for uid in sorted(present - wanted):
            LOG.info("%s: removing %s", self.mission_name, uid)
            s, r = await self.server.mission.remove_mission_content(
                self.mission_name, uid, self.creator_uid, self.token)
            if s != 200:
                LOG.warning("Could not remove %s: HTTP %s %s", uid, s, r)
            await asyncio.sleep(REMOVE_PAUSE)
        upload = dict(files) if force else await self._changed(files, wanted != present)
        if not upload:
            LOG.info("%s: %d items, no changes", self.mission_name, len(wanted))
            return False
        LOG.info("%s: uploading %d of %d items", self.mission_name, len(upload), len(files))
        package = self.build_package(
            name=self.mission_name, mission_name=self.mission_name,
            mission_server=f"{self.api_host}:8443:ssl", creator_uid=self.creator_uid,
            cot_files=upload, groups=self.groups or None)
        s, r = await self.server.mission.add_mission_package(
            self.mission_name, self.creator_uid, self.token, package)
        if s != 200:
            raise RuntimeError(f"package upload to {self.mission_name} failed: HTTP {s} {r}")
        return True
