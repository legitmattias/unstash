# Reranker × embedder — 40 queries, fused top-20

| pair | all nDCG@10 | all MRR | keyword | semantic | decision | english |
|---|---|---|---|---|---|---|
| jina-v4 + no rerank | 0.685 | 0.705 | 0.709 | 0.715 | 0.772 | 0.636 |
| jina-v4 + v2-base-multilingual | 0.682 | 0.677 | 0.644 | 0.739 | 0.729 | 0.637 |
| jina-v4 + v3 | 0.784 | 0.809 | 0.842 | 0.813 | 0.832 | 0.768 |
| jina-v5-small + no rerank | 0.730 | 0.723 | 0.761 | 0.800 | 0.583 | 0.696 |
| jina-v5-small + v2-base-multilingual | 0.696 | 0.694 | 0.680 | 0.849 | 0.701 | 0.647 |
| jina-v5-small + v3 | 0.786 | 0.844 | 0.845 | 0.858 | 0.752 | 0.779 |
