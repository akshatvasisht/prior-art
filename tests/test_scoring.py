"""Tests for scoring engine."""

import pytest

from priorart.core.scoring import PackageScorer


def test_scorer_initialization(sample_config):
    """Test scorer initializes with valid config."""
    scorer = PackageScorer(sample_config)
    assert scorer.weights == sample_config['weights']


def test_scorer_rejects_invalid_weights():
    """Test scorer rejects weights that don't sum to 1.0."""
    bad_config = {
        'weights': {
            'reliability': 0.40,
            'adoption': 0.20,
            'versioning': 0.20,
            'activity_regularity': 0.15,
            'dependency_health': 0.15,
        }
    }

    with pytest.raises(ValueError, match="must sum to 1.0"):
        PackageScorer(bad_config)


def test_score_healthy_package(sample_config, sample_package_data):
    """Test scoring a healthy package like requests."""
    scorer = PackageScorer(sample_config)

    scored = scorer.score_package(sample_package_data, explain=True)

    # Requests should score high
    assert scored.health_score >= 75
    assert scored.recommendation == "use_existing"
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
    from datetime import datetime, timedelta
    sample_package_data['first_release_date'] = datetime.utcnow() - timedelta(days=180)

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

    # Test different score ranges by manipulating signals

    # High score package (use_existing)
    high_score_data = sample_package_data.copy()
    high_score_data['weekly_downloads'] = 50000000
    high_score_data['star_count'] = 60000
    scored = scorer.score_package(high_score_data)
    if scored.health_score >= 75:
        assert scored.recommendation == "use_existing"

    # Medium score (evaluate) - reduce signals
    medium_data = sample_package_data.copy()
    medium_data['weekly_downloads'] = 1000
    medium_data['star_count'] = 100
    medium_data['mttr_median_days'] = 30
    scored = scorer.score_package(medium_data)
    # Should be in evaluate or build range
    assert scored.recommendation in ["evaluate", "build"]


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