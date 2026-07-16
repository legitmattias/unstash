# Retrieval eval — embedder=jina, bm25=swedish, 40 queries

| metric | vector | bm25 | rrf |
|---|---|---|---|
| all/mrr | 0.719 | 0.530 | 0.627 |
| all/ndcg@10 | 0.673 | 0.500 | 0.638 |
| all/recall@10 | 0.861 | 0.686 | 0.866 |
| date/ndcg@10 | 0.496 | 0.297 | 0.447 |
| decision/ndcg@10 | 0.749 | 0.578 | 0.674 |
| english/ndcg@10 | 0.650 | 0.024 | 0.466 |
| keyword/ndcg@10 | 0.640 | 0.745 | 0.740 |
| numeric/ndcg@10 | 0.700 | 0.374 | 0.623 |
| semantic/ndcg@10 | 0.730 | 0.643 | 0.713 |
