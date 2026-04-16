"""Tests for scoring engine."""

import pytest

from priorart.core.scoring import PackageScorer


def test_scorer_initialization(sample_config):
    """Test scorer initializes with valid config."""
    scorer = PackageScorer(sample_config)
    assert scorer.weights == sample_config['weights']


def test_scorer_rejects_invalid_weights(sample_config):
    """Test scorer rejects weights that don't sum to 1.0."""
    bad_config = sample_config.copy()
    bad_config['weights'] = {
        'reliability': 0.40,
        'adoption': 0.20,
        'versioning': 0.20,
        'activity_regularity': 0.15,
        'dependency_health': 0.15,
    }

    with pytest.raises(ValueError, match="must sum to 1.0"):
        PackageScorer(bad_config)


def test_score_healthy_package(sample_config, sample_package_data):
    """Test scoring a healthy package like requests."""
    scorer = PackageScorer(sample_config)

    scored = scorer.score_package(sample_package_data, explain=True)

    # Requests scores in evaluate/use_existing range (70 with default fixture data
    # because days_since_compatible_release defaults to 365 and reliability MTTR
    # of 7.5 days yields ~47/100)
    assert scored.health_score >= 60
    assert scored.recommendation in ("use_existing", "evaluate")
    assert scored.name == "requests"
    assert scored.license_warning is False
    assert scored.dep_health_flag is False

    # Check score breakdown exists
    assert scored.score_breakdown is not None
    assert scored.score_breakdown.reliability > 0
    assert scored.score_breakdown.adoption > 0


def test_score_young_package(sample_config, sample_package_data):
    """Test age confidence multiplier for young packages."""
    scorer = PackageScorer(sample_config)

    # Make package very young (6 months old)
    from datetime import datetime, timedelta, timezone
    sample_package_data['first_release_date'] = datetime.now(timezone.utc) - timedelta(days=180)

    scored = scorer.score_package(sample_package_data)

    # Young package should be blended toward neutral
    assert scored.age_years < 1.0
    # Score should be lower than if fully trusted
    assert scored.health_score < 80  # Would be higher if 3+ years old


def test_score_abandoned_package(sample_config, sample_package_data):
    """Test abandoned package detection."""
    scorer = PackageScorer(sample_config)

    # Set abandonment signals
    sample_package_data['days_since_last_commit'] = 600  # >540 days
    sample_package_data['open_issue_count'] = 500
    sample_package_data['closed_issues_last_year'] = 10

    scored = scorer.score_package(sample_package_data)

    assert scored.likely_abandoned is True


def test_floor_filter(sample_config):
    """Test floor filter excludes low-quality packages."""
    scorer = PackageScorer(sample_config)

    candidates = [
        {'name': 'popular', 'weekly_downloads': 10000, 'star_count': 5000},
        {'name': 'niche', 'weekly_downloads': 200, 'star_count': 30},  # Below threshold
        {'name': 'medium', 'weekly_downloads': 500, 'star_count': 100},
    ]

    filtered = scorer.apply_floor_filter(candidates)

    assert len(filtered) == 2
    assert filtered[0]['name'] == 'popular'
    assert filtered[1]['name'] == 'medium'


def test_copyleft_license_warning(sample_config, sample_package_data):
    """Test copyleft license detection."""
    scorer = PackageScorer(sample_config)

    # Test GPL
    sample_package_data['license'] = 'GPL-3.0'
    scored = scorer.score_package(sample_package_data)
    assert scored.license_warning is True

    # Test AGPL
    sample_package_data['license'] = 'AGPL-3.0'
    scored = scorer.score_package(sample_package_data)
    assert scored.license_warning is True

    # Test MIT (not copyleft)
    sample_package_data['license'] = 'MIT'
    scored = scorer.score_package(sample_package_data)
    assert scored.license_warning is False


def test_dependency_health_flag(sample_config, sample_package_data):
    """Test dependency health flag for vulnerabilities."""
    scorer = PackageScorer(sample_config)

    # No vulnerabilities
    sample_package_data['vulnerable_dep_count'] = 0
    sample_package_data['deprecated_dep_count'] = 0
    scored = scorer.score_package(sample_package_data)
    assert scored.dep_health_flag is False

    # Has vulnerabilities
    sample_package_data['vulnerable_dep_count'] = 2
    scored = scorer.score_package(sample_package_data)
    assert scored.dep_health_flag is True

    # Too many deprecated
    sample_package_data['vulnerable_dep_count'] = 0
    sample_package_data['deprecated_dep_count'] = 5
    scored = scorer.score_package(sample_package_data)
    assert scored.dep_health_flag is True


