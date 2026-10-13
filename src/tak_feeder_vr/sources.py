# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Street3r
#
# MQTT handling follows sgofferj's tak-feeder-hsl / trakbridge-plugin-hsl,
# Copyright Stefan Gofferje, GPL-3.0-or-later.
"""Digitraffic REST + MQTT and HSL HFP MQTT clients."""

from __future__ import annotations

import gzip
import json
import logging
import time
import urllib.request
import uuid
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import paho.mqtt.client as mqtt

from . import __version__
from .model import parse_composition, parse_hsl_vp, parse_position, parse_train
from .store import TrainStore

LOG = logging.getLogger("tak_feeder_vr")

DIGITRAFFIC_TOPICS = ("train-locations/#", "trains/#", "compositions/#")
HSL_TRAIN_TOPIC = "/hfp/v2/journey/ongoing/vp/train/#"


def user_agent_headers(user: str) -> dict[str, str]:
    """Headers Digitraffic asks every client to send. No personal data here."""
    return {
        "Digitraffic-User": user,
        "User-Agent": f"tak-feeder-vr/{__version__}",
        "Accept-Encoding": "gzip",
    }


def http_get_json(url: str, user: str, timeout: float = 60.0) -> Any:
    """GET a Digitraffic REST resource (gzip is mandatory there)."""
    req = urllib.request.Request(url, headers=user_agent_headers(user))
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        body = resp.read()
        if resp.headers.get("Content-Encoding", "").lower() == "gzip":
            body = gzip.decompress(body)
    return json.loads(body.decode("utf-8"))


def _new_client(client_id: str, transport: str) -> mqtt.Client:
    return mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
        transport=transport,  # type: ignore[arg-type]
    )


class MqttSource:
    """One MQTT connection with automatic reconnect and back-off."""

    def __init__(
        self,
        name: str,
        url: str,
        topics: tuple[str, ...],
        on_json: Callable[[str, Any], None],
        headers: Optional[dict[str, str]] = None,
    ) -> None:
        self.name = name
        self.topics = topics
        self.on_json = on_json
        self.messages = 0
        self.connected = False
        u = urlparse(url)
        websocket = u.scheme in ("ws", "wss")
        secure = u.scheme in ("wss", "mqtts", "ssl")
        default_port = {"ws": 80, "wss": 443, "mqtts": 8883, "ssl": 8883}.get(u.scheme, 1883)
        self.host = u.hostname or ""
        self.port = u.port or default_port
        self.client = _new_client(
            f"tak-feeder-vr-{uuid.uuid4().hex[:12]}", "websockets" if websocket else "tcp"
        )
        if websocket:
            self.client.ws_set_options(path=u.path or "/mqtt", headers=headers)
        if secure:
            self.client.tls_set()
        # Digitraffic limits new MQTT connections per IP: never hammer it.
        self.client.reconnect_delay_set(min_delay=10, max_delay=300)
        self.client.on_connect = self._on_connect
        self.client.on_connect_fail = self._on_connect_fail
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    def start(self) -> None:
        """Connect in the background; paho keeps reconnecting."""
        LOG.info("%s: connecting to %s:%s", self.name, self.host, self.port)
        self.client.connect_async(self.host, self.port, keepalive=60)
        self.client.loop_start()

    def stop(self) -> None:
        """Disconnect."""
        self.client.disconnect()
        self.client.loop_stop()

    def _on_connect(self, client: mqtt.Client, _u: Any, _f: Any, reason: Any, _p: Any = None) -> None:
        if getattr(reason, "is_failure", False):
            LOG.error("%s: connection refused: %s", self.name, reason)
            return
        self.connected = True
        LOG.info("%s: connected, subscribing %s", self.name, ", ".join(self.topics))
        for topic in self.topics:
            client.subscribe(topic, qos=0)

    def _on_connect_fail(self, _c: mqtt.Client, _u: Any) -> None:
        LOG.warning("%s: cannot connect to %s:%s, retrying", self.name, self.host, self.port)

    def _on_disconnect(self, _c: mqtt.Client, _u: Any, _f: Any, reason: Any, _p: Any = None) -> None:
        self.connected = False
        LOG.warning("%s: disconnected (%s), reconnecting", self.name, reason)

    def _on_message(self, _c: mqtt.Client, _u: Any, msg: mqtt.MQTTMessage) -> None:
        self.messages += 1
        payload = msg.payload
        try:
            if payload[:2] == b"\x1f\x8b":  # some Digitraffic feeds are gzipped
                payload = gzip.decompress(payload)
            data = json.loads(payload.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            LOG.debug("%s: bad payload on %s", self.name, msg.topic)
            return
        try:
            self.on_json(msg.topic, data)
        except Exception:  # noqa: BLE001 - a bad message must not stop the client
            LOG.exception("%s: error handling %s", self.name, msg.topic)


def digitraffic_handler(store: TrainStore) -> Callable[[str, Any], None]:
    """Dispatch Digitraffic MQTT messages into the store."""

    def handle(topic: str, data: Any) -> None:
        if not isinstance(data, dict):
            return
        if topic.startswith("train-locations/"):
            pos = parse_position(data)
            if pos:
                store.update_position(pos)
        elif topic.startswith("trains/"):
            info = parse_train(data)
            if info:
                store.update_train(info)
        elif topic.startswith("compositions/"):
            comp = parse_composition(data)
            if comp:
                store.update_composition(comp)

    return handle


def hsl_handler(store: TrainStore) -> Callable[[str, Any], None]:
    """Store train trips from HSL's HFP feed."""

    def handle(topic: str, data: Any) -> None:
        trip = parse_hsl_vp(topic, data, time.time())
        if trip:
            store.update_hsl(trip)

    return handle
