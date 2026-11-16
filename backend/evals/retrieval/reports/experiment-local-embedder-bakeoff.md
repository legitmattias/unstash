# Self-hosted embedder bake-off — 40 queries, 272 chunks, CPU 8 threads

Fused = weighted RRF with the shared stemmed BM25 leg. The difference column is fused nDCG@10 minus the baseline's, with a clustered bootstrap interval and a permutation p-value.

| model | dims | vector nDCG@10 | fused nDCG@10 | fused MRR | fused recall@10 | diff vs baseline [95% CI] | p | chunks/s | query ms |
|---|---|---|---|---|---|---|---|---|---|
| jina-v4 (hosted) | 2048 | 0.698 | 0.705 | 0.749 | 0.880 | - | - | - | - |
| qwen3-0.6b | 1024 | 0.719 | 0.717 | 0.745 | 0.898 | +0.012 [-0.084, +0.131] | 0.820 | 0.6 | 387 |
| bge-m3 | 1024 | 0.679 | 0.680 | 0.671 | 0.949 | -0.025 [-0.114, +0.080] | 0.625 | 4.7 | 59 |
| granite-311m-r2 | 768 | 0.673 | 0.698 | 0.716 | 0.924 | -0.007 [-0.068, +0.059] | 0.828 | 2.2 | 41 |
| granite-97m-r2 | 384 | 0.603 | 0.609 | 0.615 | 0.815 | -0.096 [-0.177, -0.023] | 0.030 | 8.3 | 15 |
| harrier-0.6b | 1024 | 0.746 | 0.743 | 0.736 | 0.932 | +0.038 [-0.051, +0.144] | 0.441 | 0.6 | 381 |
| harrier-270m | 640 | 0.687 | 0.690 | 0.680 | 0.932 | -0.014 [-0.091, +0.082] | 0.743 | 2.3 | 95 |

## Fused nDCG@10 by category

| model | keyword | semantic | decision | numeric | date | english |
|---|---|---|---|---|---|---|
| jina-v4 (hosted) | 0.707 | 0.709 | 0.764 | 0.728 | 0.610 | 0.667 |
| qwen3-0.6b | 0.775 | 0.780 | 0.614 | 0.683 | 0.861 | 0.517 |
| bge-m3 | 0.654 | 0.696 | 0.664 | 0.719 | 0.722 | 0.602 |
| granite-311m-r2 | 0.721 | 0.703 | 0.669 | 0.693 | 0.607 | 0.813 |
| granite-97m-r2 | 0.599 | 0.691 | 0.551 | 0.635 | 0.517 | 0.613 |
| harrier-0.6b | 0.781 | 0.681 | 0.593 | 0.814 | 0.918 | 0.729 |
| harrier-270m | 0.715 | 0.765 | 0.506 | 0.698 | 0.787 | 0.664 |
