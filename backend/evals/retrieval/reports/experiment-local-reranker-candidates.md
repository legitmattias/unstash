# Self-hosted reranker comparison — 40 queries

Rows are `embedder + reranker @candidates`: the reranker reorders that many top fused results. Difference is nDCG@10 minus `bge-m3 + bge-reranker-v2-m3 @20`, with a clustered bootstrap interval and a permutation p-value.

| configuration | nDCG@10 | MRR | diff vs reference [95% CI] | p |
|---|---|---|---|---|
| granite-311m-r2 + none | 0.698 | 0.716 | -0.078 [-0.179, +0.021] | 0.127 |
| bge-m3 + none | 0.680 | 0.671 | -0.096 [-0.182, -0.013] | 0.053 |
| harrier-0.6b + none | 0.743 | 0.736 | -0.032 [-0.151, +0.081] | 0.602 |
| granite-311m-r2 + bge-reranker-v2-m3 @20 | 0.772 | 0.808 | -0.004 [-0.043, +0.041] | 0.828 |
| granite-311m-r2 + bge-reranker-v2-m3 @10 | 0.769 | 0.815 | -0.006 [-0.053, +0.041] | 0.817 |
| bge-m3 + bge-reranker-v2-m3 @20 | 0.776 | 0.810 | - | - |
| bge-m3 + bge-reranker-v2-m3 @10 | 0.767 | 0.812 | -0.009 [-0.040, +0.019] | 0.577 |
| harrier-0.6b + bge-reranker-v2-m3 @20 | 0.753 | 0.800 | -0.023 [-0.052, -0.002] | 0.104 |
| harrier-0.6b + bge-reranker-v2-m3 @10 | 0.746 | 0.784 | -0.029 [-0.088, +0.017] | 0.386 |
| granite-311m-r2 + mmarco-mminilm-l12 @20 | 0.769 | 0.779 | -0.007 [-0.072, +0.062] | 0.865 |
| granite-311m-r2 + mmarco-mminilm-l12 @10 | 0.773 | 0.797 | -0.003 [-0.070, +0.061] | 0.936 |
| bge-m3 + mmarco-mminilm-l12 @20 | 0.778 | 0.813 | +0.003 [-0.048, +0.055] | 0.915 |
| bge-m3 + mmarco-mminilm-l12 @10 | 0.787 | 0.829 | +0.012 [-0.048, +0.070] | 0.699 |
| harrier-0.6b + mmarco-mminilm-l12 @20 | 0.758 | 0.782 | -0.017 [-0.089, +0.050] | 0.730 |
| harrier-0.6b + mmarco-mminilm-l12 @10 | 0.762 | 0.778 | -0.013 [-0.093, +0.064] | 0.765 |
