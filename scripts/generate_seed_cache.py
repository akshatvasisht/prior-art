"""
Generate seed cache for priorart.

Pre-populates cache with top packages from each ecosystem to minimize
cold-start latency on first install.
"""

import asyncio
import hashlib
import logging
import os
import sqlite3
import sys
from pathlib import Path
from typing import List, Dict, Any

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from priorart.core.find_alternatives import find_alternatives
from priorart.core.cache import SQLiteCache

logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)


# Top packages to include in seed cache
# These are high-value packages frequently queried
SEED_PACKAGES = {
    "python": [
        "requests", "httpx", "urllib3", "aiohttp",
        "flask", "fastapi", "django",
        "pytest", "unittest2",
        "pydantic", "marshmallow",
        "sqlalchemy", "psycopg2", "pymongo",
        "redis", "celery",
        "boto3", "google-cloud-storage",
        "numpy", "pandas",
        "click", "argparse",
        "pyyaml", "toml",
        "python-dateutil", "arrow",
        "cryptography", "pyjwt",
        "pillow", "opencv-python",
    ],
    "javascript": [
        "axios", "node-fetch", "got", "superagent",
        "express", "fastify", "koa",
        "react", "vue", "angular",
        "jest", "mocha", "vitest",
        "zod", "yup", "joi",
        "sequelize", "prisma", "mongoose",
        "redis", "bull",
        "aws-sdk", "@google-cloud/storage",
        "lodash", "ramda",
        "commander", "yargs",
        "dotenv", "config",
        "dayjs", "date-fns", "moment",
        "jsonwebtoken", "passport",
        "sharp", "jimp",
    ],
    "typescript": [
        "axios", "node-fetch",
        "express", "fastify",
        "zod", "yup",
        "prisma",
    ],
    "rust": [
        "reqwest", "hyper",
        "actix-web", "axum", "rocket",
        "tokio", "async-std",
        "serde", "serde_json",
        "sqlx", "diesel",
        "redis",
        "aws-sdk-rust",
        "clap", "structopt",
        "toml", "config",
        "chrono", "time",
        "jsonwebtoken",
    ],
    "go": [
        "net/http", "gorilla/mux", "chi", "fiber",
        "gin-gonic/gin", "echo",
        "testify", "gomega",
        "gorm", "sqlx",
        "go-redis/redis",
        "aws/aws-sdk-go",
        "spf13/cobra", "urfave/cli",
        "spf13/viper",
        "golang.org/x/time",
        "dgrijalva/jwt-go",
    ],
}


async def generate_seed_cache(output_path: Path, limit_per_lang: int = 30) -> None:
    """Generate seed cache database.

    Args:
        output_path: Path where seed_cache.db will be written
        limit_per_lang: Maximum packages to cache per language
    """
    github_token = os.environ.get("GITHUB_TOKEN")
    if not github_token:
        logger.error("GITHUB_TOKEN environment variable required")
        sys.exit(1)

    logger.info(f"Generating seed cache at {output_path}")
    logger.info(f"This will take several minutes...")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if output_path.exists():
        output_path.unlink()
        logger.info("Removed existing seed cache")

    cache = SQLiteCache(output_path.parent)
    total_cached = 0
    failed = []

    for language, package_names in SEED_PACKAGES.items():
        logger.info(f"\nProcessing {language} packages...")

        # Use common task descriptions to trigger caching
        task_descriptions = [
            "http client",
            "web framework",
            "testing framework",
            "data validation",
            "database orm",
            "json parser",
            "cli framework",
            "date time handling",
            "authentication",
        ]

        cached_this_lang = 0

        for task in task_descriptions:
            if cached_this_lang >= limit_per_lang:
                break

            try:
                logger.info(f"  Querying: {task}")
                result = find_alternatives(language, task, explain=False)

                if result.get("status") == "success":
                    packages = result.get("packages", [])
                    logger.info(f"    Found {len(packages)} packages")
                    cached_this_lang += len(packages)
                    total_cached += len(packages)
                else:
                    logger.warning(f"    Query failed: {result.get('message')}")

            except Exception as e:
                logger.error(f"    Error querying '{task}': {e}")
                failed.append((language, task, str(e)))

            # Rate limiting
            await asyncio.sleep(2)

        logger.info(f"  Cached {cached_this_lang} {language} packages")

    # Generate summary
    logger.info(f"\n{'='*60}")
    logger.info(f"Seed cache generation complete!")
    logger.info(f"Total packages cached: {total_cached}")
    logger.info(f"Cache location: {output_path}")
    logger.info(f"Cache size: {output_path.stat().st_size / 1024:.2f} KB")

    if failed:
        logger.warning(f"\nFailed queries: {len(failed)}")
        for lang, task, error in failed[:10]:  # Show first 10
            logger.warning(f"  {lang}/{task}: {error}")

    checksum_path = output_path.with_suffix(".db.sha256")
    sha256_hash = hashlib.sha256()

    with open(output_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)

    checksum = sha256_hash.hexdigest()

    with open(checksum_path, "w") as f:
        f.write(f"{checksum}  {output_path.name}\n")

    logger.info(f"Checksum written to: {checksum_path}")
    logger.info(f"SHA256: {checksum}")
    logger.info(f"{'='*60}\n")


def verify_seed_cache(cache_path: Path) -> bool:
    """Verify seed cache integrity against checksum.

    Args:
        cache_path: Path to seed_cache.db

    Returns:
        True if verification passes, False otherwise
    """
    checksum_path = cache_path.with_suffix(".db.sha256")

    if not checksum_path.exists():
        logger.error(f"Checksum file not found: {checksum_path}")
        return False

    # Read expected checksum
    with open(checksum_path, "r") as f:
        expected_checksum = f.read().strip().split()[0]

    # Calculate actual checksum
    sha256_hash = hashlib.sha256()
    with open(cache_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)

    actual_checksum = sha256_hash.hexdigest()

    if actual_checksum == expected_checksum:
        logger.info(f"✓ Seed cache verification passed")
        logger.info(f"  SHA256: {actual_checksum}")
        return True
    else:
        logger.error(f"✗ Seed cache verification FAILED")
        logger.error(f"  Expected: {expected_checksum}")
        logger.error(f"  Actual:   {actual_checksum}")
        return False


async def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Generate seed cache for priorart")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("src/priorart/data/seed_cache.db"),
        help="Output path for seed cache database"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=30,
        help="Maximum packages to cache per language"
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify existing seed cache instead of generating"
    )

    args = parser.parse_args()

    if args.verify:
        if not args.output.exists():
            logger.error(f"Seed cache not found: {args.output}")
            sys.exit(1)

        if verify_seed_cache(args.output):
            sys.exit(0)
        else:
            sys.exit(1)
    else:
        await generate_seed_cache(args.output, args.limit)


if __name__ == "__main__":
    asyncio.run(main())
