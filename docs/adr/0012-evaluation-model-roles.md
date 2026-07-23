# ADR 0012: Evaluation Model Roles

## Status

Accepted.

## Context

With the first LLM call in the product path (cluster labeling), model outputs start to appear in places that evaluations may later grade — and LLMs are increasingly attractive as graders themselves (label quality, answer faithfulness, refusal correctness).

Using the same model — or the same model family — to both produce and judge output is a known failure mode: models systematically prefer their own outputs (self-preference bias), and family-shared training data produces correlated blind spots. An evaluation pipeline that quietly grades Mistral output with a Mistral judge would report inflated quality with no visible warning sign.

This decision fixes the role boundaries before any LLM grades or generates over the corpus, so they are constraints on future work rather than retrofits.

## Decision

Three roles, fixed per evaluation run and recorded in its report:

| Role | Definition | Current assignment |
|---|---|---|
| **Generator** | Produces product-path output being evaluated (cluster labels; later, classification fallback) | Mistral Small (current version, via the config-switched backend) |
| **Answerer** | Produces answers over retrieved context (answer synthesis) | Unassigned — synthesis is deferred |
| **Judge** | Grades generator/answerer output in evaluations | Unassigned — candidate: a self-hosted open judge model (Prometheus-2 7B class) |

Constraints:

1. **The judge must not share a model family with the generator or answerer it grades.** A Mistral generator is never judged by a Mistral judge.
2. **Human judgment remains the gold standard.** Golden-set relevance grades and label-quality reference judgments are human-made; an LLM judge extends coverage between human passes, it does not replace them.
3. **Judged output never feeds training or retraining automatically.** Classifier retraining and label corrections go through human review; an LLM judge's verdict is a signal, not a write path.
4. **Every evaluation report names its models per role** (and versions), so a role-constraint violation is visible in the artifact itself.

## Consequences

- Adopting an LLM judge later requires standing up a second, unrelated model — a real cost, accepted deliberately to keep evaluation independent of the thing evaluated.
- Provider changes on the product path (e.g. a different generator) force a judge-independence re-check, made visible by the per-report role listing.
- Until a judge is assigned, LLM-output quality checks are human-only; evaluation throughput is bounded by that, which is proportionate at pilot scale.