def test_recommendation_thresholds(sample_config, sample_package_data):
    """Test recommendation based on health score."""
    scorer = PackageScorer(sample_config)

    # High score: strong signals + fast MTTR + recent release
    high_score_data = sample_package_data.copy()
    high_score_data['weekly_downloads'] = 50000000
    high_score_data['star_count'] = 60000
    high_score_data['mttr_median_days'] = 1.0
    high_score_data['mttr_mad'] = 0.5
    high_score_data['days_since_compatible_release'] = 30
    scored = scorer.score_package(high_score_data)
    assert scored.health_score >= 75
    assert scored.recommendation == "use_existing"

    # Weak signals should yield evaluate or build
    medium_data = sample_package_data.copy()
    medium_data['weekly_downloads'] = 1000
    medium_data['star_count'] = 100
    medium_data['mttr_median_days'] = 30
    scored = scorer.score_package(medium_data)
    assert scored.recommendation in ["evaluate", "build"]
    assert scored.health_score < 75


def test_reliability_null_states(sample_config, sample_package_data):
    """Test reliability scoring with null states."""
    scorer = PackageScorer(sample_config)

    # Issues disabled
    sample_package_data['mttr_state'] = 'issues_disabled'
    sample_package_data['mttr_median_days'] = None
    scored = scorer.score_package(sample_package_data, explain=True)
    assert scored.score_breakdown.reliability_details['state'] == 'issues_disabled'

    # Low volume healthy
    sample_package_data['mttr_state'] = 'low_volume_healthy'
    scored = scorer.score_package(sample_package_data, explain=True)
    assert scored.score_breakdown.reliability_details['state'] == 'low_volume_healthy'

    # Low volume backlog
    sample_package_data['mttr_state'] = 'low_volume_backlog'
    scored = scorer.score_package(sample_package_data, explain=True)
    assert scored.score_breakdown.reliability_details['state'] == 'low_volume_backlog'


def test_identity_not_verified(sample_config, sample_package_data):
    """Test scoring with identity not verified."""
    scorer = PackageScorer(sample_config)

    sample_package_data['identity_verified'] = False
    scored = scorer.score_package(sample_package_data)

    assert scored.identity_verified is False
    # Package should still be scored but with warning
    assert scored.health_score > 0


def test_score_all_zero_signals(sample_config):
    """Test scoring package with all signals at zero or missing."""
    scorer = PackageScorer(sample_config)
    from datetime import datetime, timezone

    sparse_data = {
        'name': 'sparse-pkg',
        'full_name': 'owner/sparse-pkg',
        'url': 'https://github.com/owner/sparse-pkg',
        'package_name': 'sparse-pkg',
        'registry': 'pypi',
        'language': 'python',
        'weekly_downloads': 0,
        'star_count': 0,
        'fork_count': 0,
        'mttr_median_days': None,
        'mttr_state': 'issues_disabled',
        'first_release_date': datetime(2022, 1, 1, tzinfo=timezone.utc),
    }

    scored = scorer.score_package(sparse_data)

    assert 0 <= scored.health_score <= 100
    assert scored.recommendation in ["use_existing", "evaluate", "build"]


def test_score_brand_new_package(sample_config, sample_package_data):
    """Test that a brand-new package is blended toward neutral (50)."""
    from datetime import datetime, timedelta, timezone
    scorer = PackageScorer(sample_config)

    new_data = sample_package_data.copy()
    new_data['first_release_date'] = datetime.now(timezone.utc) - timedelta(days=30)

    scored = scorer.score_package(new_data)

    # Confidence ≈ 0 → health_score ≈ 50 regardless of signals
    assert 40 <= scored.health_score <= 60


def test_score_first_release_date_as_string(sample_config, sample_package_data):
    """Test that first_release_date as an ISO string is parsed correctly."""
    scorer = PackageScorer(sample_config)

    string_data = sample_package_data.copy()
    string_data['first_release_date'] = '2011-02-13T00:00:00+00:00'

    scored = scorer.score_package(string_data)

    assert scored.age_years > 10
    assert scored.health_score > 0


