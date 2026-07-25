# Classification evals

Measures how much of document-type classification the cheap deterministic
layer carries, per ADR 0014's premise that filename and path rules should
handle most volume with the learned classifier serving the remainder.

## Gold-set discipline

- **Labels are collected blind.** `label_sample.py` never shows a rule
  prediction. The gold set scores the rules; pre-filling it with those same
  rules would contaminate the measurement.
- **Two adjudication escapes are first-class.** `osaker` (genuinely
  ambiguous) and `flera` (several types apply) are excluded from scoring
  rather than counted as failures — real archives contain both, and
  published benchmarks put legitimate multi-label cases near 2%.
- **The label file stays local.** It contains real filenames and paths.
  Only aggregates belong in reports.
- **Version the gold set** alongside the taxonomy: changing `taxonomy.py`
  invalidates comparisons against labels collected under the old set.

## Usage

```
python evals/classification/label_sample.py --corpus-dir /path/to/docs --out labels.jsonl
python evals/classification/rules_baseline.py --labels labels.jsonl
```

`taxonomy.py` holds the starter type set and surface lexicon for the
housing-cooperative vertical; both are authored, and terms that legitimately
signal several types are declared as ambiguous so the scorer's margin, not
its top score alone, decides whether to abstain.
