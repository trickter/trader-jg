from __future__ import annotations

import argparse
import asyncio
import logging

import uvicorn

from .collector import Collector
from .config import Settings
from .db import Database


def main() -> None:
    parser = argparse.ArgumentParser(prog="radar", description="热门币雷达")
    parser.add_argument("command", choices=("init-db", "collect-once", "collector", "serve"))
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper()), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.from_env()
    if args.command == "init-db":
        Database(settings.db_path).initialize()
    elif args.command == "collect-once":
        asyncio.run(Collector(settings).once())
    elif args.command == "collector":
        asyncio.run(Collector(settings).run_forever())
    else:
        uvicorn.run("radar.api:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()