@pytest.mark.parametrize("downloads,stars", [
    (0, 0),
    (0, 100),
    (1000, 0),
    (10_000_000, 500_000),  # At or above saturation
])
def test_score_extreme_adoption_signals(sample_config, sample_package_data, downloads, stars):
    """Test scoring does not crash or produce out-of-range values for extreme adoption signals."""
    scorer = PackageScorer(sample_config)

    data = sample_package_data.copy()
    data['weekly_downloads'] = downloads
    data['star_count'] = stars

    scored = scorer.score_package(data)

    assert 0 <= scored.health_score <= 100


def test_floor_filter_star_fallback(sample_config):
    """Floor filter uses star count when downloads are 0."""
    scorer = PackageScorer(sample_config)

    # Above star threshold (min_stars=50), no downloads
    passes = [{'name': 'starred', 'weekly_downloads': 0, 'star_count': 100}]
    assert len(scorer.apply_floor_filter(passes)) == 1

    # Below star threshold and no downloads
    fails = [{'name': 'obscure', 'weekly_downloads': 0, 'star_count': 10}]
    assert len(scorer.apply_floor_filter(fails)) == 0


def test_versioning_incompatible(sample_config, sample_package_data):
    """version_compatible=False uses is_current_partial_score."""
    scorer = PackageScorer(sample_config)

    compatible_data = sample_package_data.copy()
    compatible_data['version_compatible'] = True
    scored_compat = scorer.score_package(compatible_data, explain=True)

    incompatible_data = sample_package_data.copy()
    incompatible_data['version_compatible'] = False
    scored_incompat = scorer.score_package(incompatible_data, explain=True)

    # Incompatible version should score lower in versioning
    assert scored_incompat.score_breakdown.versioning < scored_compat.score_breakdown.versioning


def test_abandonment_early_warning_ratio(sample_config, sample_package_data):
    """Between early_warning and dormant, high open/closed ratio triggers abandonment."""
    scorer = PackageScorer(sample_config)

    data = sample_package_data.copy()
    data['days_since_last_commit'] = 400  # Between 365 (early_warning) and 540 (dormant)
    data['open_issue_count'] = 500
    data['closed_issues_last_year'] = 50  # Ratio = 10.0 > threshold 2.0

    scored = scorer.score_package(data)
    assert scored.likely_abandoned is True


def test_age_confidence_created_at_fallback(sample_config, sample_package_data):
    """When first_release_date is missing, uses created_at as fallback."""
    from datetime import datetime, timezone
    scorer = PackageScorer(sample_config)

    data = sample_package_data.copy()
    data['first_release_date'] = None
    data['created_at'] = datetime(2015, 1, 1, tzinfo=timezone.utc)

    scored = scorer.score_package(data)
    assert scored.age_years > 5.0


def test_age_confidence_naive_datetime(sample_config, sample_package_data):
    """Naive datetime for first_release_date gets timezone added."""
    from datetime import datetime
    scorer = PackageScorer(sample_config)

    data = sample_package_data.copy()
    data['first_release_date'] = datetime(2010, 1, 1)  # Naive

    scored = scorer.score_package(data)
    assert scored.age_years > 10.0


def test_reliability_measured_no_median(sample_config, sample_package_data):
    """Measured mttr_state with None median returns no_data."""
    scorer = PackageScorer(sample_config)

    data = sample_package_data.copy()
    data['mttr_state'] = 'measured'
    data['mttr_median_days'] = None

    scored = scorer.score_package(data, explain=True)
    assert scored.score_breakdown.reliability_details['state'] == 'no_data'


def test_age_no_dates(sample_config, sample_package_data):
    """No first_release_date or created_at returns 0 age."""
    scorer = PackageScorer(sample_config)

    data = sample_package_data.copy()
    data['first_release_date'] = None
    data.pop('created_at', None)

    scored = scorer.score_package(data)
    assert scored.age_years == 0.0


def test_age_unparseable_string(sample_config, sample_package_data):
    """Unparseable date string for first_release_date returns 0 age."""
    scorer = PackageScorer(sample_config)

    data = sample_package_data.copy()
    data['first_release_date'] = 'not-a-date'

    scored = scorer.score_package(data)
    assert scored.age_years == 0.0
