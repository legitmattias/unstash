# Embedding bake-off — 40 queries, shared stemmed BM25

| model / config | all nDCG@10 | all MRR | all recall@10 | keyword | semantic | decision | english |
|---|---|---|---|---|---|---|---|
| jina-v4 (2048) vector | 0.677 | 0.723 | 0.861 | 0.643 | 0.744 | 0.749 | 0.650 |
| jina-v4 (2048) rrf | 0.685 | 0.706 | 0.886 | 0.710 | 0.716 | 0.772 | 0.636 |
| jina-v5-small (1024) vector | 0.751 | 0.775 | 0.962 | 0.721 | 0.860 | 0.602 | 0.722 |
| jina-v5-small (1024) rrf | 0.730 | 0.723 | 0.949 | 0.761 | 0.800 | 0.583 | 0.696 |
| bge-m3 (1024) vector | 0.677 | 0.671 | 0.924 | 0.599 | 0.808 | 0.619 | 0.623 |
| bge-m3 (1024) rrf | 0.678 | 0.670 | 0.944 | 0.654 | 0.690 | 0.660 | 0.600 |
