"""
Cache backend for storing package scoring data with freshness tracking.

Implements synchronous staleness checking to ensure data freshness guarantees.
Uses SQLite for local persistent storage with parameterized queries for security.
"""

import dataclasses
import logging
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from platformdirs import user_cache_dir

logger = logging.getLogger(__name__)


@dataclass
class SignalSnapshot:
    """Snapshot of package signals with per-group freshness tracking."""

    package_name: str
    registry: str

    # Identity
    github_url: str | None = None
    identity_verified: bool = False

    # Download/adoption signals (7 day freshness)
    weekly_downloads: int | None = None
    downloads_refreshed_at: datetime | None = None

    # Repository signals (30 day freshness)
    star_count: int | None = None
    fork_count: int | None = None
    fork_to_star_ratio: float | None = None
    days_since_last_commit: int | None = None
    open_issue_count: int | None = None
    closed_issues_last_year: int | None = None
    repo_refreshed_at: datetime | None = None

    # MTTR signals (21 day freshness)
    mttr_median_days: float | None = None
    mttr_mad: float | None = None
    mttr_state: str | None = None
    mttr_refreshed_at: datetime | None = None

    # Commit regularity (21 day freshness)
    weekly_commit_cv: float | None = None
    recent_committer_count: int | None = None
    regularity_refreshed_at: datetime | None = None

    # Version/stability (30 day freshness)
    latest_version: str | None = None
    first_release_date: datetime | None = None
    release_cv: float | None = None
    major_versions_per_year: float | None = None
    version_refreshed_at: datetime | None = None

    # Dependency health (7 day freshness)
    direct_dep_count: int | None = None
    vulnerable_dep_count: int | None = None
    deprecated_dep_count: int | None = None
    reverse_dep_count: int | None = None
    dep_health_refreshed_at: datetime | None = None

    # Metadata
    description: str | None = None
    license: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self):
        """Initialize timestamps if not provided."""
        now = datetime.now(timezone.utc)
        if self.created_at is None:
            self.created_at = now
        if self.updated_at is None:
            self.updated_at = now

    def is_signal_group_stale(self, group: str, freshness_days: int) -> bool:
        """Check if a signal group is stale based on freshness window."""
        refresh_field = f"{group}_refreshed_at"
        refreshed_at = getattr(self, refresh_field, None)

        if refreshed_at is None:
            return True  # Never refreshed

        if isinstance(refreshed_at, str):
            refreshed_at = datetime.fromisoformat(refreshed_at)
        if refreshed_at.tzinfo is None:
            refreshed_at = refreshed_at.replace(tzinfo=timezone.utc)

        age = datetime.now(timezone.utc) - refreshed_at
        return age > timedelta(days=freshness_days)


# Computed once at import time — used in get() to filter DB rows with removed columns
_SIGNAL_SNAPSHOT_FIELDS: frozenset = frozenset(f.name for f in dataclasses.fields(SignalSnapshot))


