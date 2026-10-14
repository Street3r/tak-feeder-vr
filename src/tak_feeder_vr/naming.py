# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Street3r
"""Callsigns, hashtags and UIDs."""

from __future__ import annotations

import re
from typing import Optional

from .model import TrainInfo, TrainKey

# Digitraffic trainCategory -> (English label, Finnish hashtag, English hashtag)
CATEGORIES: dict[str, tuple[str, str, str]] = {
    "Long-distance": ("Long-distance", "kaukoliikenne", "longdistance"),
    "Commuter": ("Commuter", "lähiliikenne", "commuter"),
    "Cargo": ("Cargo", "tavaraliikenne", "cargo"),
    "Locomotive": ("Locomotive", "veturi", "locomotive"),
    "Shunting": ("Shunting", "vaihtotyö", "shunting"),
    "On-track machines": ("Track machine", "ratatyökone", "trackmachine"),
    "Test drive": ("Test run", "koeajo", "testrun"),
}


def _tagify(text: str) -> str:
    """Make a hashtag-safe word: lowercase, no spaces or punctuation."""
    return re.sub(r"[^\w]", "", text.lower())


def category_label(info: Optional[TrainInfo]) -> str:
    """English label for the train category."""
    if not info or not info.category:
        return "Unknown"
    return CATEGORIES.get(info.category, (info.category, "", ""))[0]


def category_tags(info: Optional[TrainInfo]) -> list[str]:
    """Hashtag words for the train category: Finnish, then English."""
    if not info or not info.category:
        return ["tuntematon", "unknown"]
    known = CATEGORIES.get(info.category)
    return [known[1], known[2]] if known else [_tagify(info.category)]


def operator_code(info: Optional[TrainInfo], overrides: dict[str, str]) -> str:
    """Operator abbreviation shown in the callsign, e.g. "vr" -> "VR"."""
    if not info or not info.operator:
        return ""
    return overrides.get(info.operator.lower(), info.operator.upper())


def is_commuter(info: Optional[TrainInfo]) -> bool:
    """Commuter trains with a line letter are named by line, like HSL does."""
    return bool(info and info.commuter_line)


def commuter_brand(info: Optional[TrainInfo], brands: dict[str, str]) -> str:
    """Transit authority whose line this is ("HSL", "NYSSE"), or "" when the
    line is not in the brand map (e.g. R, Z: named after the operator)."""
    if not info or not info.commuter_line:
        return ""
    return brands.get(info.commuter_line.upper(), "")


def callsign(
    key: TrainKey,
    info: Optional[TrainInfo],
    overrides: dict[str, str],
    brands: Optional[dict[str, str]] = None,
) -> str:
    """
    VR IC 59 (long-distance and others); commuter lines by the authority
    that runs the line, like tak-feeder-hsl / tak-feeder-nysse name them
    (HSL I, NYSSE M), otherwise by operator (VR R).
    Without timetable data: TRAIN <number>.
    """
    number = key[1]
    if not info:
        return f"TRAIN {number}"
    if is_commuter(info):
        prefix = commuter_brand(info, brands or {}) or operator_code(info, overrides)
        return f"{prefix} {info.commuter_line}".strip()
    op = operator_code(info, overrides)
    return f"{op} {info.train_type} {number}".strip()


def route_tag(key: TrainKey, info: Optional[TrainInfo]) -> str:
    """#IC59 for numbered trains, #R for commuter lines."""
    if info and is_commuter(info):
        return _tagify(info.commuter_line).upper()
    train_type = info.train_type if info else ""
    return f"{_tagify(train_type).upper()}{key[1]}"


_COMPANY_FORMS = re.compile(r"[\s,]+(oyj|oy|ab|abp|ltd|as|gmbh)\.?$", re.IGNORECASE)


def operator_tag(name: str) -> str:
    """Hashtag word for an operator's full name without the company form:
    "Fenniarail Oy" -> "fenniarail", "VR-Yhtymä Oyj" -> "vryhtymä"."""
    return _tagify(_COMPANY_FORMS.sub("", name.strip()))


def hashtags(
    key: TrainKey,
    info: Optional[TrainInfo],
    feed_tag: str,
    brands: Optional[dict[str, str]] = None,
    operator_name: str = "",
) -> str:
    """#vr #IC59 #kaukoliikenne #longdistance #vryhtymä: feed, route, category
    (Finnish, English), operator's full name; the authority of its lines last:
    #vr #I #lähiliikenne #commuter #vryhtymä #hsl"""
    tags = [feed_tag, route_tag(key, info), *category_tags(info)]
    if operator_name:
        tags.append(operator_tag(operator_name))
    brand = commuter_brand(info, brands or {})
    if brand:
        tags.append(_tagify(brand))
    seen: set[str] = set()
    unique = [t for t in tags if t and not (t in seen or seen.add(t))]
    return " ".join(f"#{t}" for t in unique)


def native_uid(key: TrainKey, prefix: str) -> str:
    """VR-2026-10-13-59. Trains without a departure date use "x"."""
    date, number = key
    return f"{prefix}-{date or 'x'}-{number}"
