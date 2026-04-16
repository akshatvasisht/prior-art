"""
CLI interface for priorart package discovery tool.
"""

import json
import logging
import os
import sys

import click

from . import __version__
from .core.find_alternatives import find_alternatives
from .core.ingest_repo import ingest_repo

# Set up logging
logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")


@click.group()
@click.version_option(__version__, package_name="priorart")
def cli() -> None:
    """priorart - Build-vs-borrow intelligence for agentic workflows.

    Discover and evaluate open source packages to make informed
    decisions about whether to build custom solutions or use
    existing libraries.
    """
    pass


@cli.command()
@click.option(
    "--language",
    "-l",
    required=True,
    type=click.Choice(["python", "javascript", "typescript", "go", "rust"], case_sensitive=False),
    help="Programming language",
)
@click.option(
    "--task", "-t", required=True, help='Task description (e.g., "http client", "jwt parser")'
)
@click.option("--explain", "-e", is_flag=True, help="Include detailed scoring breakdown")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def find(language: str, task: str, explain: bool, output_json: bool, verbose: bool) -> None:
    """Find alternative packages for a given task.

    Args:
        language: Programming language (python, javascript, typescript, go, rust)
        task: Natural language description of the capability needed
        explain: Whether to include detailed scoring breakdown
        output_json: Whether to output results as JSON
        verbose: Enable verbose logging

    Examples:
      priorart find --language python --task "http client"
      priorart find -l javascript -t "rate limiter" --explain
      priorart find -l rust -t "json parser" --json
    """
    if verbose:  # pragma: no cover
        logging.getLogger().setLevel(logging.INFO)

    try:
        result = find_alternatives(language, task, explain=explain)

        if output_json:
            click.echo(json.dumps(result, indent=2))
        else:
            _print_find_results(result)

    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("repo_url")
@click.option(
    "--language",
    "-l",
    type=click.Choice(["python", "javascript", "typescript", "go", "rust"], case_sensitive=False),
    help="Programming language for prioritization",
)
@click.option("--category", "-c", help="Package category for file prioritization")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def ingest(
    repo_url: str, language: str | None, category: str | None, output_json: bool, verbose: bool
) -> None:
    """Ingest a GitHub repository to understand its public interface.

    Args:
        repo_url: URL of the GitHub repository to ingest
        language: Optional programming language for prioritization
        category: Optional package category for file prioritization
        output_json: Whether to output results as JSON
        verbose: Enable verbose logging

    Examples:
      priorart ingest https://github.com/psf/requests
      priorart ingest https://github.com/axios/axios --language javascript
    """
    if verbose:  # pragma: no cover
        logging.getLogger().setLevel(logging.INFO)

    try:
        result = ingest_repo(repo_url, language, category)

        if output_json:
            click.echo(json.dumps(result, indent=2))
        else:
            _print_ingest_results(result)

    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command()
def cache_clear() -> None:
    """Clear the local package cache."""
    from pathlib import Path

    from platformdirs import user_cache_dir

    try:
        cache_dir = Path(user_cache_dir("priorart"))
        cache_file = cache_dir / "cache.db"

        if cache_file.exists():
            cache_file.unlink()
            click.echo(f"Cache cleared: {cache_file}")
        else:
            click.echo("Cache already empty")

    except Exception as e:
        click.echo(f"Error clearing cache: {e}", err=True)
        sys.exit(1)


