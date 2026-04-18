"""
OpenSSF Scorecard API client.

Fetches automated supply-chain security scores for a GitHub repository and
aggregates check scores into the reliability and dependency-health buckets
consumed by PackageScorer.

API reference: https://api.scorecard.dev/ (no auth required, CDN-cached,
weekly update cadence). Score of -1 on a check means "not applicable / unable
to evaluate" — treat as skipped, not zero.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.scorecard.dev"

RELIABILITY_CHECKS = {
    "Code-Review",
    "CI-Tests",
    "Fuzzing",
    "SAST",
    "Signed-Releases",
    "Branch-Protection",
    "Maintained",
    "Security-Policy",
}

DEP_HEALTH_CHECKS = {
    "Dependency-Update-Tool",
    "Pinned-Dependencies",
    "Vulnerabilities",
    "SBOM",
}


@dataclass
class ScorecardResult:
    """Aggregated Scorecard signals for a single GitHub repo."""

    overall_score: float | None = None  # 0.0 to 10.0
    reliability_bucket: float | None = None  # normalized 0-1
    dep_health_bucket: float | None = None  # normalized 0-1
    checks: dict[str, int] = field(default_factory=dict)
    available: bool = False


class ScorecardClient:
    """Thin client for api.scorecard.dev."""

    def __init__(self, timeout: int = 10):
        self.client = httpx.Client(timeout=timeout)

    def __enter__(self) -> "ScorecardClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.client.close()

    def close(self) -> None:
        self.client.close()

    def fetch(self, owner: str, repo: str) -> ScorecardResult:
        """Fetch Scorecard results. Returns an empty, unavailable result on miss."""
        url = f"{BASE_URL}/projects/github.com/{owner}/{repo}"
        try:
            response = self.client.get(url)
            if response.status_code == 404:
                return ScorecardResult()
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            logger.info(f"Scorecard lookup failed for {owner}/{repo}: {e}")
            return ScorecardResult()

        return self._aggregate(data)

    @staticmethod
    def _aggregate(data: dict[str, Any]) -> ScorecardResult:
        checks_raw = data.get("checks") or []
        checks: dict[str, int] = {}
        for check in checks_raw:
            name = check.get("name")
            score = check.get("score")
            if name is None or score is None:
                continue
            checks[name] = int(score)

        def bucket(names: set[str]) -> float | None:
            values = [checks[n] for n in names if n in checks and checks[n] >= 0]
            if not values:
                return None
            # Scorecard checks are 0-10; normalize to 0-1
            return sum(values) / (len(values) * 10.0)

        overall = data.get("score")
        overall_f: float | None = None
        if isinstance(overall, (int, float)) and overall >= 0:
            overall_f = float(overall)

        return ScorecardResult(
            overall_score=overall_f,
            reliability_bucket=bucket(RELIABILITY_CHECKS),
            dep_health_bucket=bucket(DEP_HEALTH_CHECKS),
            checks=checks,
            available=bool(checks) or overall_f is not None,
        )
