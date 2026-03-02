"""Tests for query and taxonomy mapping."""

import pytest
from pathlib import Path

from priorart.core.query import QueryMapper, TaxonomyCategory


def test_query_mapper_initialization():
    """Test query mapper loads taxonomy."""
    mapper = QueryMapper()
    assert len(mapper.categories) > 0


def test_taxonomy_category_structure():
    """Test taxonomy categories have required fields."""
    mapper = QueryMapper()

    for category in mapper.categories:
        assert hasattr(category, 'id')
        assert hasattr(category, 'keywords')
        assert hasattr(category, 'search_terms')
        assert 'default' in category.search_terms


def test_query_mapping_http_client():
    """Test mapping 'http client' query."""
    mapper = QueryMapper(confidence_threshold=0.6)

    result = mapper.map_query("http client", "python")

    assert result.matched is True
    assert result.category.id == "http_client"
    assert result.search_query is not None
    assert "http" in result.search_query.lower()


def test_query_mapping_authentication():
    """Test mapping authentication queries."""
    mapper = QueryMapper(confidence_threshold=0.6)

    # Test various phrasings
    queries = [
        "authentication",
        "user login",
        "oauth integration",
        "jwt auth"
    ]

    for query in queries:
        result = mapper.map_query(query, "python")
        if result.matched:
            assert result.category.id == "authentication"
            assert result.service_note is not None  # Auth has service note


def test_query_mapping_no_match():
    """Test that vague queries don't match."""
    mapper = QueryMapper(confidence_threshold=0.6)

    result = mapper.map_query("make my app better", "python")

    assert result.matched is False
    assert result.category is None


def test_query_mapping_confidence_threshold():
    """Test confidence threshold filtering."""
    # High threshold
    mapper_strict = QueryMapper(confidence_threshold=0.9)
    result = mapper_strict.map_query("web requests", "python")
    # May not match with very high threshold

    # Low threshold
    mapper_lenient = QueryMapper(confidence_threshold=0.3)
    result = mapper_lenient.map_query("web requests", "python")
    # Should match with low threshold
    assert result.matched is True


def test_text_normalization():
    """Test text normalization removes punctuation and normalizes case."""
    mapper = QueryMapper()

    text = "HTTP-Client!! With (Special) Characters..."
    normalized = mapper._normalize_text(text)

    assert "http" in normalized
    assert "client" in normalized
    assert "!" not in normalized
    assert "(" not in normalized


def test_tokenization():
    """Test tokenization removes stop words."""
    mapper = QueryMapper()

    text = "I need to add a database to my application"
    tokens = mapper._tokenize(text)

    # Stop words removed
    assert "need" not in tokens
    assert "the" not in tokens
    assert "my" not in tokens

    # Important words kept
    assert "database" in tokens
    assert "application" in tokens


def test_language_specific_search_terms():
    """Test language-specific search terms."""
    mapper = QueryMapper()

    result_python = mapper.map_query("http client", "python")
    result_javascript = mapper.map_query("http client", "javascript")

    if result_python.matched and result_javascript.matched:
        # Python and JavaScript may have different search terms
        python_query = result_python.search_query
        js_query = result_javascript.search_query

        # Both should be valid
        assert python_query is not None
        assert js_query is not None


def test_priority_files_retrieval():
    """Test getting priority files for a category."""
    mapper = QueryMapper()

    # Get priority files for http_client in Python
    patterns = mapper.get_priority_files("http_client", "python")

    assert isinstance(patterns, list)
    assert len(patterns) > 0


def test_no_match_response():
    """Test structured no-match response."""
    mapper = QueryMapper()

    response = mapper.get_no_match_response()

    assert response['status'] == 'no_taxonomy_match'
    assert 'message' in response
    assert 'hint' in response


def test_category_scoring():
    """Test category confidence scoring."""
    mapper = QueryMapper()

    tokens = ["http", "request", "client", "rest"]

    # Find http_client category
    http_category = None
    for cat in mapper.categories:
        if cat.id == "http_client":
            http_category = cat
            break

    if http_category:
        score = mapper._score_category(tokens, http_category)
        assert score > 0.5  # Should have high confidence


def test_service_note_categories():
    """Test that infrastructure categories have service notes."""
    mapper = QueryMapper()

    infrastructure_categories = ["authentication", "database", "queue", "email"]

    for cat_id in infrastructure_categories:
        for category in mapper.categories:
            if category.id == cat_id:
                # Most infrastructure categories should have service notes
                # (though not all might)
                if cat_id in ["authentication", "database", "queue"]:
                    assert category.service_note is not None