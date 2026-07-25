"""How far do filename and path rules alone get us?

Scores two cheap classifiers against a human-labelled gold set:

- **Tier 1 — deterministic lexicon rules.** Type terms matched against the
  normalised filename and each path segment, weighted by where the match
  occurred (filename beats immediate parent beats distant ancestor).
  Confidence is the normalised top score; abstention is by threshold.
- **Tier 2 — learned filename model.** Character n-grams over the filename
  and path, logistic regression, cross-validated. Reproduces the published
  filename-classification setup on this corpus.

Reports coverage and accuracy across the confidence range, a per-type
breakdown (a global threshold quietly degrades rare types), and an ablation
of filename-only vs path-only vs combined — which decides whether capturing
path is worth prioritising in ingestion.

    python evals/classification/rules_baseline.py --labels labels.jsonl

No API calls; runs in seconds. Aggregates are safe to record, per-document
rows are not.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from taxonomy import ADJUDICATION_ESCAPES, AMBIGUOUS_TERMS, LEXICON, TYPES

# Evidence weights by where a term matched. A type stated in the filename is
# a much stronger claim than the same word in a grandparent folder.
WEIGHT_FILENAME = 1.0
WEIGHT_PARENT = 0.6
WEIGHT_ANCESTOR = 0.35
AMBIGUOUS_FACTOR = 0.5
THRESHOLDS = (0.0, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
_DELIMITERS = re.compile(r"[_\-.,()\[\]{}+]+")


def normalise(text: str) -> str:
    """Lowercase, strip extension noise, delimiters to spaces, collapse space."""
    text = unicodedata.normalize("NFC", text).lower()
    text = _DELIMITERS.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def score_rules(name: str, relpath: str, *, use_filename: bool, use_path: bool) -> dict[str, float]:
    """Weighted evidence per type from lexicon matches."""
    scores: dict[str, float] = defaultdict(float)
    stem = normalise(Path(name).stem)
    segments = [normalise(s) for s in Path(relpath).parts[:-1]]

    zones: list[tuple[str, float]] = []
    if use_filename:
        zones.append((stem, WEIGHT_FILENAME))
    if use_path and segments:
        zones.append((segments[-1], WEIGHT_PARENT))
        zones.extend((seg, WEIGHT_ANCESTOR) for seg in segments[:-1])

    for haystack, weight in zones:
        for doc_type, terms in LEXICON.items():
            for term in terms:
                if normalise(term) in haystack:
                    scores[doc_type] = max(scores[doc_type], weight)
        for term, candidates in AMBIGUOUS_TERMS.items():
            if term in haystack:
                for candidate in candidates:
                    scores[candidate] = max(scores[candidate], weight * AMBIGUOUS_FACTOR)
    return dict(scores)


def predict_rules(
    name: str, relpath: str, *, use_filename: bool = True, use_path: bool = True
) -> tuple[str | None, float]:
    """Top type and its confidence; ``None`` when nothing matched."""
    scores = score_rules(name, relpath, use_filename=use_filename, use_path=use_path)
    if not scores:
        return None, 0.0
    total = sum(scores.values())
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    return ranked[0][0], ranked[0][1] / total


def coverage_table(rows: list[dict], **kwargs) -> list[tuple[float, int, float]]:
    """(threshold, covered, accuracy) over the confidence range."""
    graded = []
    for row in rows:
        predicted, confidence = predict_rules(row["name"], row["relpath"], **kwargs)
        graded.append((predicted, confidence, row["label"]))

    table = []
    for threshold in THRESHOLDS:
        covered = [(p, t) for p, c, t in graded if p is not None and c >= threshold]
        if not covered:
            table.append((threshold, 0, 0.0))
            continue
        correct = sum(1 for p, t in covered if p == t)
        table.append((threshold, len(covered), correct / len(covered)))
    return table


def learned_model(rows: list[dict]) -> None:
    """Cross-validated character n-gram model over filename + path."""
    import numpy as np
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import StratifiedKFold
    from sklearn.pipeline import make_pipeline

    texts = [f"{normalise(r['name'])} {normalise(r['relpath'])}" for r in rows]
    labels = [r["label"] for r in rows]
    counts = Counter(labels)
    keep = [i for i, label in enumerate(labels) if counts[label] >= 3]
    if len(keep) < 20:
        print("  too few labels per class for cross-validation; skipping tier 2")
        return
    texts = [texts[i] for i in keep]
    labels = [labels[i] for i in keep]

    pipeline = make_pipeline(
        TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=2, sublinear_tf=True),
        LogisticRegression(max_iter=2000, class_weight=None, C=2.0),
    )
    splits = min(5, *Counter(labels).values())
    folds = StratifiedKFold(n_splits=splits, shuffle=True, random_state=42)
    confidences: list[float] = []
    correct_flags: list[bool] = []
    for train_idx, test_idx in folds.split(texts, labels):
        pipeline.fit([texts[i] for i in train_idx], [labels[i] for i in train_idx])
        probabilities = pipeline.predict_proba([texts[i] for i in test_idx])
        predicted = pipeline.classes_[np.argmax(probabilities, axis=1)]
        for row_index, test_index in enumerate(test_idx):
            confidences.append(float(np.max(probabilities[row_index])))
            correct_flags.append(bool(predicted[row_index] == labels[test_index]))

    print(f"\n  cross-validated on {len(texts)} labelled documents, {splits} folds")
    print(f"  {'threshold':>9} {'coverage':>9} {'accuracy':>9}")
    for threshold in THRESHOLDS:
        covered = [
            ok for conf, ok in zip(confidences, correct_flags, strict=True) if conf >= threshold
        ]
        if not covered:
            continue
        share = len(covered) / len(confidences)
        print(f"  {threshold:>9.2f} {share:>8.1%} {sum(covered) / len(covered):>9.1%}")


def main(labels_path: Path) -> None:
    rows = [
        json.loads(line)
        for line in labels_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    scored = [r for r in rows if r["label"] not in ADJUDICATION_ESCAPES]
    escapes = len(rows) - len(scored)
    print(
        f"gold set: {len(rows)} labelled, {len(scored)} scoreable, {escapes} adjudication escapes"
    )
    print(f"label distribution: {dict(Counter(r['label'] for r in scored).most_common())}")

    print("\ntier 1 — deterministic lexicon rules (filename + path)")
    print(f"  {'threshold':>9} {'coverage':>9} {'accuracy':>9}")
    for threshold, covered, accuracy in coverage_table(scored):
        print(f"  {threshold:>9.2f} {covered / len(scored):>8.1%} {accuracy:>9.1%}")

    print("\n  ablation at threshold 0.5:")
    for label, kwargs in (
        ("filename only", {"use_filename": True, "use_path": False}),
        ("path only", {"use_filename": False, "use_path": True}),
        ("combined", {"use_filename": True, "use_path": True}),
    ):
        table = {t: (c, a) for t, c, a in coverage_table(scored, **kwargs)}
        covered, accuracy = table[0.5]
        print(f"    {label:<15} coverage {covered / len(scored):>6.1%}   accuracy {accuracy:>6.1%}")

    print("\n  per-type at threshold 0.5 (rare types are where a global gate fails):")
    per_type: dict[str, list[bool]] = defaultdict(list)
    missed: Counter[str] = Counter()
    for row in scored:
        predicted, confidence = predict_rules(row["name"], row["relpath"])
        if predicted is not None and confidence >= 0.5:
            per_type[row["label"]].append(predicted == row["label"])
        else:
            missed[row["label"]] += 1
    for doc_type in TYPES:
        results = per_type.get(doc_type, [])
        total = len(results) + missed[doc_type]
        if total == 0:
            continue
        accuracy = f"{sum(results) / len(results):>6.1%}" if results else "     —"
        print(
            f"    {doc_type:<20} n={total:>3}  covered {len(results) / total:>6.1%}  accuracy {accuracy}"
        )

    print("\ntier 2 — learned character n-gram model over filename + path")
    learned_model(scored)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", type=Path, required=True)
    args = parser.parse_args()
    main(args.labels)
