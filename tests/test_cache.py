"""Tests for cache backend."""

from datetime import datetime, timedelta

import pytest

from priorart.core.cache import SQLiteCache, SignalSnapshot


def test_cache_initialization(temp_cache_dir):
    """Test cache initialization creates database."""
    cache = SQLiteCache(temp_cache_dir)
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
    snapshot.downloads_refreshed_at = datetime.utcnow()
    assert not snapshot.is_signal_group_stale('downloads', 7)

    # Stale data
    snapshot.downloads_refreshed_at = datetime.utcnow() - timedelta(days=10)
    assert snapshot.is_signal_group_stale('downloads', 7)

    # Never refreshed
    snapshot.downloads_refreshed_at = None
    assert snapshot.is_signal_group_stale('downloads', 7)


def test_clear_stale(temp_cache_dir):
    """Test clearing stale cache entries."""
    cache = SQLiteCache(temp_cache_dir)

    # Create old entry
    old_snapshot = SignalSnapshot(
        package_name='old_package',
        registry='pypi',
        created_at=datetime.utcnow() - timedelta(days=100),
        updated_at=datetime.utcnow() - timedelta(days=100),
    )
    cache.set(old_snapshot)

    # Create recent entry
    new_snapshot = SignalSnapshot(
        package_name='new_package',
        registry='pypi',
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
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
        'download_percentile': 0.995,
    }

    cache.update_signal_group('requests', 'pypi', 'downloads', new_signals)

    retrieved = cache.get('requests', 'pypi')
    assert retrieved.weekly_downloads == 60000000
    assert retrieved.download_percentile == 0.995
    assert retrieved.downloads_refreshed_at is not None


def test_cache_datetime_serialization(temp_cache_dir):
    """Test datetime fields serialize/deserialize correctly."""
    cache = SQLiteCache(temp_cache_dir)

    now = datetime.utcnow()
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