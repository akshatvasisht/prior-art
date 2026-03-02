"""
Query construction and taxonomy mapping for package discovery.

Maps task descriptions to curated search terms using taxonomy,
with fail-fast handling for unmatched queries.
"""

import re
import logging
from dataclasses import dataclass
from typing import List, Optional, Dict, Any, Tuple
from pathlib import Path
from importlib.resources import files

import yaml

logger = logging.getLogger(__name__)


@dataclass
class TaxonomyCategory:
    """A category in the package taxonomy."""

    id: str
    keywords: List[str]
    search_terms: Dict[str, str]  # language -> search query
    priority_files: Dict[str, List[str]]  # language -> file patterns
    service_note: Optional[str] = None


@dataclass
class QueryResult:
    """Result of query mapping."""

    matched: bool
    category: Optional[TaxonomyCategory] = None
    search_query: Optional[str] = None
    confidence: float = 0.0
    service_note: Optional[str] = None


class QueryMapper:
    """Maps task descriptions to search queries using taxonomy."""

    def __init__(self, taxonomy_path: Optional[Path] = None, confidence_threshold: float = 0.6):
        """Initialize query mapper.

        Args:
            taxonomy_path: Path to taxonomy.yaml. Uses bundled version if not provided.
            confidence_threshold: Minimum confidence for category match (0-1)
        """
        self.confidence_threshold = confidence_threshold
        self.categories = self._load_taxonomy(taxonomy_path)

    def _load_taxonomy(self, taxonomy_path: Optional[Path]) -> List[TaxonomyCategory]:
        """Load taxonomy from YAML file."""
        if taxonomy_path and taxonomy_path.exists():
            yaml_content = taxonomy_path.read_text()
        else:
            # Load bundled taxonomy
            try:
                yaml_content = files('priorart.data').joinpath('taxonomy.yaml').read_text()
            except Exception as e:
                logger.warning(f"Failed to load bundled taxonomy: {e}")
                return []

        try:
            data = yaml.safe_load(yaml_content)
            categories = []

            for cat_data in data.get('categories', []):
                category = TaxonomyCategory(
                    id=cat_data['id'],
                    keywords=cat_data.get('keywords', []),
                    search_terms=cat_data.get('search_terms', {}),
                    priority_files=cat_data.get('priority_files', {}),
                    service_note=cat_data.get('service_note')
                )
                categories.append(category)

            return categories

        except Exception as e:
            logger.error(f"Error parsing taxonomy: {e}")
            return []

    def map_query(self, task_description: str, language: str) -> QueryResult:
        """Map a task description to a search query.

        Args:
            task_description: Natural language description of the task
            language: Programming language (python, javascript, etc.)

        Returns:
            QueryResult with matched category and search query
        """
        # Normalize and tokenize
        normalized = self._normalize_text(task_description)
        tokens = self._tokenize(normalized)

        if not tokens:
            return QueryResult(
                matched=False,
                confidence=0.0
            )

        # Score each category
        best_match = None
        best_confidence = 0.0

        for category in self.categories:
            confidence = self._score_category(tokens, category)

            if confidence > best_confidence:
                best_confidence = confidence
                best_match = category

        # Check if confidence meets threshold
        if best_confidence >= self.confidence_threshold and best_match:
            # Get language-specific or default search terms
            search_query = best_match.search_terms.get(
                language.lower(),
                best_match.search_terms.get('default', '')
            )

            return QueryResult(
                matched=True,
                category=best_match,
                search_query=search_query,
                confidence=best_confidence,
                service_note=best_match.service_note
            )

        # No match - return fail-fast response
        return QueryResult(
            matched=False,
            confidence=best_confidence
        )

    def _normalize_text(self, text: str) -> str:
        """Normalize text for matching."""
        # Convert to lowercase
        text = text.lower()

        # Remove punctuation except hyphens and underscores
        text = re.sub(r'[^\w\s\-_]', ' ', text)

        # Normalize whitespace
        text = ' '.join(text.split())

        return text

    def _tokenize(self, text: str) -> List[str]:
        """Tokenize text into meaningful terms."""
        # Split on whitespace
        tokens = text.split()

        # Remove common stop words
        stop_words = {
            'a', 'an', 'the', 'is', 'are', 'was', 'were', 'be', 'been',
            'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
            'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by', 'from',
            'i', 'me', 'my', 'we', 'you', 'your', 'it', 'its', 'they',
            'need', 'want', 'like', 'make', 'implement', 'create', 'build', 'use', 'add'
        }

        filtered = [t for t in tokens if t not in stop_words and len(t) > 2]

        # Also include bigrams for compound terms
        bigrams = []
        for i in range(len(tokens) - 1):
            bigram = f"{tokens[i]}_{tokens[i+1]}"
            bigrams.append(bigram)

        return filtered + bigrams

    def _score_category(self, tokens: List[str], category: TaxonomyCategory) -> float:
        """Score how well tokens match a category.

        Returns:
            Confidence score between 0 and 1
        """
        if not tokens or not category.keywords:
            return 0.0

        # Normalize category keywords
        normalized_keywords = []
        for keyword in category.keywords:
            normalized = self._normalize_text(keyword)
            normalized_keywords.append(normalized)

            # Also add individual words from multi-word keywords
            for word in normalized.split():
                if len(word) > 2:
                    normalized_keywords.append(word)

        # Count matches
        matches = 0
        for token in tokens:
            for keyword in normalized_keywords:
                if token in keyword or keyword in token:
                    matches += 1
                    break  # Only count once per token

        # Calculate confidence
        # Weight by both coverage and specificity
        coverage = matches / max(len(tokens), 1)
        specificity = matches / max(len(normalized_keywords), 1)

        # Weighted average favoring coverage
        confidence = 0.7 * coverage + 0.3 * specificity

        return min(confidence, 1.0)

    def get_no_match_response(self) -> Dict[str, Any]:
        """Get structured response for taxonomy miss."""
        return {
            "status": "no_taxonomy_match",
            "message": "Could not confidently map task description to a known package category. Retry with a more specific search term.",
            "hint": "Pass a concrete noun describing the capability — e.g. 'http client', 'jwt parser', 'database migration' — rather than a description of what you are building.",
            "service_note": None
        }

    def get_priority_files(self, category_id: str, language: str) -> List[str]:
        """Get priority file patterns for a category and language.

        Args:
            category_id: Category ID from taxonomy
            language: Programming language

        Returns:
            List of file patterns to prioritize during ingestion
        """
        for category in self.categories:
            if category.id == category_id:
                patterns = category.priority_files.get(
                    language.lower(),
                    category.priority_files.get('default', [])
                )
                return patterns

        return ["README*", "*.md"]  # Default fallback