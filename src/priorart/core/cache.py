"""
Cache backend for storing package scoring data with freshness tracking.

Implements synchronous staleness checking to ensure data freshness guarantees.
Uses SQLite for local persistent storage with parameterized queries for security.
"""

import json
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Any, List
from platformdirs import user_cache_dir


@dataclass
class SignalSnapshot:
    """Snapshot of package signals with per-group freshness tracking."""

    package_name: str
    registry: str

    # Identity
    github_url: Optional[str] = None
    identity_verified: bool = False

    # Download/adoption signals (7 day freshness)
    weekly_downloads: Optional[int] = None
    download_percentile: Optional[float] = None
    downloads_refreshed_at: Optional[datetime] = None

    # Repository signals (30 day freshness)
    star_count: Optional[int] = None
    fork_count: Optional[int] = None
    fork_to_star_ratio: Optional[float] = None
    days_since_last_commit: Optional[int] = None
    open_issue_count: Optional[int] = None
    closed_issues_last_year: Optional[int] = None
    repo_refreshed_at: Optional[datetime] = None

    # MTTR signals (21 day freshness)
    mttr_median_days: Optional[float] = None
    mttr_mad: Optional[float] = None
    mttr_state: Optional[str] = None
    mttr_refreshed_at: Optional[datetime] = None

    # Commit regularity (21 day freshness)
    weekly_commit_cv: Optional[float] = None
    recent_committer_count: Optional[int] = None
    regularity_refreshed_at: Optional[datetime] = None

    # Version/stability (30 day freshness)
    latest_version: Optional[str] = None
    first_release_date: Optional[datetime] = None
    release_cv: Optional[float] = None
    major_versions_per_year: Optional[float] = None
    version_refreshed_at: Optional[datetime] = None

    # Dependency health (7 day freshness)
    direct_dep_count: Optional[int] = None
    vulnerable_dep_count: Optional[int] = None
    deprecated_dep_count: Optional[int] = None
    reverse_dep_count: Optional[int] = None
    dep_health_refreshed_at: Optional[datetime] = None

    # Metadata
    description: Optional[str] = None
    license: Optional[str] = None
    created_at: datetime = None
    updated_at: datetime = None

    def __post_init__(self):
        """Initialize timestamps if not provided."""
        now = datetime.utcnow()
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

        age = datetime.utcnow() - refreshed_at
        return age > timedelta(days=freshness_days)


class SQLiteCache:
    """SQLite implementation of cache backend with parameterized queries."""

    def __init__(self, cache_dir: Optional[Path] = None):
        """Initialize SQLite cache.

        Args:
            cache_dir: Directory for cache database. Defaults to platformdirs location.
        """
        if cache_dir is None:
            cache_dir = Path(user_cache_dir("priorart"))

        cache_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = cache_dir / "cache.db"
        self._init_database()

    def _init_database(self):
        """Create database schema if not exists."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS package_signals (
                    package_name TEXT NOT NULL,
                    registry TEXT NOT NULL,

                    -- Identity
                    github_url TEXT,
                    identity_verified BOOLEAN DEFAULT 0,

                    -- Downloads (7 day freshness)
                    weekly_downloads INTEGER,
                    download_percentile REAL,
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

            # Create index for faster lookups
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_package_registry
                ON package_signals(package_name, registry)
            """)

            # Enable WAL mode for concurrent reads
            conn.execute("PRAGMA journal_mode=WAL")

    def get(self, package_name: str, registry: str) -> Optional[SignalSnapshot]:
        """Retrieve package snapshot using parameterized query."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM package_signals WHERE package_name = ? AND registry = ?",
                (package_name, registry)
            )
            row = cursor.fetchone()

            if row is None:
                return None

            # Convert Row to dict and handle datetime fields
            data = dict(row)

            # Parse datetime fields
            for field in ['created_at', 'updated_at', 'downloads_refreshed_at',
                         'repo_refreshed_at', 'mttr_refreshed_at',
                         'regularity_refreshed_at', 'version_refreshed_at',
                         'dep_health_refreshed_at', 'first_release_date']:
                if data.get(field):
                    try:
                        data[field] = datetime.fromisoformat(data[field])
                    except (ValueError, TypeError):
                        data[field] = None

            return SignalSnapshot(**data)

    def set(self, snapshot: SignalSnapshot) -> None:
        """Store or update package snapshot using parameterized query."""
        snapshot.updated_at = datetime.utcnow()

        # Convert datetime objects to ISO format strings
        data = asdict(snapshot)
        for key, value in data.items():
            if isinstance(value, datetime):
                data[key] = value.isoformat()

        with sqlite3.connect(self.db_path) as conn:
            # Use REPLACE to insert or update
            placeholders = ', '.join(['?'] * len(data))
            columns = ', '.join(data.keys())

            conn.execute(f"""
                REPLACE INTO package_signals ({columns})
                VALUES ({placeholders})
            """, list(data.values()))

    def exists(self, package_name: str, registry: str) -> bool:
        """Check if package exists in cache."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM package_signals WHERE package_name = ? AND registry = ?",
                (package_name, registry)
            )
            return cursor.fetchone()[0] > 0

    def clear_stale(self, max_age_days: int = 90) -> int:
        """Remove entries older than max_age_days."""
        cutoff = datetime.utcnow() - timedelta(days=max_age_days)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM package_signals WHERE updated_at < ?",
                (cutoff.isoformat(),)
            )
            return cursor.rowcount

    def update_signal_group(self, package_name: str, registry: str,
                          group: str, signals: Dict[str, Any]) -> None:
        """Update a specific signal group without affecting others."""
        refresh_field = f"{group}_refreshed_at"
        signals[refresh_field] = datetime.utcnow().isoformat()

        # Build UPDATE query with parameterized values
        set_clause = ', '.join([f"{k} = ?" for k in signals.keys()])
        values = list(signals.values()) + [package_name, registry]

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(f"""
                UPDATE package_signals
                SET {set_clause}, updated_at = ?
                WHERE package_name = ? AND registry = ?
            """, values + [datetime.utcnow().isoformat(), package_name, registry])


def copy_seed_cache(seed_path: Path) -> None:
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
        logging.info(f"Initialized cache with seed data at {user_cache}")