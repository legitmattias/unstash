# ADR 0014: Document Type Is Authored and Supervised, Not Discovered by Clustering

## Status

Accepted.

## Context

Automatic organisation of a tenant's archive was designed around unsupervised discovery: cluster document embeddings, label each cluster, treat the labels as the tenant's document types. Evaluation and a survey of the field show that this conflates two different axes and does not match how the problem is solved anywhere in production.

**Type and topic are different axes.** Automatic genre (document type / register / form) identification has been a distinct research area from topic classification since the 1990s, with its own feature families — surface/stylometric cues, layout and structure, genre-marking boilerplate, and filename/metadata — deliberately *excluding* content words. Swedish archival practice states the distinction precisely: Riksarkivet's *verksamhetsbaserad arkivredovisning* defines a **handlingstyp** as "handling som tillkommer genom att en aktivitet genomförs upprepat" — a document arising from a repeatedly performed activity. **Type is the recurring output of a recurring process, not a subject area.** Clustering semantic embeddings measures subject.

**Dense embeddings are trained to suppress the signal we need.** Style/genre evaluation benchmarks report that general-purpose semantic embedding models largely fail to capture style and collapse stylistic distinctions into topical groupings. Independently, pooling results published in 2026 prove that averaging component vectors shifts the result away from every component and *guarantees* a reduction in mean pairwise distance, monotonically worsening with the number and diversity of pooled units. A long, multi-section document therefore dilutes toward a generic centroid while a short one keeps a sharp topical vector — making document length a confound in any clustering over pooled document vectors.

**Layout is the strongest available type signal, and it is already computed.** On the standard document-type benchmark (RVL-CDIP, 16 classes), text-only models reach ~90% accuracy, image-only ~94%, text+layout ~96%. The same benchmark is estimated to carry ~8% label noise, with ~1.7% of documents legitimately multi-label. Classical structural features — page count, text density, table/figure presence, heading structure — are documented as highly indicative of document type. The parser already produces labelled layout elements with bounding boxes; the pipeline discards them.

**No production system discovers types by clustering.** Established document-management systems treat document type as a supervised, per-tenant label seeded by humans: separate per-field classifiers trained only on human-confirmed documents; classifiers that never overwrite a human assignment; abstention when candidates conflict; and deterministic, user-editable path/filename rules at *higher* precedence than the learned model. Commercial document-understanding services require a small number of labelled samples per class and place classification as an explicit layer upstream of extraction. Where LLM assistance has been added, it supplements the classical classifier as a suggestion source rather than replacing it.

**Existing folder structure is intent.** When a tenant's archive is already organised into folders, that structure represents deliberate human classification accumulated over years. Reorganising against it degrades the product rather than improving it.

The in-repo synthetic evaluation is consistent with all of this: clustering quality against ground-truth types plateaus around ARI 0.4 regardless of algorithm or parameters, and classical baselines (K-means, Ward) perform comparably — the ceiling is the representation, not the clustering method.

## Decision

**Document type is authored and supervised. Discovery assists; it does not decide.**

Five layers, in strict precedence order. Nothing at a lower layer overrides anything above it:

1. **Human assignment** — immutable by any automated process.
2. **Path and filename rules** — user-editable glob/regex mappings from path segments and filename patterns to types and facets. Deterministic, inspectable, higher precedence than any model.
3. **Starter taxonomy** — a curated per-vertical set of document types, pruned and extended at onboarding. For the housing-cooperative vertical the types are legally grounded and carry retention periods and visibility rules as attributes.
4. **Classifier** — calibrated logistic regression over a concatenated feature space of structural features, filename/surface features, and a header embedding. Produces **suggestions with an abstain threshold**; low-confidence documents enter a review queue rather than receiving a label.
5. **Discovery** — clustering and folder-graph analysis, which *propose*: groupings of related folders, candidate new types over unclassified residue, and misfile suggestions where a document's content disagrees with its location.

Supporting decisions:

- **Persist the parser's structural output.** A per-document structural feature vector (page count, text density, table and figure ratios, heading depth and frequency, header/footer repetition, scanned flag) is stored at ingest. It costs nothing beyond parsing that already happens and is the highest-value type signal available.
- **A document gets a dedicated type representation** — filename, title and leading content — stored separately from the pooled vector used for topic-flavoured retrieval. Pooled whole-document vectors are not used for type.
- **Folder structure is authoritative by default, weighted by measured coherence.** A folder's internal content similarity determines how much its name is trusted; incoherent folders fall back to content-based classification.
- **Inference performs no model-provider calls.** Bootstrapping a tenant's taxonomy may use a bounded, one-off LLM pass over a stratified sample (taxonomy induction and sample labelling, in the published teacher/student pattern), after which all classification is local and deterministic. Corrections retrain the classifier; taxonomy is versioned and re-induction is an explicit event.
- **Abstention and review are first-class.** Given documented irreducible label noise and legitimate multi-label cases, a "needs review" state is part of the design rather than a fallback.

ADR 0013 (clustering method) is **not superseded**: HDBSCAN with stability-favouring extraction remains the choice for the clustering that remains. Its *scope* narrows to the discovery layer.

## Consequences

- Type quality no longer depends on unsupervised structure emerging from semantic similarity. It depends on signals that actually carry type — path, filename, layout — plus a small supervised component that improves with use.
- A tenant with an organised archive gets their own structure honoured and connected, rather than replaced by inferred categories.
- A tenant with an unorganised archive is served by the discovery layer, which is where clustering genuinely belongs.
- Type-attached retention and visibility rules become expressible, which a purely inferred category set could not support.
- The correction loop gains a natural entry point (the review queue) instead of depending on users noticing wrong categories.
- More moving parts than a single clustering job: a rules layer, a feature store, a classifier, and a review state. The precedence order keeps their interaction predictable, and each layer is independently testable.
- Per-tenant taxonomy bootstrap is a bounded one-off cost; ongoing classification stays free, preserving the cost target as archives grow.
