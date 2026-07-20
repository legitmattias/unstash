"""Unit tests for the search filter-clause builder."""

from __future__ import annotations

from unstash.search.service import SearchFilters, _filter_clause


def test_no_filters_produces_empty_clause() -> None:
    clause, params = _filter_clause(SearchFilters())
    assert clause == ""
    assert params == {}
    assert SearchFilters().any is False


def test_mime_filter_only() -> None:
    clause, params = _filter_clause(SearchFilters(mime_type="application/pdf"))
    assert "d.mime_type = :mime_type" in clause
    assert "document_metadata" not in clause
    assert params == {"mime_type": "application/pdf"}


def test_date_range_uses_bind_params_and_defaults_open_bounds() -> None:
    clause, params = _filter_clause(SearchFilters(date_from="2022-01-01"))
    assert "document_metadata" in clause
    assert ":date_from" in clause
    assert ":date_to" in clause
    # Missing upper bound defaults to an open bound, still a bind param.
    assert params["date_from"] == "2022-01-01"
    assert params["date_to"] == "9999-12-31"


def test_combined_filters_compose() -> None:
    clause, params = _filter_clause(
        SearchFilters(mime_type="text/markdown", date_from="2022-01-01", date_to="2022-12-31"),
    )
    assert "d.mime_type = :mime_type" in clause
    assert "document_metadata" in clause
    assert params == {
        "mime_type": "text/markdown",
        "date_from": "2022-01-01",
        "date_to": "2022-12-31",
    }


def test_user_values_never_appear_in_clause_text() -> None:
    # The clause is fixed fragments only; a hostile value must stay in params.
    hostile = "'; DROP TABLE documents; --"
    clause, params = _filter_clause(SearchFilters(mime_type=hostile))
    assert hostile not in clause
    assert params["mime_type"] == hostile
