# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Street3r
#
# Worker structure follows sgofferj's tak-feeder-hsl, Copyright Stefan
# Gofferje, GPL-3.0-or-later.
"""pytak workers: the rail feeder and an RX queue drain."""

from __future__ import annotations

import asyncio
import logging
import time
import urllib.error
from typing import Any, Optional

import pytak

from .common import Health, env_float
from .config import Settings
from .model import parse_composition, parse_operators, parse_stations, parse_train
from .sources import (
    DIGITRAFFIC_TOPICS,
    HSL_TRAIN_TOPIC,
    MqttSource,
    digitraffic_handler,
    http_get_json,
    hsl_handler,
    user_agent_headers,
)
from .store import Stats, TrainStore, utcnow

LOG = logging.getLogger("tak_feeder_vr")

METADATA_INTERVAL = 24 * 3600
PRUNE_INTERVAL = 3600
STATS_INTERVAL = 300
MISSING_INTERVAL = 30
MISSING_PER_ROUND = 10  # keeps REST use far below the per-IP limit


class RailWorker(pytak.QueueWorker):  # type: ignore[misc]
    """Collects Digitraffic data and puts CoT events on the TX queue."""

    def __init__(self, queue: asyncio.Queue[bytes], config: Any, settings: Settings) -> None:
        super().__init__(queue, config)
        self.s = settings
        self.store = TrainStore(settings)
        self.stats = Stats()
        self.sources: list[MqttSource] = []
        self._bootstrapped = False
        self._started = False
        self._metadata_at = 0.0
        self._seen_messages = 0
        # Unhealthy when Digitraffic MQTT has been silent this long.
        self.health = Health(max_silence=env_float("HEALTH_MAX_SILENCE", 600.0))

    async def handle_data(self, data: bytes) -> None:
        """Unused: data is produced in run()."""

    # ------------------------------------------------------------------ REST
    async def _get(self, path: str) -> Optional[Any]:
        url = f"{self.s.api_url}/{path.lstrip('/')}"
        try:
            return await asyncio.to_thread(http_get_json, url, self.s.digitraffic_user)
        except urllib.error.HTTPError as exc:
            # 404 = no data for this train (e.g. no composition); not an error
            level = logging.DEBUG if exc.code == 404 else logging.WARNING
            LOG.log(level, "GET %s: HTTP %s", url, exc.code)
            return None
        except Exception as exc:  # noqa: BLE001
            LOG.warning("GET %s failed: %s", url, exc)
            return None

    async def _refresh_metadata(self) -> None:
        stations = parse_stations(await self._get("metadata/stations"))
        operators = parse_operators(await self._get("metadata/operators"))
        self.store.set_metadata(stations, operators)
        if stations and operators:
            self._metadata_at = time.time()
        LOG.info("Metadata: %d stations, %d operators", len(stations), len(operators))

    async def _bootstrap(self) -> None:
        """Load the timetables of trains running now (version 0 = everything),
        so they get names before their next MQTT update."""
        trains = await self._get("live-trains?version=0")
        if isinstance(trains, list):
            for item in trains:
                info = parse_train(item)
                if info:
                    self.store.update_train(info)
            self._bootstrapped = True
        LOG.info(
            "Bootstrap %s: %s", "done" if self._bootstrapped else "failed", self.store.counts()
        )

    async def _fetch_missing(self) -> None:
        """Timetables and compositions MQTT has not delivered for tracked trains."""
        for kind, (date, number) in self.store.missing(time.time(), MISSING_PER_ROUND):
            if kind == "train":
                data = await self._get(f"trains/{date}/{number}")
                for item in data if isinstance(data, list) else []:
                    info = parse_train(item)
                    if info:
                        self.store.update_train(info)
            else:
                data = await self._get(f"compositions/{date}/{number}")
                for item in data if isinstance(data, list) else [data]:
                    comp = parse_composition(item) if isinstance(item, dict) else None
                    if comp:
                        self.store.update_composition(comp)

    # ------------------------------------------------------------------ loop
    def _start_sources(self) -> None:
        self.sources.append(
            MqttSource(
                "digitraffic",
                self.s.mqtt_url,
                DIGITRAFFIC_TOPICS,
                digitraffic_handler(self.store),
                headers=user_agent_headers(self.s.digitraffic_user),
            )
        )
        if self.s.hsl_mode != "off":
            self.sources.append(
                MqttSource(
                    "hsl",
                    f"mqtt://{self.s.hsl_broker}:{self.s.hsl_port}",
                    (HSL_TRAIN_TOPIC,),
                    hsl_handler(self.store),
                )
            )
        for src in self.sources:
            src.start()

    def _log_stats(self) -> None:
        msgs = ", ".join(f"{s.name} {s.messages} msgs ({'up' if s.connected else 'down'})" for s in self.sources)
        st = self.stats
        LOG.info(
            "Sent %d CoT (HSL UID %d, skipped as HSL %d, no timetable %d, filtered %d) | %s | %s",
            st.sent, st.hsl_uid, st.hsl_skipped, st.no_timetable, st.filtered, self.store.counts(), msgs,
        )
        self.stats = Stats()

    def _check_health(self) -> None:
        messages = self.sources[0].messages if self.sources else 0
        if messages != self._seen_messages:
            self._seen_messages = messages
            self.health.ok()
        self.health.check_silence()

    def close(self) -> None:
        """Stop the MQTT clients."""
        for src in self.sources:
            src.stop()

    async def run(self, number_of_iterations: Optional[int] = None) -> None:
        """Main loop. The data sources are started once and keep running when
        pytak reconnects to TAK, so the train store survives TAK outages."""
        if not self._started:
            self._started = True
            LOG.info(
                "Starting tak-feeder-vr: COT_URL=%s, HSL_MODE=%s, Digitraffic-User=%s",
                self.s.cot_url, self.s.hsl_mode, self.s.digitraffic_user,
            )
            self._start_sources()
            await self._refresh_metadata()
            await self._bootstrap()

        last_prune = last_stats = last_missing = last_boot = time.time()
        iterations = 0
        while number_of_iterations is None or iterations < number_of_iterations:
            iterations += 1
            for event in self.store.events(utcnow(), self.stats):
                await self.put_queue(event)
                if self.s.debug:
                    LOG.debug("%s", event.decode("utf-8"))

            now = time.time()
            if now - last_missing >= MISSING_INTERVAL:
                last_missing = now
                await self._fetch_missing()
            if not self._bootstrapped and now - last_boot >= 300:
                last_boot = now
                await self._bootstrap()
            if now - self._metadata_at >= METADATA_INTERVAL and now - last_boot >= 300:
                last_boot = now
                await self._refresh_metadata()
            if now - last_prune >= PRUNE_INTERVAL:
                last_prune = now
                self.store.prune(utcnow())
            self._check_health()
            if now - last_stats >= STATS_INTERVAL:
                last_stats = now
                self._log_stats()
            await asyncio.sleep(self.s.update_interval)


class RxDrain(pytak.QueueWorker):  # type: ignore[misc]
    """Empties the RX queue; the feeder does not use incoming CoT."""

    async def handle_data(self, data: bytes) -> None:
        """Ignore."""

    async def run(self, number_of_iterations: Optional[int] = None) -> None:
        """Drain forever."""
        iterations = 0
        while number_of_iterations is None or iterations < number_of_iterations:
            iterations += 1
            await self.queue.get()
