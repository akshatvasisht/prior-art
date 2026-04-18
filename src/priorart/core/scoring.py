"""
Multi-dimensional package scoring engine.

Implements five scoring dimensions with age confidence multiplier.
All scoring is deterministic - same inputs always produce same outputs.
"""

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ScoreBreakdown:
    """Detailed breakdown of scoring dimensions."""

    reliability: float
    adoption: float
    versioning: float
    activity_regularity: float
    dependency_health: float

    # Sub-scores for explain mode
    reliability_details: dict[str, Any] | None = None
    adoption_details: dict[str, Any] | None = None
    versioning_details: dict[str, Any] | None = None
    regularity_details: dict[str, Any] | None = None
    dependency_details: dict[str, Any] | None = None


@dataclass
class ScoredPackage:
    """A package with complete health scoring."""

    # Identity
    name: str
    full_name: str  # org/repo
    url: str
    package_name: str
    registry: str

    # Basic info
    description: str | None = None
    age_years: float = 0.0
    latest_compatible_version: str | None = None
    version_compatible: bool = True

    # Adoption signals
    weekly_downloads: int | None = None
    reverse_dep_count: int = 0
    fork_to_star_ratio: float | None = None

    # Reliability signals
    mttr_median_days: float | None = None
    mttr_mad: float | None = None
    reliability_state: str = "unknown"
    likely_abandoned: bool = False

    # Other metadata
    license: str | None = None
    license_warning: bool = False
    dep_count: int = 0
    dep_health_flag: bool = False

    # Scoring
    health_score: int = 50
    score_breakdown: ScoreBreakdown | None = None
    recommendation: str = "evaluate"  # use_existing, evaluate, build
    service_note: str | None = None
    identity_verified: bool = True

    # Scorecard signals (optional)
    scorecard_overall: float | None = None

    # Build-vs-borrow lens (optional)
    build_cost_weeks: float | None = None
    commodity_tag: str | None = None  # "commodity" | "differentiator"
    maintenance_liability: str | None = None  # "low" | "medium" | "high"


