"""Tests for query and taxonomy mapping."""

from priorart.core.query import QueryMapper


def test_query_mapper_initialization():
    """Test query mapper loads taxonomy."""
    mapper = QueryMapper()
    assert len(mapper.categories) > 0


def test_taxonomy_category_structure():
    """Test taxonomy categories have required fields."""
    mapper = QueryMapper()

    for category in mapper.categories:
        assert hasattr(category, "id")
        assert hasattr(category, "keywords")
        assert hasattr(category, "search_terms")
        assert "default" in category.search_terms


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

    result = mapper.map_query("authentication", "python")

    assert result.matched is True
    assert result.category.id == "authentication"
    assert result.service_note is not None


def test_query_mapping_no_match():
    """Test that vague queries don't match."""
    mapper = QueryMapper(confidence_threshold=0.6)

    result = mapper.map_query("make my app better", "python")

    assert result.matched is False
    assert result.category is None


def test_query_mapping_confidence_threshold():
    """Test confidence threshold filtering."""
    # At 0.9 the same query that passes 0.3 is filtered out
    mapper_strict = QueryMapper(confidence_threshold=0.9)
    strict_result = mapper_strict.map_query("web requests", "python")

    mapper_lenient = QueryMapper(confidence_threshold=0.3)
    lenient_result = mapper_lenient.map_query("web requests", "python")

    assert lenient_result.matched is True
    # Strict threshold must be at least as restrictive as lenient
    if lenient_result.confidence < 0.9:
        assert strict_result.matched is False


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

    assert result_python.matched is True
    assert result_javascript.matched is True
    assert result_python.search_query is not None
    assert result_javascript.search_query is not None


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

    assert response["status"] == "no_taxonomy_match"
    assert "message" in response
    assert "hint" in response


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


def test_map_query_empty_input():
    """Empty string returns no match."""
    mapper = QueryMapper()

    result = mapper.map_query("", "python")

    assert result.matched is False
    assert result.confidence == 0.0


def test_load_custom_taxonomy_path(tmp_path):
    """QueryMapper loads taxonomy from a custom file path."""
    taxonomy_file = tmp_path / "taxonomy.yaml"
    taxonomy_file.write_text("""
categories:
  - id: custom_cat
    keywords: ["custom", "test"]
    search_terms:
      default: "custom test"
    priority_files:
      default: ["*.py"]
""")

    mapper = QueryMapper(taxonomy_path=taxonomy_file)
    assert len(mapper.categories) == 1
    assert mapper.categories[0].id == "custom_cat"


def test_load_bundled_taxonomy_failure():
    """QueryMapper returns empty categories when bundled taxonomy fails."""
    from unittest.mock import patch

    with patch("priorart.core.query.files", side_effect=FileNotFoundError("no data")):
        mapper = QueryMapper(taxonomy_path=None)

    assert mapper.categories == []


def test_load_taxonomy_parse_error(tmp_path):
    """QueryMapper returns empty categories when YAML is invalid."""
    bad_file = tmp_path / "bad.yaml"
    bad_file.write_text("not: valid: yaml: [")

    mapper = QueryMapper(taxonomy_path=bad_file)
    assert mapper.categories == []


def test_score_category_empty_keywords():
    """_score_category returns 0 for category with no keywords."""
    from priorart.core.query import TaxonomyCategory

    mapper = QueryMapper()
    cat = TaxonomyCategory(id="empty", keywords=[], search_terms={}, priority_files={})

    score = mapper._score_category(["http", "client"], cat)
    assert score == 0.0


def test_get_priority_files_unknown_category():
    """get_priority_files returns default for unknown category ID."""
    mapper = QueryMapper()

    patterns = mapper.get_priority_files("nonexistent_category", "python")
    assert patterns == ["README*", "*.md"]
