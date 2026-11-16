# Self-hosted reranker comparison — 40 queries, fused top-20

Difference is nDCG@10 minus `jina-v4 + jina-reranker-v3` (the production pair), with a clustered bootstrap interval and a permutation p-value.

| embedder + reranker | nDCG@10 | MRR | diff vs production [95% CI] | p |
|---|---|---|---|---|
| jina-v4 + none | 0.705 | 0.749 | -0.076 [-0.169, +0.003] | 0.068 |
| harrier-0.6b + none | 0.743 | 0.736 | -0.038 [-0.169, +0.084] | 0.585 |
| qwen3-0.6b + none | 0.717 | 0.745 | -0.065 [-0.172, +0.052] | 0.299 |
| granite-311m-r2 + none | 0.698 | 0.716 | -0.084 [-0.182, +0.001] | 0.074 |
| bge-m3 + none | 0.680 | 0.671 | -0.101 [-0.214, +0.019] | 0.117 |
| jina-v4 + jina-reranker-v3 | 0.781 | 0.819 | - | - |
| harrier-0.6b + jina-reranker-v3 | 0.754 | 0.809 | -0.027 [-0.114, +0.077] | 0.584 |
| qwen3-0.6b + jina-reranker-v3 | 0.792 | 0.855 | +0.011 [-0.076, +0.118] | 0.823 |
| granite-311m-r2 + jina-reranker-v3 | 0.775 | 0.810 | -0.006 [-0.104, +0.099] | 0.906 |
| bge-m3 + jina-reranker-v3 | 0.774 | 0.851 | -0.008 [-0.105, +0.099] | 0.887 |
| jina-v4 + bge-reranker-v2-m3 | 0.726 | 0.763 | -0.055 [-0.123, +0.005] | 0.097 |
| harrier-0.6b + bge-reranker-v2-m3 | 0.753 | 0.800 | -0.029 [-0.133, +0.080] | 0.607 |
| qwen3-0.6b + bge-reranker-v2-m3 | 0.750 | 0.806 | -0.031 [-0.128, +0.080] | 0.558 |
| granite-311m-r2 + bge-reranker-v2-m3 | 0.772 | 0.808 | -0.010 [-0.101, +0.089] | 0.840 |
| bge-m3 + bge-reranker-v2-m3 | 0.776 | 0.810 | -0.006 [-0.109, +0.099] | 0.925 |
| jina-v4 + qwen3-reranker-0.6b | 0.685 | 0.693 | -0.096 [-0.172, -0.023] | 0.016 |
| harrier-0.6b + qwen3-reranker-0.6b | 0.729 | 0.743 | -0.052 [-0.166, +0.067] | 0.390 |
| qwen3-0.6b + qwen3-reranker-0.6b | 0.703 | 0.747 | -0.079 [-0.194, +0.051] | 0.236 |
| granite-311m-r2 + qwen3-reranker-0.6b | 0.729 | 0.755 | -0.052 [-0.164, +0.063] | 0.376 |
| bge-m3 + qwen3-reranker-0.6b | 0.725 | 0.750 | -0.057 [-0.176, +0.064] | 0.358 |