class PackageScorer:
    """Scores packages based on health signals."""

    # Copyleft licenses that trigger warnings
    COPYLEFT_LICENSES = {
        "gpl",
        "gpl-2.0",
        "gpl-3.0",
        "gplv2",
        "gplv3",
        "agpl",
        "agpl-3.0",
        "agplv3",
        "eupl",
        "eupl-1.2",
        "sspl",
        "sspl-1.0",
    }

    def __init__(self, config: dict[str, Any]):
        """Initialize scorer with configuration.

        Args:
            config: Configuration dictionary from config.yaml
        """
        self.config = config

        # Extract key parameters
        self.weights = config["weights"]
        self.floor_filter = config["floor_filter"]
        self.reliability_config = config["reliability"]
        self.adoption_config = config["adoption"]
        self.versioning_config = config["versioning"]
        self.dependency_config = config["dependency_health"]
        self.confidence_config = config["confidence"]
        self.abandonment_config = config["abandonment"]
        self.recommendation_config = config["recommendation"]

        # Validate weights sum to 1.0
        weight_sum = sum(self.weights.values())
        if abs(weight_sum - 1.0) > 0.01:
            raise ValueError(
                f"Scoring weights must sum to 1.0, got {weight_sum}. "
                "Hint: Check src/priorart/data/config.yaml for correct weight values."
            )

    def apply_floor_filter(self, candidates: list[Any]) -> list[Any]:
        """Apply floor filter to exclude noise packages.

        Args:
            candidates: List of package candidates

        Returns:
            Filtered list excluding packages below thresholds
        """
        filtered = []
        min_downloads = self.floor_filter["min_weekly_downloads"]
        min_stars = self.floor_filter["min_stars"]

        for candidate in candidates:
            # Check download threshold
            downloads = candidate.get("weekly_downloads", 0) or 0

            # If we have download data, use it
            if downloads > 0:
                if downloads >= min_downloads:
                    filtered.append(candidate)
            else:
                # Fallback to star count if no download data
                stars = candidate.get("star_count", 0) or 0
                if stars >= min_stars:
                    filtered.append(candidate)

        logger.info(f"Floor filter: {len(candidates)} -> {len(filtered)} candidates")
        return filtered

    def score_package(self, package_data: dict[str, Any], explain: bool = False) -> ScoredPackage:
        """Score a package based on all available signals.

        Args:
            package_data: Dictionary with all package signals
            explain: Whether to include detailed breakdown

        Returns:
            ScoredPackage with health score and recommendation
        """
        # Calculate individual dimension scores
        reliability_score, reliability_details = self._score_reliability(package_data)
        adoption_score, adoption_details = self._score_adoption(package_data)
        versioning_score, versioning_details = self._score_versioning(package_data)
        regularity_score, regularity_details = self._score_regularity(package_data)
        dependency_score, dependency_details = self._score_dependency(package_data)

        # Calculate raw composite score
        raw_score = (
            self.weights["reliability"] * reliability_score
            + self.weights["adoption"] * adoption_score
            + self.weights["versioning"] * versioning_score
            + self.weights["activity_regularity"] * regularity_score
            + self.weights["dependency_health"] * dependency_score
        )

        # Apply age confidence multiplier
        age_years = self._calculate_age_years(package_data)
        confidence = min(age_years / self.confidence_config["full_trust_age_years"], 1.0)
        neutral_prior = self.confidence_config["neutral_prior"]

        # Blend with neutral prior based on confidence
        final_score = confidence * raw_score + (1 - confidence) * neutral_prior

        # Convert to 0-100 scale
        health_score = int(final_score * 100)

        # Determine recommendation
        recommendation = self._get_recommendation(health_score)

        # Check for abandonment
        likely_abandoned = self._check_abandonment(package_data)

        # Check license
        license_warning = self._check_license(package_data.get("license"))

        # Check dependency health flag
        dep_health_flag = self._check_dep_health_flag(package_data)

        # Create score breakdown if requested
        score_breakdown = None
        if explain:
            score_breakdown = ScoreBreakdown(
                reliability=int(reliability_score * 100),
                adoption=int(adoption_score * 100),
                versioning=int(versioning_score * 100),
                activity_regularity=int(regularity_score * 100),
                dependency_health=int(dependency_score * 100),
                reliability_details=reliability_details,
                adoption_details=adoption_details,
                versioning_details=versioning_details,
                regularity_details=regularity_details,
                dependency_details=dependency_details,
            )

        # Build final scored package
        return ScoredPackage(
            name=package_data.get("name", ""),
            full_name=package_data.get("full_name", ""),
            url=package_data.get("url", ""),
            package_name=package_data.get("package_name", ""),
            registry=package_data.get("registry", ""),
            description=package_data.get("description"),
            age_years=age_years,
            latest_compatible_version=package_data.get("latest_version"),
            version_compatible=package_data.get("version_compatible", True),
            weekly_downloads=package_data.get("weekly_downloads"),
            reverse_dep_count=package_data.get("reverse_dep_count", 0),
            fork_to_star_ratio=package_data.get("fork_to_star_ratio"),
            mttr_median_days=package_data.get("mttr_median_days"),
            mttr_mad=package_data.get("mttr_mad"),
            reliability_state=package_data.get("mttr_state", "unknown"),
            likely_abandoned=likely_abandoned,
            license=package_data.get("license"),
            license_warning=license_warning,
            dep_count=package_data.get("direct_dep_count", 0),
            dep_health_flag=dep_health_flag,
            health_score=health_score,
            score_breakdown=score_breakdown,
            recommendation=recommendation,
            service_note=package_data.get("service_note"),
            identity_verified=package_data.get("identity_verified", True),
            scorecard_overall=package_data.get("scorecard_overall"),
        )

    def _score_reliability(self, data: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        """Score reliability dimension (30% weight).

        Args:
            data: Package signal data

        Returns:
            Tuple of (score, details)
        """
        mttr_state = data.get("mttr_state", "unknown")

        # Handle null states
        null_scores = self.reliability_config["null_state_scores"]

        if mttr_state == "issues_disabled":
            return null_scores["issues_disabled"], {"state": "issues_disabled"}
        elif mttr_state == "low_volume_healthy":
            return null_scores["low_volume_healthy"], {"state": "low_volume_healthy"}
        elif mttr_state == "low_volume_backlog":
            return null_scores["low_volume_backlog"], {"state": "low_volume_backlog"}

        # Measured state - calculate score
        mttr_median = data.get("mttr_median_days")
        mttr_mad = data.get("mttr_mad")

        if mttr_median is None:
            return null_scores["issues_disabled"], {"state": "no_data"}

        # MTTR score: faster response = higher score
        mttr_score = 1.0 / (1.0 + math.log(1.0 + mttr_median))

        # Consistency score: lower variance = higher score
        consistency_score = 1.0
        if mttr_mad is not None and mttr_median > 0:
            consistency_score = 1.0 / (1.0 + mttr_mad / max(mttr_median, 1.0))

        # Combined reliability score
        reliability_score = 0.6 * mttr_score + 0.4 * consistency_score

        details = {
            "state": "measured",
            "mttr_median_days": mttr_median,
            "mttr_mad": mttr_mad,
            "mttr_score": mttr_score,
            "consistency_score": consistency_score,
        }

        # Blend with OpenSSF Scorecard reliability bucket if available (30% weight)
        sc_reliability = data.get("scorecard_reliability_bucket")
        if sc_reliability is not None:
            reliability_score = 0.7 * reliability_score + 0.3 * sc_reliability
            details["scorecard_reliability_bucket"] = sc_reliability

        return reliability_score, details

    def _score_adoption(self, data: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        """Score adoption dimension (20% weight).

        Args:
            data: Package signal data

        Returns:
            Tuple of (score, details)
        """
        language = data.get("language", "default")

        # Fork-to-star ratio (may be None when GitHub signals are missing)
        fsr = data.get("fork_to_star_ratio") or 0
        fsr_reference = self.adoption_config["fsr_reference"].get(
            language.lower(), self.adoption_config["fsr_reference"]["default"]
        )
        fsr_score = min(fsr / fsr_reference, 1.0) if fsr_reference > 0 else 0.0

        # Saturation at 10M/week (~top 0.01% of ecosystem); 0 when unavailable (PyPI fallback to stars)
        weekly_downloads = data.get("weekly_downloads") or 0
        dl_saturation = 10_000_000
        dl_score = min(math.log(1 + weekly_downloads) / math.log(1 + dl_saturation), 1.0)

        # Recent committers
        committers = data.get("recent_committer_count") or 0
        committer_saturation = self.adoption_config["committer_saturation"]
        committer_score = min(committers / committer_saturation, 1.0)

        # Reverse dependencies
        revdeps = data.get("reverse_dep_count") or 0
        revdep_saturation = self.adoption_config["revdep_saturation"]
        revdep_score = min(math.log(1 + revdeps) / math.log(1 + revdep_saturation), 1.0)

        # Combined adoption score
        adoption_score = (
            0.35 * dl_score + 0.25 * fsr_score + 0.20 * revdep_score + 0.20 * committer_score
        )

        details = {
            "dl_score": dl_score,
            "fork_to_star_ratio": fsr,
            "fsr_score": fsr_score,
            "recent_committers": committers,
            "committer_score": committer_score,
            "reverse_dependencies": revdeps,
            "revdep_score": revdep_score,
        }

        return adoption_score, details

    def _score_versioning(self, data: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        """Score versioning dimension (20% weight).

        Args:
            data: Package signal data

        Returns:
            Tuple of (score, details)
        """
        # Recency of compatible version (fall back to 365 days if missing or None)
        days_since_release = data.get("days_since_compatible_release")
        if days_since_release is None:
            days_since_release = 365
        halflife = self.versioning_config["recency_halflife_days"]
        recency_score = 1.0 / (1.0 + days_since_release / halflife)

        # Is compatible version current?
        is_current = 1.0
        if not data.get("version_compatible", True):
            is_current = self.versioning_config["is_current_partial_score"]

        # Release regularity
        release_cv = data.get("release_cv")
        if release_cv is None:
            release_cv = 1.0
        regularity_score = 1.0 / (1.0 + release_cv)

        # API stability
        major_versions_per_year = data.get("major_versions_per_year") or 0
        stability_score = 1.0 / (1.0 + major_versions_per_year)

        # Combined versioning score
        versioning_score = (
            0.35 * recency_score * is_current + 0.35 * regularity_score + 0.30 * stability_score
        )

        details = {
            "days_since_release": days_since_release,
            "recency_score": recency_score,
            "is_current": is_current,
            "release_cv": release_cv,
            "regularity_score": regularity_score,
            "major_versions_per_year": major_versions_per_year,
            "stability_score": stability_score,
        }

        return versioning_score, details

    def _score_regularity(self, data: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        """Score activity regularity dimension (15% weight).

        Args:
            data: Package signal data

        Returns:
            Tuple of (score, details)
        """
        weekly_cv = data.get("weekly_commit_cv")

        if weekly_cv is None:
            return 0.5, {"weekly_commit_cv": None, "score": 0.5}

        regularity_score = 1.0 / (1.0 + weekly_cv)

        details = {"weekly_commit_cv": weekly_cv, "score": regularity_score}

        return regularity_score, details

    def _score_dependency(self, data: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        """Score dependency health dimension (15% weight).

        Args:
            data: Package signal data

        Returns:
            Tuple of (score, details)
        """
        direct_deps = data.get("direct_dep_count") or 0
        vulnerable_deps = data.get("vulnerable_dep_count") or 0
        deprecated_deps = data.get("deprecated_dep_count") or 0

        # Dependency count score (fewer is better)
        dep_count_score = 1.0 / (1.0 + math.log(1.0 + direct_deps))

        # Dependency quality score (penalize vulnerable/deprecated)
        dep_quality_score = 1.0 / (1.0 + deprecated_deps + 2 * vulnerable_deps)

        # Combined dependency score
        dependency_score = 0.40 * dep_count_score + 0.60 * dep_quality_score

        details = {
            "direct_dep_count": direct_deps,
            "vulnerable_dep_count": vulnerable_deps,
            "deprecated_dep_count": deprecated_deps,
            "dep_count_score": dep_count_score,
            "dep_quality_score": dep_quality_score,
        }

        # Blend Scorecard dep-health bucket if available (30% weight)
        sc_dep = data.get("scorecard_dep_health_bucket")
        if sc_dep is not None:
            dependency_score = 0.7 * dependency_score + 0.3 * sc_dep
            details["scorecard_dep_health_bucket"] = sc_dep

        return dependency_score, details

    def _calculate_age_years(self, data: dict[str, Any]) -> float:
        """Calculate package age in years.

        Args:
            data: Package signal data

        Returns:
            Age in years or 0.0 if not found
        """
        first_release = data.get("first_release_date")

        if not first_release:
            created_at = data.get("created_at")
            if created_at:
                first_release = created_at
            else:
                return 0.0

        if isinstance(first_release, str):
            try:
                first_release = datetime.fromisoformat(first_release)
            except Exception:
                return 0.0

        if first_release.tzinfo is None:
            first_release = first_release.replace(tzinfo=timezone.utc)

        age_days = (datetime.now(timezone.utc) - first_release).days
        return age_days / 365.0

    def _get_recommendation(self, health_score: int) -> str:
        """Get recommendation based on health score.

        Args:
            health_score: Composite performance score (0-100)

        Returns:
            Recommendation string: "use_existing", "evaluate", or "build"
        """
        use_min = self.recommendation_config["use_existing_min"]
        evaluate_min = self.recommendation_config["evaluate_min"]

        if health_score >= use_min:
            return "use_existing"
        elif health_score >= evaluate_min:
            return "evaluate"
        else:
            return "build"

    def _check_abandonment(self, data: dict[str, Any]) -> bool:
        """Check if package is likely abandoned.

        Args:
            data: Package signal data

        Returns:
            True if thresholds suggest abandonment, False otherwise
        """
        days_since_commit = data.get("days_since_last_commit")
        if days_since_commit is None:
            return False

        # Check thresholds
        early_warning = self.abandonment_config["early_warning_days"]
        dormant = self.abandonment_config["dormant_days"]

        if days_since_commit > dormant:
            return True

        if days_since_commit > early_warning:
            # Check issue ratio
            open_issues = data.get("open_issue_count", 0)
            closed_last_year = data.get("closed_issues_last_year", 0)

            ratio_threshold = self.abandonment_config["open_to_closed_ratio_threshold"]
            if closed_last_year > 0:
                ratio = open_issues / closed_last_year
                if ratio > ratio_threshold:
                    return True

        return False

    def _check_license(self, license_str: str | None) -> bool:
        """Check if license is copyleft.

        Args:
            license_str: License identifier from registry

        Returns:
            True if copyleft, False otherwise
        """
        if not license_str:
            return False

        license_lower = license_str.lower()
        for copyleft in self.COPYLEFT_LICENSES:
            if copyleft in license_lower:
                return True

        return False

    def _check_dep_health_flag(self, data: dict[str, Any]) -> bool:
        """Check if dependency health flag should be set.

        Args:
            data: Package signal data

        Returns:
            True if issues exceed thresholds, False otherwise
        """
        vulnerable = data.get("vulnerable_dep_count") or 0
        deprecated = data.get("deprecated_dep_count") or 0
        threshold = self.dependency_config["dep_health_deprecated_threshold"]

        return vulnerable > 0 or deprecated > threshold
