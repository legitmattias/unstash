# Retrieval eval — embedder=jina, 40 queries

| metric | vector | bm25 | rrf |
|---|---|---|---|
| all/mrr | 0.719 | 0.449 | 0.657 |
| all/ndcg@10 | 0.673 | 0.447 | 0.638 |
| all/recall@10 | 0.861 | 0.672 | 0.856 |
| date/ndcg@10 | 0.496 | 0.313 | 0.510 |
| decision/ndcg@10 | 0.749 | 0.430 | 0.671 |
| english/ndcg@10 | 0.650 | 0.024 | 0.466 |
| keyword/ndcg@10 | 0.640 | 0.687 | 0.745 |
| numeric/ndcg@10 | 0.700 | 0.431 | 0.594 |
| semantic/ndcg@10 | 0.730 | 0.523 | 0.700 |
