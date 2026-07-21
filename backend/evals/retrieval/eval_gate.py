"""Eval-gated CI: deterministic nDCG regression gate.

Runs the retrieval harness in deterministic (fake-embedder) mode and compares
the production ``rrf`` config against a pinned baseline. A metric dropping more
than the tolerance fails the build. Fake vectors carry no semantics, so this
gate catches regressions in the retrieval *pipeline* (BM25 indexing, fusion,
best-chunk reduction) — not embedding quality, which belongs to the live tier.

Usage:
    python eval_gate.py --calibrate 5   # measure run-to-run spread, write baseline
    python eval_gate.py                 # gate: exit non-zero on regression
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "src"))

from run_eval import run

BASELINE = HERE / "baseline-deterministic.json"
GATED_CONFIG = "rrf"
GATED_METRICS = ("all/ndcg@10", "all/recall@10", "all/mrr")
# A small fixed margin above the measured spread, absorbing floating-point /
# tie-order noise across platforms. Flaky *queries* are surfaced for quarantine,
# never hidden by inflating this.
TOLERANCE_MARGIN = 0.01


async def _measure() -> dict[str, float]:
    tmp = HERE / ".gate-metrics.json"
    await run("fake", None, "icu", tmp.name)
    data = json.loads(tmp.read_text())
    tmp.unlink()
    return {metric: float(data[GATED_CONFIG][metric]) for metric in GATED_METRICS}


async def calibrate(runs: int) -> None:
    measurements = [await _measure() for _ in range(runs)]
    metrics: dict[str, float] = {}
    spread = 0.0
    for metric in GATED_METRICS:
        values = [m[metric] for m in measurements]
        metrics[metric] = round(min(values), 4)  # baseline is the worst observed
        spread = max(spread, max(values) - min(values))
    tolerance = round(spread + TOLERANCE_MARGIN, 4)
    BASELINE.write_text(
        json.dumps(
            {
                "config": GATED_CONFIG,
                "embedder": "fake",
                "runs": runs,
                "observed_spread": round(spread, 4),
                "tolerance": tolerance,
                "metrics": metrics,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"baseline written: observed_spread={spread:.4f} tolerance={tolerance}")
    if spread > TOLERANCE_MARGIN:
        print(
            f"WARNING: run-to-run spread {spread:.4f} exceeds the margin — the harness is "
            "nondeterministic; quarantine the flaky queries rather than trusting the gate.",
            file=sys.stderr,
        )


async def gate() -> int:
    if not BASELINE.exists():
        print("no baseline; run --calibrate first", file=sys.stderr)
        return 2
    baseline = json.loads(BASELINE.read_text())
    tolerance = float(baseline["tolerance"])
    current = await _measure()

    print(f"{'metric':<16}{'baseline':>10}{'current':>10}{'delta':>10}")
    failed = False
    for metric in GATED_METRICS:
        base = float(baseline["metrics"][metric])
        now = current[metric]
        delta = now - base
        marker = "  REGRESSION" if delta < -tolerance else ""
        if marker:
            failed = True
        print(f"{metric:<16}{base:>10.4f}{now:>10.4f}{delta:>+10.4f}{marker}")

    if failed:
        print(f"\nFAIL: a metric dropped more than the tolerance ({tolerance}).", file=sys.stderr)
        return 1
    print(f"\nPASS: no regression beyond tolerance ({tolerance}).")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--calibrate",
        type=int,
        default=0,
        metavar="N",
        help="run N times, measure spread, and write the baseline",
    )
    args = parser.parse_args()
    if args.calibrate:
        asyncio.run(calibrate(args.calibrate))
    else:
        sys.exit(asyncio.run(gate()))
