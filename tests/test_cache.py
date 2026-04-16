"""Tests for cache backend."""

from datetime import datetime, timedelta, timezone

from priorart.core.cache import SignalSnapshot, SQLiteCache


def test_cache_initialization(temp_cache_dir):
    """Test cache initialization creates database."""
    SQLiteCache(temp_cache_dir)
    assert (temp_cache_dir / "cache.db").exists()


def test_cache_set_and_get(temp_cache_dir, sample_package_snapshot):
    """Test setting and retrieving cache entries."""
    cache = SQLiteCache(temp_cache_dir)

    cache.set(sample_package_snapshot)
    retrieved = cache.get('requests', 'pypi')

    assert retrieved is not None
    assert retrieved.package_name == 'requests'
    assert retrieved.registry == 'pypi'
    assert retrieved.weekly_downloads == 45000000
    assert retrieved.star_count == 50000


def test_cache_exists(temp_cache_dir, sample_package_snapshot):
    """Test cache existence check."""
    cache = SQLiteCache(temp_cache_dir)

    assert not cache.exists('requests', 'pypi')

    cache.set(sample_package_snapshot)

    assert cache.exists('requests', 'pypi')
    assert not cache.exists('httpx', 'pypi')


def test_cache_update(temp_cache_dir, sample_package_snapshot):
    """Test updating existing cache entry."""
    cache = SQLiteCache(temp_cache_dir)

    cache.set(sample_package_snapshot)

    # Update downloads
    sample_package_snapshot.weekly_downloads = 50000000
    cache.set(sample_package_snapshot)

    retrieved = cache.get('requests', 'pypi')
    assert retrieved.weekly_downloads == 50000000


def test_signal_group_staleness(sample_package_snapshot):
    """Test signal group staleness detection."""
    snapshot = sample_package_snapshot

    # Fresh data
    snapshot.downloads_refreshed_at = datetime.now(timezone.utc)
    assert not snapshot.is_signal_group_stale('downloads', 7)

    # Stale data
    snapshot.downloads_refreshed_at = datetime.now(timezone.utc) - timedelta(days=10)
    assert snapshot.is_signal_group_stale('downloads', 7)

    # Never refreshed
    snapshot.downloads_refreshed_at = None
    assert snapshot.is_signal_group_stale('downloads', 7)


def test_clear_stale(temp_cache_dir):
    """Test clearing stale cache entries."""
    import sqlite3

    cache = SQLiteCache(temp_cache_dir)

    # Create old entry — cache.set() overwrites updated_at with now(),
    # so backdate it via direct SQL after insertion.
    old_snapshot = SignalSnapshot(
        package_name='old_package',
        registry='pypi',
    )
    cache.set(old_snapshot)
    old_ts = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    with sqlite3.connect(cache.db_path, timeout=10) as conn:
        conn.execute(
            "UPDATE package_signals SET updated_at = ? WHERE package_name = ?",
            (old_ts, 'old_package'),
        )

    # Create recent entry
    new_snapshot = SignalSnapshot(
        package_name='new_package',
        registry='pypi',
    )
    cache.set(new_snapshot)

    # Clear entries older than 90 days
    deleted = cache.clear_stale(max_age_days=90)

    assert deleted == 1
    assert not cache.exists('old_package', 'pypi')
    assert cache.exists('new_package', 'pypi')


def test_update_signal_group(temp_cache_dir, sample_package_snapshot):
    """Test updating a specific signal group."""
    cache = SQLiteCache(temp_cache_dir)
    cache.set(sample_package_snapshot)

    # Update downloads signal group
    new_signals = {
        'weekly_downloads': 60000000,
    }

    cache.update_signal_group('requests', 'pypi', 'downloads', new_signals)

    retrieved = cache.get('requests', 'pypi')
    assert retrieved.weekly_downloads == 60000000
    assert retrieved.downloads_refreshed_at is not None


