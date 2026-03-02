"""
Repository ingestion with monorepo detection and AST extraction.

Implements two-pass ingestion:
1. Whole files up to char budget
2. AST extraction for oversized files
"""

import re
import logging
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Set
from dataclasses import dataclass
from urllib.parse import urlparse
import tempfile
import shutil

from git import Repo, GitCommandError

from .ast_extract import InterfaceExtractor

logger = logging.getLogger(__name__)


@dataclass
class IngestionResult:
    """Result of repository ingestion."""

    content: str
    files_included: List[str]
    files_skipped: List[str]
    total_chars: int
    monorepo_warning: bool = False
    content_warnings: List[str] = None

    def __post_init__(self):
        if self.content_warnings is None:
            self.content_warnings = []


class RepositoryIngester:
    """Ingests repository content with smart prioritization."""

    # Files/directories to always skip
    SKIP_PATTERNS = [
        'tests/', 'test/', '__tests__/', 'spec/',
        'fixtures/', 'examples/',
        'node_modules/', 'vendor/', 'dist/', 'build/',
        '.git/', '.github/', '.gitlab/',
        '*.min.js', '*.bundle.js',
        '*.map', '*.lock',
        'package-lock.json', 'yarn.lock', 'Cargo.lock', 'poetry.lock',
    ]

    # Monorepo indicators
    MONOREPO_INDICATORS = [
        'packages/', 'workspaces/', 'modules/',
        'pnpm-workspace.yaml', 'lerna.json', 'nx.json',
        'turbo.json', 'rush.json'
    ]

    # Prompt injection patterns to scan for
    INJECTION_PATTERNS = [
        r'IGNORE PREVIOUS',
        r'SYSTEM:',
        r'\[INST\]',
        r'<<<OVERRIDE',
        r'###INSTRUCTION',
        r'DISREGARD ALL',
        r'NEW INSTRUCTIONS',
    ]

    def __init__(self, char_budget: int = 24000, max_repo_mb: int = 100, timeout_seconds: int = 30):
        """Initialize ingester.

        Args:
            char_budget: Maximum characters to include
            max_repo_mb: Maximum repository size in MB
            timeout_seconds: Timeout for git operations
        """
        self.char_budget = char_budget
        self.max_repo_mb = max_repo_mb
        self.timeout_seconds = timeout_seconds
        self.extractor = InterfaceExtractor()

    def ingest(self, github_url: str, priority_files: Optional[List[str]] = None) -> IngestionResult:
        """Ingest a GitHub repository.

        Args:
            github_url: GitHub repository URL
            priority_files: File patterns to prioritize

        Returns:
            IngestionResult with extracted content
        """
        # Validate URL
        if not self._validate_url(github_url):
            raise ValueError(f"Invalid GitHub URL: {github_url}")

        # Clone to temporary directory
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                repo_path = self._clone_repo(github_url, temp_dir)
            except Exception as e:
                raise RuntimeError(f"Failed to clone repository: {e}")

            # Check for monorepo
            is_monorepo, subdir = self._detect_monorepo(repo_path)
            monorepo_warning = False

            if is_monorepo and subdir:
                repo_path = repo_path / subdir
            elif is_monorepo:
                # Can't resolve subdirectory - restrict to README and CHANGELOG
                monorepo_warning = True
                priority_files = ['README*', 'CHANGELOG*']

            # Get file list with priorities
            files = self._get_file_list(repo_path, priority_files)

            # Two-pass ingestion
            result = self._ingest_files(repo_path, files)
            result.monorepo_warning = monorepo_warning

            # Scan for prompt injection in README files
            self._scan_for_injection(result)

            return result

    def _validate_url(self, url: str) -> bool:
        """Validate GitHub URL."""
        pattern = r'^https://github\.com/[^/]+/[^/]+/?$'
        return bool(re.match(pattern, url))

    def _clone_repo(self, url: str, temp_dir: str) -> Path:
        """Clone repository to temporary directory.

        Args:
            url: GitHub repository URL
            temp_dir: Temporary directory path

        Returns:
            Path to cloned repository
        """
        repo_path = Path(temp_dir) / "repo"

        try:
            # Shallow clone for speed
            Repo.clone_from(
                url,
                repo_path,
                depth=1,
                timeout=self.timeout_seconds
            )

            # Check size
            size_mb = self._get_dir_size(repo_path) / (1024 * 1024)
            if size_mb > self.max_repo_mb:
                raise ValueError(f"Repository too large: {size_mb:.1f}MB > {self.max_repo_mb}MB")

            return repo_path

        except GitCommandError as e:
            raise RuntimeError(f"Git clone failed: {e}")

    def _get_dir_size(self, path: Path) -> int:
        """Get directory size in bytes."""
        total = 0
        for item in path.rglob('*'):
            if item.is_file():
                try:
                    total += item.stat().st_size
                except:
                    pass
        return total

    def _detect_monorepo(self, repo_path: Path) -> Tuple[bool, Optional[str]]:
        """Detect if repository is a monorepo and try to resolve subdirectory.

        Returns:
            Tuple of (is_monorepo, subdirectory_path)
        """
        # Check for monorepo indicators
        is_monorepo = False

        for indicator in self.MONOREPO_INDICATORS:
            if '/' in indicator:
                if (repo_path / indicator).exists():
                    is_monorepo = True
                    break
            else:
                if (repo_path / indicator).is_file():
                    is_monorepo = True
                    break

        if not is_monorepo:
            return False, None

        # Try to resolve primary package
        # Check for most common subdirectories
        common_subdirs = ['core', 'lib', 'src', 'main', 'packages/core']

        for subdir in common_subdirs:
            subpath = repo_path / subdir
            if subpath.exists() and (subpath / 'package.json').exists():
                return True, subdir

        # Can't resolve - will need to restrict to root docs
        return True, None

    def _get_file_list(self, repo_path: Path, priority_patterns: Optional[List[str]] = None) -> List[Path]:
        """Get prioritized list of files to ingest.

        Args:
            repo_path: Path to repository
            priority_patterns: File patterns to prioritize

        Returns:
            Sorted list of file paths
        """
        all_files = []

        for item in repo_path.rglob('*'):
            if not item.is_file():
                continue

            # Skip based on patterns
            relative = item.relative_to(repo_path)
            if self._should_skip(relative):
                continue

            all_files.append(item)

        # Sort by priority
        if priority_patterns:
            all_files.sort(key=lambda f: self._priority_score(f, repo_path, priority_patterns))
        else:
            # Default priority: README, types, entry points, markdown
            default_patterns = [
                'README*', '*.pyi', '*.d.ts',
                '__init__.py', 'index.ts', 'index.js', 'lib.rs', 'main.go',
                'CHANGELOG*', '*.md'
            ]
            all_files.sort(key=lambda f: self._priority_score(f, repo_path, default_patterns))

        return all_files

    def _should_skip(self, relative_path: Path) -> bool:
        """Check if file should be skipped."""
        path_str = str(relative_path)

        for pattern in self.SKIP_PATTERNS:
            if pattern.endswith('/'):
                # Directory pattern
                if pattern[:-1] in path_str.split('/'):
                    return True
            elif '*' in pattern:
                # Glob pattern
                if relative_path.match(pattern):
                    return True
            else:
                # Exact match
                if pattern in path_str:
                    return True

        return False

    def _priority_score(self, file_path: Path, repo_path: Path, patterns: List[str]) -> int:
        """Calculate priority score for a file (lower = higher priority)."""
        relative = file_path.relative_to(repo_path)

        for idx, pattern in enumerate(patterns):
            if relative.match(pattern):
                return idx

        # No match - low priority
        return len(patterns) + 100

    def _ingest_files(self, repo_path: Path, files: List[Path]) -> IngestionResult:
        """Ingest files with two-pass approach.

        Pass 1: Include whole files up to budget
        Pass 2: Extract interfaces from oversized files
        """
        included_content = []
        included_files = []
        skipped_files = []
        total_chars = 0

        # Pass 1: Whole files
        for file_path in files:
            relative = file_path.relative_to(repo_path)

            try:
                content = file_path.read_text(encoding='utf-8', errors='ignore')
            except:
                skipped_files.append(str(relative))
                continue

            # Check if it fits
            if total_chars + len(content) <= self.char_budget:
                included_content.append(f"# File: {relative}\n{content}\n")
                included_files.append(str(relative))
                total_chars += len(content)
            else:
                # Try AST extraction
                extracted = self.extractor.extract(file_path, content)

                if total_chars + len(extracted) <= self.char_budget:
                    included_content.append(f"# File: {relative} (interface only)\n{extracted}\n")
                    included_files.append(str(relative))
                    total_chars += len(extracted)
                else:
                    skipped_files.append(str(relative))

            # Stop if we're close to budget
            if total_chars >= self.char_budget * 0.95:
                # Add remaining to skipped
                remaining_idx = files.index(file_path) + 1
                skipped_files.extend([str(f.relative_to(repo_path)) for f in files[remaining_idx:]])
                break

        return IngestionResult(
            content='\n'.join(included_content),
            files_included=included_files,
            files_skipped=skipped_files,
            total_chars=total_chars
        )

    def _scan_for_injection(self, result: IngestionResult) -> None:
        """Scan README files for prompt injection patterns."""
        for pattern in self.INJECTION_PATTERNS:
            matches = re.findall(pattern, result.content, re.IGNORECASE)
            if matches:
                result.content_warnings.append(
                    f"Potential prompt injection pattern detected: {pattern}"
                )

                # Redact the pattern
                result.content = re.sub(
                    pattern,
                    '[REDACTED]',
                    result.content,
                    flags=re.IGNORECASE
                )