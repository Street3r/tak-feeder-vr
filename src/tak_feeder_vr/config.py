# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Street3r
"""Settings read from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping, Optional

HSL_MODES = ("uid", "skip", "off")

# Commuter lines named after the transit authority that runs them, matching
# tak-feeder-hsl ("HSL I") and tak-feeder-nysse ("NYSSE M"). Lines not listed
# (R, Z, D, T, G, O, H ...) are named after the operator ("VR R").
DEFAULT_COMMUTER_BRANDS = "A=HSL,E=HSL,I=HSL,K=HSL,L=HSL,P=HSL,U=HSL,X=HSL,Y=HSL,M=NYSSE"


def _bool(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _map(value: Optional[str], upper_keys: bool = False) -> dict[str, str]:
    """Parse "a=X,b=Y" into {"a": "X", "b": "Y"} (keys lower- or upper-cased)."""
    result: dict[str, str] = {}
    for part in (value or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip() and v.strip():
                key = k.strip().upper() if upper_keys else k.strip().lower()
                result[key] = v.strip()
    return result


def _list(value: Optional[str]) -> list[str]:
    return [p.strip() for p in (value or "").split(",") if p.strip()]


@dataclass
class Settings:
    """All feeder settings."""

    # TAK connection (passed to pytak)
    cot_url: str = "udp://239.2.3.1:6969"
    tls_client_cert: str = ""
    tls_client_key: str = ""
    tls_client_cafile: str = ""
    tls_dont_verify: bool = False

    # Digitraffic
    digitraffic_user: str = "tak-feeder-vr"
    mqtt_url: str = "wss://rata.digitraffic.fi:443/mqtt"
    api_url: str = "https://rata.digitraffic.fi/api/v1"

    # Output
    update_interval: float = 2.0
    refresh_interval: float = 30.0
    stale_seconds: int = 120
    max_position_age: int = 180
    cot_type: str = "a-f-G-E-V-C-M"  # same as HSL, so a shared UID keeps one look
    uid_prefix: str = "VR"
    feed_tag: str = "vr"
    source_line: str = "Source: Fintraffic / digitraffic.fi, CC BY 4.0"
    operator_callsigns: dict[str, str] = field(default_factory=dict)
    commuter_brands: dict[str, str] = field(
        default_factory=lambda: _map(DEFAULT_COMMUTER_BRANDS, upper_keys=True)
    )
    categories: list[str] = field(default_factory=list)  # empty = all
    timezone: str = "Europe/Helsinki"

    # Matching with HSL's own feed (tak-feeder-hsl / trakbridge-plugin-hsl)
    hsl_mode: str = "off"
    hsl_broker: str = "mqtt.hsl.fi"
    hsl_port: int = 1883
    hsl_match_distance: float = 1500.0
    hsl_max_age: float = 60.0

    debug: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Settings":
        """Build settings from the environment."""
        d = cls()

        def get(name: str, default: str) -> str:
            value = env.get(name)
            return value if value not in (None, "") else default

        cert = get("PYTAK_TLS_CLIENT_CERT", env.get("CLIENT_CERT", ""))
        key = get("PYTAK_TLS_CLIENT_KEY", env.get("CLIENT_KEY", ""))
        hsl_mode = get("HSL_MODE", d.hsl_mode).strip().lower()
        if hsl_mode not in HSL_MODES:
            raise ValueError(f"HSL_MODE must be one of {', '.join(HSL_MODES)}")
        return cls(
            cot_url=get("COT_URL", d.cot_url),
            tls_client_cert=os.path.abspath(cert) if cert else "",
            tls_client_key=os.path.abspath(key) if key else "",
            tls_client_cafile=get("PYTAK_TLS_CLIENT_CAFILE", ""),
            tls_dont_verify=_bool(env.get("PYTAK_TLS_DONT_VERIFY")),
            digitraffic_user=get("DIGITRAFFIC_USER", d.digitraffic_user),
            mqtt_url=get("DIGITRAFFIC_MQTT_URL", d.mqtt_url),
            api_url=get("DIGITRAFFIC_API_URL", d.api_url).rstrip("/"),
            update_interval=float(get("UPDATE_INTERVAL", str(d.update_interval))),
            refresh_interval=float(get("REFRESH_INTERVAL", str(d.refresh_interval))),
            stale_seconds=int(get("STALE_SECONDS", str(d.stale_seconds))),
            max_position_age=int(get("MAX_POSITION_AGE", str(d.max_position_age))),
            cot_type=get("COT_TYPE", d.cot_type),
            uid_prefix=get("UID_PREFIX", d.uid_prefix),
            feed_tag=get("FEED_TAG", d.feed_tag),
            source_line=get("SOURCE_LINE", d.source_line),
            operator_callsigns=_map(env.get("OPERATOR_CALLSIGNS")),
            # Set but empty = no brands, every commuter train as "VR <line>"
            commuter_brands=_map(
                env["COMMUTER_BRANDS"] if "COMMUTER_BRANDS" in env else DEFAULT_COMMUTER_BRANDS,
                upper_keys=True,
            ),
            categories=_list(env.get("CATEGORIES")),
            timezone=get("TIMEZONE", d.timezone),
            hsl_mode=hsl_mode,
            hsl_broker=get("HSL_MQTT_BROKER", d.hsl_broker),
            hsl_port=int(get("HSL_MQTT_PORT", str(d.hsl_port))),
            hsl_match_distance=float(
                get("HSL_MATCH_DISTANCE", str(d.hsl_match_distance))
            ),
            hsl_max_age=float(get("HSL_MAX_AGE", str(d.hsl_max_age))),
            debug=_bool(env.get("DEBUG")),
        )

    def pytak_config(self) -> dict[str, str]:
        """Options for pytak.CLITool. Keys are only set when used, because pytak
        treats any non-empty string (including "0") as true."""
        cfg = {"COT_URL": self.cot_url}
        if self.tls_client_cert:
            cfg["PYTAK_TLS_CLIENT_CERT"] = self.tls_client_cert
        if self.tls_client_key:
            cfg["PYTAK_TLS_CLIENT_KEY"] = self.tls_client_key
        if self.tls_client_cafile:
            cfg["PYTAK_TLS_CLIENT_CAFILE"] = self.tls_client_cafile
        if self.tls_dont_verify:
            cfg["PYTAK_TLS_DONT_VERIFY"] = "1"
        if self.debug:
            cfg["DEBUG"] = "1"
        return cfg
