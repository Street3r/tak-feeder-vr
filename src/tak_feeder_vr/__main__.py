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

from . import railworks
from .config import Settings
from .worker import RailWorker, RxDrain

LOG = logging.getLogger("tak_feeder_vr")


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

    # Track works and traffic restrictions go to a mission through the TAK
    # API, independent of the CoT connection.
    rw = railworks.RailworksSettings.from_env()
    tasks = [pytak.supervise_with_reconnect(config, run_once)]
    if rw.enabled and rw.api_host and rw.include:
        LOG.info("Railworks: %s -> mission %s (%s) on %s", ",".join(rw.include),
                 rw.mission_name, ",".join(rw.groups) or "default groups", rw.api_host)
        tasks.append(railworks.run(rw))
    elif rw.enabled:
        LOG.warning("Railworks off: no API_HOST and no host in COT_URL")
    try:
        await asyncio.gather(*tasks)
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
