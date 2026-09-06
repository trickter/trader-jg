from __future__ import annotations

import argparse
import asyncio
import logging

import uvicorn

from .collector import Collector
from .config import Settings
from .db import Database
from .research import ResearchService


def main() -> None:
    parser = argparse.ArgumentParser(prog="radar", description="热门币雷达")
    parser.add_argument("command", choices=("init-db", "collect-once", "collector", "serve", "research-import", "backfill", "replay", "research-worker"))
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--token-id", type=int)
    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper()), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.from_env()
    if args.command == "init-db":
        Database(settings.db_path).initialize()
    elif args.command == "collect-once":
        asyncio.run(Collector(settings).once())
    elif args.command == "collector":
        asyncio.run(Collector(settings).run_forever())
    elif args.command == "research-import":
        print(ResearchService(settings).refresh_qualifications())
    elif args.command == "backfill":
        print(asyncio.run(ResearchService(settings).backfill(args.days, args.limit, args.token_id)))
    elif args.command == "replay":
        print(ResearchService(settings).replay())
    elif args.command == "research-worker":
        service = ResearchService(settings)
        print(service.refresh_qualifications())
        print(asyncio.run(service.backfill(args.days, args.limit, args.token_id)))
        print(service.replay())
    else:
        uvicorn.run("radar.api:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()
