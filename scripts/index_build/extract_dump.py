"""
Stream-extract the packages table from an ecosyste.ms PostgreSQL custom-format
dump and write per-ecosystem slim JSONL files.

Reads `pg_restore --data-only` SQL output from stdin. The dump's table order is
not guaranteed, so we scan COPY headers and only consume the ``public.packages``
block — every other table is skipped row-by-row without parsing.

Output JSONL fields per package (one of our 6 ecosystems, status not "removed"):

    name, registry, description, repository_url, homepage,
    downloads, dependent_packages_count, dependent_repos_count,
    latest_release_published_at

Usage:

    pg_restore --data-only dump.sql | python -m scripts.index_build.extract_dump out/
"""

from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import IO

logger = logging.getLogger(__name__)

# ecosyste.ms uses lowercase ecosystem strings on packages.ecosystem. Our
# internal ecosystem names differ (e.g. "crates" vs "cargo"); keep both so the
# slim JSONL slots straight into the existing fetch path.
ECOSYSTEM_MAP = {
    "pypi": ("python", "pypi"),
    "npm": ("npm", "npm"),
    "cargo": ("crates", "cargo"),
    "go": ("go", "go"),
    "maven": ("maven", "maven"),
    "nuget": ("nuget", "nuget"),
}

# Sized to hit before any tracked ecosystem could legitimately have zero
# matches — even the smallest registry we track holds well over this. Zero
# matches by here means filter drift, not data.
EARLY_EXIT_THRESHOLD = 500_000

# Postgres COPY text-format escape sequences. \N is special-cased — it only
# represents NULL when it is the entire field, never embedded.
_COPY_ESCAPES = {"\\": "\\", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}
_COPY_RE = re.compile(r"\\(\\|b|f|n|r|t|v)")
_COPY_HEADER_RE = re.compile(r"^COPY public\.([A-Za-z_][A-Za-z0-9_]*) \((.+)\) FROM stdin;$")


def _unescape(field: str) -> str:
    if "\\" not in field:
        return field
    return _COPY_RE.sub(lambda m: _COPY_ESCAPES[m.group(1)], field)


def _to_int(val: str | None) -> int:
    if val is None or val == "":
        return 0
    try:
        return int(val)
    except ValueError:
        return 0


def _parse_row(line: str, columns: list[str]) -> dict:
    fields = line.rstrip("\n").split("\t")
    if len(fields) != len(columns):
        raise ValueError(f"column count mismatch: {len(fields)} vs {len(columns)}")
    return {col: None if val == "\\N" else _unescape(val) for col, val in zip(columns, fields)}


def _slim_record(row: dict, registry_tag: str) -> dict:
    return {
        "name": row.get("name") or "",
        "registry": registry_tag,
        "description": row.get("description") or "",
        "repository_url": row.get("repository_url") or None,
        "homepage": row.get("homepage") or None,
        "downloads": _to_int(row.get("downloads")),
        "dependent_packages_count": _to_int(row.get("dependent_packages_count")),
        "dependent_repos_count": _to_int(row.get("dependent_repos_count")),
        "latest_release_published_at": row.get("latest_release_published_at"),
    }


def extract(stream: Iterable[str], out_dir: Path) -> dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {eco: 0 for eco, _ in ECOSYSTEM_MAP.values()}
    files: dict[str, IO[str]] = {}
    columns: list[str] | None = None
    in_packages = False
    scanned = 0

    try:
        for raw in stream:
            line = raw.rstrip("\n")

            if columns is None:
                m = _COPY_HEADER_RE.match(line)
                if not m:
                    continue
                table, cols = m.group(1), m.group(2)
                if table != "packages":
                    # Bypass _parse_row for non-target tables — the full dump
                    # carries 100M+ rows across other tables we'd discard.
                    for inner in stream:
                        if inner.rstrip("\n") == "\\.":
                            break
                    continue
                columns = [c.strip() for c in cols.split(",")]
                in_packages = True
                logger.info("packages columns: %s", columns)
                continue

            if line == "\\.":
                logger.info("end of packages COPY block at row %d", scanned)
                break

            scanned += 1
            if scanned % 250_000 == 0:
                logger.info("scanned %d rows", scanned)
            # Fail fast on filter drift: zero matches this far in means
            # status / ecosystem semantics changed upstream, and the rest of
            # the scan would yield nothing either.
            if scanned == EARLY_EXIT_THRESHOLD and sum(counts.values()) == 0:
                raise RuntimeError(
                    f"scanned {EARLY_EXIT_THRESHOLD} rows with zero matches in any "
                    f"tracked ecosystem — filter logic is broken (check status / "
                    f"ecosystem column values vs ECOSYSTEM_MAP)"
                )

            try:
                row = _parse_row(raw, columns)
            except ValueError as e:
                logger.warning("skip malformed row %d: %s", scanned, e)
                continue

            # Status default 'active' was added after the column; pre-existing
            # rows have NULL. Accept those alongside explicit "active"; reject
            # only known-removed packages.
            status = row.get("status")
            if status not in (None, "", "active"):
                continue
            ecosystem_string = row.get("ecosystem") or ""
            target = ECOSYSTEM_MAP.get(ecosystem_string)
            if not target:
                continue

            ecosystem_name, registry_tag = target
            f = files.get(ecosystem_name)
            if f is None:
                f = (out_dir / f"{ecosystem_name}.jsonl").open("w", encoding="utf-8")
                files[ecosystem_name] = f
            f.write(json.dumps(_slim_record(row, registry_tag)) + "\n")
            counts[ecosystem_name] += 1
    finally:
        for f in files.values():
            f.close()

    if not in_packages:
        raise RuntimeError("no public.packages COPY block found in input stream")

    logger.info("total rows scanned: %d", scanned)
    for eco, n in counts.items():
        logger.info("  %s: %d", eco, n)
    return counts


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "snapshot")
    extract(sys.stdin, out_dir)


if __name__ == "__main__":
    main()
