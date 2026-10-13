# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Street3r
#
# Entry point follows sgofferj's tak-feeder-hsl, Copyright Stefan Gofferje,
# GPL-3.0-or-later.
"""Run the feeder: python -m tak_feeder_vr"""

from __future__ import annotations

import asyncio
import logging
from configparser import ConfigParser

import pytak

from .config import Settings
from .worker import RailWorker, RxDrain


async def main() -> None:
    """Read settings, connect to TAK and run the workers."""
    settings = Settings.from_env()
    logging.basicConfig(
        level=logging.DEBUG if settings.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = ConfigParser(interpolation=None)
    parser["tak_feeder_vr"] = settings.pytak_config()
    config = parser["tak_feeder_vr"]

    clitool = pytak.CLITool(config)
    await clitool.setup()
    clitool.add_tasks(
        {
            RailWorker(clitool.tx_queue, config, settings),
            RxDrain(clitool.rx_queue, config),
        }
    )
    await clitool.run()


def run() -> None:
    """Console script entry point."""
    asyncio.run(main())


if __name__ == "__main__":
    run()
