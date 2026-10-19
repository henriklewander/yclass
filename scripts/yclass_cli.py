#!/usr/bin/env python3
"""Try the Vklass client outside Home Assistant.

    .venv/bin/python scripts/yclass_cli.py            # credentials from .env
    .venv/bin/python scripts/yclass_cli.py --days 28 --json

.env holds VK_USER and VK_PASS (see .env.example).
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta
import json
import os
from pathlib import Path
import importlib.util
import sys

import aiohttp

ROOT = Path(__file__).resolve().parent.parent


def _load_vklass():
    # Load the file on its own: putting the integration directory on sys.path
    # would let its calendar.py shadow the standard library's calendar module.
    spec = importlib.util.spec_from_file_location("vklass", ROOT / "custom_components" / "yclass" / "vklass.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["vklass"] = module
    spec.loader.exec_module(module)
    return module


vklass = _load_vklass()
VklassClient, new_cookie_jar = vklass.VklassClient, vklass.new_cookie_jar


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", type=Path, default=ROOT / ".env")
    parser.add_argument("--days", type=int, default=21, help="how far ahead to look (default 21)")
    parser.add_argument("--past", type=int, default=0, help="also include this many days back")
    parser.add_argument("--json", action="store_true", help="print homework as JSON")
    args = parser.parse_args()

    load_env(args.env)
    user, password = os.environ.get("VK_USER"), os.environ.get("VK_PASS")
    if not user or not password:
        parser.error(f"VK_USER and VK_PASS must be set (in {args.env} or the environment)")

    today = date.today()
    async with aiohttp.ClientSession(cookie_jar=new_cookie_jar()) as session:
        client = VklassClient(session, user, password)
        student = await client.login()
        data = await client.fetch(today - timedelta(days=args.past), today + timedelta(days=args.days))

    if args.json:
        print(json.dumps([hw.as_dict() for hw in data.homework], ensure_ascii=False, indent=2))
        return 0

    print(f"{student.full_name} (id {student.user_id})")
    for hw in data.homework:
        print(f"  {hw.due:%a %Y-%m-%d %H:%M}  [{hw.kind or '?'} / {hw.subject or '?'}]  {hw.title}")
        for line in hw.description.splitlines():
            print(f"      {line}")
    if not data.homework:
        print("  (inga läxor)")
    for day in data.school_days(today)[:7]:
        print(f"  skoldag {day.day:%a %Y-%m-%d}  {day.start:%H:%M}–{day.end:%H:%M}  lunch: {' | '.join(day.lunch) or '-'}")
    for off in data.days_off:
        span = f"{off.start}" if off.start == off.end else f"{off.start} – {off.end}"
        print(f"  ledig: {span}  {off.title}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