@cli.command()
def cache_info() -> None:
    """Show cache information."""
    import sqlite3
    from pathlib import Path

    from platformdirs import user_cache_dir

    try:
        cache_dir = Path(user_cache_dir("priorart"))
        cache_file = cache_dir / "cache.db"

        if not cache_file.exists():
            click.echo("Cache is empty (no database file)")
            return

        with sqlite3.connect(cache_file, timeout=10) as conn:
            count = conn.execute("SELECT COUNT(*) FROM package_signals").fetchone()[0]

        size_mb = cache_file.stat().st_size / (1024 * 1024)

        click.echo(f"Cache location: {cache_file}")
        click.echo(f"Cached packages: {count}")
        click.echo(f"Cache size: {size_mb:.2f} MB")

    except Exception as e:
        click.echo(f"Error reading cache: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option("--limit", default=30, type=int, help="Maximum packages to cache per language")
def seed_generate(limit: int) -> None:  # pragma: no cover
    """Generate seed cache with top packages from each ecosystem.

    Pre-populates the cache to minimize cold-start latency.
    Requires GITHUB_TOKEN environment variable.
    """
    import subprocess
    from pathlib import Path

    script_path = Path(__file__).parent.parent.parent / "scripts" / "generate_seed_cache.py"

    if not script_path.exists():
        click.echo(f"Error: Seed generation script not found at {script_path}", err=True)
        sys.exit(1)

    if not os.environ.get("GITHUB_TOKEN"):
        click.echo("Error: GITHUB_TOKEN environment variable required", err=True)
        sys.exit(1)

    click.echo("Generating seed cache...")
    click.echo("This will take several minutes...\n")

    try:
        subprocess.run(
            [sys.executable, str(script_path), "--limit", str(limit)],
            check=True,
            env=os.environ.copy(),
        )
        click.echo("\nSeed cache generated successfully!")
    except subprocess.CalledProcessError as e:
        click.echo(f"\nError generating seed cache: {e}", err=True)
        sys.exit(1)


def _print_non_success(result: dict) -> None:
    """Print status and message for any non-success response."""
    click.echo(f"Status: {result.get('status')}")
    click.echo(f"Message: {result.get('message', 'Unknown error')}")
    if result.get("service_note"):
        click.echo(f"\nNote: {result['service_note']}")


def _print_find_results(result: dict) -> None:
    """Print find_alternatives results in human-readable format.

    Args:
        result: Dictionary of results from find_alternatives
    """
    status = result.get("status")

    if status != "success":
        _print_non_success(result)
        return

    packages = result.get("packages", [])
    click.echo(f"\nFound {len(packages)} packages:\n")

    for idx, pkg in enumerate(packages, 1):
        click.echo(f"{idx}. {pkg['name']}")
        click.echo(f"   URL: {pkg['url']}")
        click.echo(f"   Health Score: {pkg['health_score']}/100")
        click.echo(f"   Recommendation: {pkg['recommendation']}")

        if pkg.get("description"):
            desc = (
                pkg["description"][:100] + "..."
                if len(pkg["description"]) > 100
                else pkg["description"]
            )
            click.echo(f"   Description: {desc}")

        if pkg.get("weekly_downloads"):
            click.echo(f"   Weekly Downloads: {pkg['weekly_downloads']:,}")

        if pkg.get("license"):
            license_str = pkg["license"]
            if pkg.get("license_warning"):
                license_str += " (copyleft)"
            click.echo(f"   License: {license_str}")

        # Warnings
        warnings = []
        if not pkg.get("identity_verified"):
            warnings.append("Identity not verified")
        if pkg.get("likely_abandoned"):
            warnings.append("Likely abandoned")
        if pkg.get("dep_health_flag"):
            warnings.append("Dependency health issues")

        if warnings:
            click.echo(f"   Warnings: {', '.join(warnings)}")

        # Score breakdown if available
        if pkg.get("score_breakdown"):
            breakdown = pkg["score_breakdown"]
            click.echo("   Score Breakdown:")
            click.echo(f"     Reliability: {breakdown['reliability']}")
            click.echo(f"     Adoption: {breakdown['adoption']}")
            click.echo(f"     Versioning: {breakdown['versioning']}")
            click.echo(f"     Activity: {breakdown['activity_regularity']}")
            click.echo(f"     Dependencies: {breakdown['dependency_health']}")

        click.echo()

    if result.get("service_note"):
        click.echo(f"Note: {result['service_note']}\n")


def _print_ingest_results(result: dict) -> None:
    """Print ingest_repo results in human-readable format.

    Args:
        result: Dictionary of results from ingest_repo
    """
    status = result.get("status")

    if status != "success":
        _print_non_success(result)
        return

    click.echo("\nRepository ingestion complete")
    click.echo(f"Total characters: {result['total_chars']:,}")
    click.echo(f"Files included: {len(result['files_included'])}")
    click.echo(f"Files skipped: {len(result['files_skipped'])}")

    if result.get("monorepo_warning"):
        click.echo(f"\nWarning: {result.get('message', 'Monorepo detected')}")

    if result.get("content_warnings"):
        click.echo("\nSecurity warnings:")
        for warning in result["content_warnings"]:
            click.echo(f"   - {warning}")

    click.echo("\n--- Content ---\n")

    # Truncate content for display if too long
    content = result["content"]
    if len(content) > 5000:
        click.echo(content[:5000])
        click.echo(f"\n... (truncated, {len(content) - 5000:,} more characters)")
    else:
        click.echo(content)


def main() -> None:  # pragma: no cover
    """Main entry point for CLI."""
    cli()


if __name__ == "__main__":  # pragma: no cover
    main()
