"""Labeler interface, fake backend, and factory selection."""

from __future__ import annotations

import pytest

from unstash.clustering.labeler import (
    FakeLabeler,
    LabelError,
    LabelRequest,
    get_labeler,
)
from unstash.config import Settings


def _request(keywords: list[str]) -> LabelRequest:
    return LabelRequest(
        keywords=keywords,
        representative_titles=["protokoll-2024-01.md"],
        representative_excerpts=["Styrelsen beslutade om budget."],
        language="sv",
    )


async def test_fake_labeler_derives_from_top_keyword():
    result = await FakeLabeler().label(_request(["protokoll", "styrelse"]))
    assert result.label == "Protokoll"
    assert result.model == "fake"
    assert result.cost == 0.0


async def test_fake_labeler_rejects_empty_keywords():
    with pytest.raises(LabelError):
        await FakeLabeler().label(_request([]))


def test_factory_off_returns_none():
    settings = Settings(labeler_backend="off")
    assert get_labeler(settings) is None


def test_factory_fake_returns_fake():
    settings = Settings(labeler_backend="fake")
    assert isinstance(get_labeler(settings), FakeLabeler)
