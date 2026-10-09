"""Record existing Internet Archive snapshots for cited sources (read-only lookups).

A snapshot is accepted only if it was captured on or after the source's publication date (when
known) and no later than 30 days after the maintainers accessed it, so the archived copy shows the
page as it stood when it was cited. Nothing is submitted to the archive.
"""

from __future__ import annotations

import datetime as dt
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .contract import partial_date_bounds, validate_project
from .layers import USER_AGENT

AVAILABILITY_API = "https://archive.org/wayback/available"
MAX_DAYS_AFTER_ACCESS = 30
MAX_DAYS_BEFORE_ACCESS_UNDATED = 365


def acceptable(snapshot_ts: str, published: str | None, accessed: str) -> bool:
    taken = dt.datetime.strptime(snapshot_ts[:8], "%Y%m%d").date()
    seen = dt.date.fromisoformat(accessed)
    if taken > seen + dt.timedelta(days=MAX_DAYS_AFTER_ACCESS):
        return False
    if published:
        return taken >= partial_date_bounds(published)[0]
    return taken >= seen - dt.timedelta(days=MAX_DAYS_BEFORE_ACCESS_UNDATED)


def lookup(url: str, accessed: str, timeout: int = 30) -> dict[str, Any] | None:
    query = urllib.parse.urlencode({"url": url, "timestamp": accessed.replace("-", "")})
    request = urllib.request.Request(f"{AVAILABILITY_API}?{query}",
                                     headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        data = json.load(response)
    closest = (data.get("archived_snapshots") or {}).get("closest")
    if not closest or not closest.get("available") or str(closest.get("status")) != "200":
        return None
    return closest


def fill_archived_urls(projects_dir: Path, *, delay: float = 1.0) -> dict[str, int]:
    """Add ``archived_url`` to sources that lack one; returns counts."""
    counts = {"checked": 0, "added": 0, "none": 0, "errors": 0}
    for path in sorted(projects_dir.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        changed = False
        for source in record["sources"]:
            if source.get("archived_url"):
                continue
            counts["checked"] += 1
            try:
                snap = lookup(source["url"], source["accessed"])
            except Exception:  # noqa: BLE001 - lookups are best effort
                counts["errors"] += 1
                snap = None
            time.sleep(delay)
            if snap and acceptable(snap["timestamp"], source.get("published"),
                                   source["accessed"]):
                source["archived_url"] = snap["url"].replace("http://", "https://", 1)
                counts["added"] += 1
                changed = True
            elif snap is None:
                counts["none"] += 1
        if changed:
            if validate_project(record):
                raise ValueError(f"{path}: archive lookup produced an invalid record")
            path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
    return counts
