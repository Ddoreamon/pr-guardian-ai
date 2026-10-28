# Repository retrieval evaluation (no model calls)

Independent dev corpus, not the frozen benchmark. Gold locations are fixed
before running the queries; nothing here measures review accuracy.

* corpus: `<dev-corpus>`, pinned `c9c73cc32451d9d9ac4b52b1260492a8b06609f6`
* chunks: 10
* index build: 0.2035s (first), 0.0098s (cached by SHA)

## search_code

Overall: Recall@1 75.00%, Recall@3 75.00%, Recall@5 75.00%, MRR 0.7500 over 12 queries.

### Lexical queries (share vocabulary with the target) (8 queries)

| metric | value |
|---|---|
| Recall@1 | 100.00% |
| Recall@3 | 100.00% |
| Recall@5 | 100.00% |
| MRR | 1.0000 |
| mean latency | 0.0005s |
| mean result size | 1531.6 chars |

| query | gold | rank |
|---|---|---|
| order total with tax | `src/pricing.py:6` | 1 |
| rate for region | `src/rates.py:7` | 1 |
| DEFAULT_RATE | `src/rates.py:3` | 1 |
| reserve stock insufficient | `src/inventory.py:6` | 1 |
| build report from orders | `src/reporting.py:6` | 1 |
| low stock threshold | `src/inventory.py:13` | 1 |
| ANNOTATED_RATE | `src/rates.py:4` | 1 |
| create order and return its total | `src/orders.py:6` | 1 |

### Paraphrase queries (little lexical overlap) (4 queries)

| metric | value |
|---|---|
| Recall@1 | 25.00% |
| Recall@3 | 25.00% |
| Recall@5 | 25.00% |
| MRR | 0.2500 |
| mean latency | 0.0004s |
| mean result size | 371.2 chars |

| query | gold | rank |
|---|---|---|
| how much money the customer owes | `src/pricing.py:6` | miss |
| prevent selling more than we have | `src/inventory.py:6` | miss |
| summary of sales rows | `src/reporting.py:6` | 1 |
| tax percentage per area | `src/rates.py:7` | miss |


## symbol + read baseline

* hit rate: 100.00%
* mean latency: 0.0013s
* mean result size: 504.8 chars
* caveat: uses the gold symbol name as input; a caller who does not already know the symbol cannot issue this query

## Reading

The baseline is not beaten on latency or payload size; it is beaten on the
question it cannot answer. Symbol lookup needs the symbol name up front, so a
reviewer who sees an unfamiliar call cannot use it, while `search_code` can be
given the change's own vocabulary. The evaluation therefore reports retrieval
reach (Recall@k, MRR) for search and marks the baseline as 'requires the
symbol name', rather than pretending the two are on the same axis.