class SQLiteCache:
    """SQLite implementation of cache backend with parameterized queries."""

    def __init__(self, cache_dir: Path | None = None):
        """Initialize SQLite cache.

        Args:
            cache_dir: Directory for cache database. Defaults to platformdirs location.
        """
        if cache_dir is None:  # pragma: no cover
            cache_dir = Path(user_cache_dir("priorart"))

        cache_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = cache_dir / "cache.db"
        self._init_database()

    def _init_database(self):
        """Create database schema if not exists."""
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS package_signals (
                    package_name TEXT NOT NULL,
                    registry TEXT NOT NULL,

                    -- Identity
                    github_url TEXT,
                    identity_verified BOOLEAN DEFAULT 0,

                    -- Downloads (7 day freshness)
                    weekly_downloads INTEGER,
                    downloads_refreshed_at TEXT,

                    -- Repository (30 day freshness)
                    star_count INTEGER,
                    fork_count INTEGER,
                    fork_to_star_ratio REAL,
                    days_since_last_commit INTEGER,
                    open_issue_count INTEGER,
                    closed_issues_last_year INTEGER,
                    repo_refreshed_at TEXT,

                    -- MTTR (21 day freshness)
                    mttr_median_days REAL,
                    mttr_mad REAL,
                    mttr_state TEXT,
                    mttr_refreshed_at TEXT,

                    -- Regularity (21 day freshness)
                    weekly_commit_cv REAL,
                    recent_committer_count INTEGER,
                    regularity_refreshed_at TEXT,

                    -- Version (30 day freshness)
                    latest_version TEXT,
                    first_release_date TEXT,
                    release_cv REAL,
                    major_versions_per_year REAL,
                    version_refreshed_at TEXT,

                    -- Dependencies (7 day freshness)
                    direct_dep_count INTEGER,
                    vulnerable_dep_count INTEGER,
                    deprecated_dep_count INTEGER,
                    reverse_dep_count INTEGER,
                    dep_health_refreshed_at TEXT,

                    -- Metadata
                    description TEXT,
                    license TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,

                    PRIMARY KEY (package_name, registry)
                )
            """)

            # Enable WAL mode for concurrent reads
            conn.execute("PRAGMA journal_mode=WAL")

    def get(self, package_name: str, registry: str) -> SignalSnapshot | None:
        """Retrieve package snapshot using parameterized query."""
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM package_signals WHERE package_name = ? AND registry = ?",
                (package_name, registry),
            )
            row = cursor.fetchone()

            if row is None:
                return None

            # Filter to known fields only — handles old cache DBs with removed columns
            data = {k: v for k, v in dict(row).items() if k in _SIGNAL_SNAPSHOT_FIELDS}

            # Parse datetime fields
            for field in [
                "created_at",
                "updated_at",
                "downloads_refreshed_at",
                "repo_refreshed_at",
                "mttr_refreshed_at",
                "regularity_refreshed_at",
                "version_refreshed_at",
                "dep_health_refreshed_at",
                "first_release_date",
            ]:
                if data.get(field):
                    try:
                        dt = datetime.fromisoformat(data[field])
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        data[field] = dt
                    except (ValueError, TypeError):
                        data[field] = None

            return SignalSnapshot(**data)

    def set(self, snapshot: SignalSnapshot) -> None:
        """Store or update package snapshot using parameterized query."""
        snapshot.updated_at = datetime.now(timezone.utc)

        # Convert datetime objects to ISO format strings
        data = asdict(snapshot)
        for key, value in data.items():
            if isinstance(value, datetime):
                data[key] = value.isoformat()

        with sqlite3.connect(self.db_path, timeout=10) as conn:
            # Use REPLACE to insert or update
            placeholders = ", ".join(["?"] * len(data))
            columns = ", ".join(data.keys())

            conn.execute(
                f"""
                REPLACE INTO package_signals ({columns})
                VALUES ({placeholders})
            """,
                list(data.values()),
            )

    def exists(self, package_name: str, registry: str) -> bool:
        """Check if package exists in cache."""
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM package_signals WHERE package_name = ? AND registry = ?",
                (package_name, registry),
            )
            return cursor.fetchone()[0] > 0

    def clear_stale(self, max_age_days: int = 90) -> int:
        """Remove entries older than max_age_days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

        with sqlite3.connect(self.db_path, timeout=10) as conn:
            cursor = conn.execute(
                "DELETE FROM package_signals WHERE updated_at < ?", (cutoff.isoformat(),)
            )
            return cursor.rowcount

    def update_signal_group(
        self, package_name: str, registry: str, group: str, signals: dict[str, Any]
    ) -> None:
        """Update a specific signal group without affecting others."""
        now = datetime.now(timezone.utc).isoformat()
        signals[f"{group}_refreshed_at"] = now

        # Validate signal keys against known schema to prevent column name injection
        unknown = set(signals) - _SIGNAL_SNAPSHOT_FIELDS
        if unknown:
            raise ValueError(f"Unknown signal fields: {unknown}")

        # Build UPDATE query with parameterized column names (values always parameterized)
        set_clause = ", ".join([f"{k} = ?" for k in signals.keys()])
        values = list(signals.values())

        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.execute(
                f"""
                UPDATE package_signals
                SET {set_clause}, updated_at = ?
                WHERE package_name = ? AND registry = ?
            """,
                values + [now, package_name, registry],
            )


def copy_seed_cache(seed_path: Path) -> None:  # pragma: no cover
    """Copy seed cache to user directory on first install.

    Args:
        seed_path: Path to bundled seed_cache.db
    """
    cache_dir = Path(user_cache_dir("priorart"))
    cache_dir.mkdir(parents=True, exist_ok=True)

    user_cache = cache_dir / "cache.db"

    if not user_cache.exists() and seed_path.exists():
        import shutil

        shutil.copy2(seed_path, user_cache)
        logger.info(f"Initialized cache with seed data at {user_cache}")
