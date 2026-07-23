"""Re-cluster trigger decision logic."""

from __future__ import annotations

import pytest

from unstash.clustering.service import evaluate_trigger
from unstash.db.models import ClusteringTrigger

_MIN = 50


@pytest.mark.parametrize(
    ("indexed", "last_run", "expected"),
    [
        (0, None, None),
        (49, None, None),
        (50, None, ClusteringTrigger.THRESHOLD),
        (200, None, ClusteringTrigger.THRESHOLD),
        (60, 50, None),
        (99, 50, None),
        (100, 50, ClusteringTrigger.DOUBLING),
        (150, 50, ClusteringTrigger.DOUBLING),
        # An org can shrink below the threshold after clustering; deletions
        # do not re-trigger.
        (30, 50, None),
    ],
)
def test_evaluate_trigger(indexed, last_run, expected):
    assert evaluate_trigger(indexed, last_run, _MIN) == expected