def test_cache_datetime_serialization(temp_cache_dir):
    """Test datetime fields serialize/deserialize correctly."""
    cache = SQLiteCache(temp_cache_dir)

    now = datetime.now(timezone.utc)
    snapshot = SignalSnapshot(
        package_name='test',
        registry='pypi',
        first_release_date=now,
        downloads_refreshed_at=now,
        created_at=now,
        updated_at=now,
    )

    cache.set(snapshot)
    retrieved = cache.get('test', 'pypi')

    # Datetimes should be preserved (within 1 second)
    assert abs((retrieved.first_release_date - now).total_seconds()) < 1
    assert abs((retrieved.downloads_refreshed_at - now).total_seconds()) < 1


def test_staleness_string_datetime():
    """is_signal_group_stale handles ISO string datetime correctly."""
    snapshot = SignalSnapshot(
        package_name='test',
        registry='pypi',
    )

    # Set as ISO string (as stored in SQLite)
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    snapshot.downloads_refreshed_at = recent
    assert not snapshot.is_signal_group_stale('downloads', 7)

    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    snapshot.downloads_refreshed_at = old
    assert snapshot.is_signal_group_stale('downloads', 7)


def test_staleness_naive_datetime():
    """is_signal_group_stale adds UTC to naive datetimes."""
    snapshot = SignalSnapshot(
        package_name='test',
        registry='pypi',
    )

    # Naive ISO string (no timezone)
    naive_recent = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    snapshot.downloads_refreshed_at = naive_recent
    assert not snapshot.is_signal_group_stale('downloads', 7)


def test_update_signal_group_rejects_unknown_fields(temp_cache_dir, sample_package_snapshot):
    """update_signal_group rejects fields not in SignalSnapshot."""
    cache = SQLiteCache(temp_cache_dir)
    cache.set(sample_package_snapshot)

    import pytest
    with pytest.raises(ValueError, match="Unknown signal fields"):
        cache.update_signal_group('requests', 'pypi', 'downloads', {'bogus_field': 42})


def test_cache_get_nonexistent(temp_cache_dir):
    """get returns None for non-existent package."""
    cache = SQLiteCache(temp_cache_dir)
    assert cache.get('nonexistent', 'pypi') is None


def test_cache_get_naive_datetime_in_db(temp_cache_dir):
    """get() adds UTC to naive datetime strings stored in SQLite."""
    import sqlite3

    cache = SQLiteCache(temp_cache_dir)

    # Insert with naive datetime (no timezone) via direct SQL
    naive_ts = "2024-01-15T10:00:00"
    snapshot = SignalSnapshot(package_name='naive-pkg', registry='pypi')
    cache.set(snapshot)

    # Backdate downloads_refreshed_at to a naive string
    with sqlite3.connect(cache.db_path, timeout=10) as conn:
        conn.execute(
            "UPDATE package_signals SET downloads_refreshed_at = ? WHERE package_name = ?",
            (naive_ts, 'naive-pkg'),
        )

    retrieved = cache.get('naive-pkg', 'pypi')
    assert retrieved is not None
    assert retrieved.downloads_refreshed_at is not None
    assert retrieved.downloads_refreshed_at.tzinfo is not None


def test_cache_get_invalid_datetime_in_db(temp_cache_dir):
    """get() handles invalid datetime string gracefully."""
    import sqlite3

    cache = SQLiteCache(temp_cache_dir)
    snapshot = SignalSnapshot(package_name='bad-dt', registry='pypi')
    cache.set(snapshot)

    # Set an invalid datetime string
    with sqlite3.connect(cache.db_path, timeout=10) as conn:
        conn.execute(
            "UPDATE package_signals SET downloads_refreshed_at = ? WHERE package_name = ?",
            ("not-a-date", 'bad-dt'),
        )

    retrieved = cache.get('bad-dt', 'pypi')
    assert retrieved is not None
    assert retrieved.downloads_refreshed_at is None
