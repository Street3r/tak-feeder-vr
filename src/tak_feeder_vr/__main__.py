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

    # One worker for the whole run: its MQTT clients and train store survive
    # TAK outages. pytak rebuilds the TAK connection and queues on reconnect.
    worker = RailWorker(asyncio.Queue(), config, settings)

    async def run_once() -> None:
        clitool = pytak.CLITool(config)
        await clitool.setup()
        worker.queue = clitool.tx_queue
        clitool.add_tasks({worker, RxDrain(clitool.rx_queue, config)})
        await clitool.run()

    try:
        await pytak.supervise_with_reconnect(config, run_once)
    finally:
        worker.close()


def run() -> None:
    """Console script entry point."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
