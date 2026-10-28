# RAG Dev evaluation v2 — BM25 vs deterministic query expansion

Separate from `docs/rag-eval/`, which is historical and untouched. This is a
retrieval experiment, not a review-accuracy benchmark.

* corpus: `<dev-corpus-v2>`, pinned `a007667e95472d34ac0116fdbff02a08c31488d5`
* chunks: 103 across 28 files
* index build: 1.048s, cached 0.0131s, cache file 39375 bytes
* frozen queries: 51 ({'lexical': 12, 'paraphrase': 31, 'mixed': 8})

## Overall

| method | Recall@1 | Recall@3 | Recall@5 | MRR | no-result | mean candidates | latency | chars |
|---|---|---|---|---|---|---|---|---|
| A · BM25 | 52.94% | 64.71% | 66.67% | 0.5791 | 7.84% | 3.31 | 0.0001s | 1394.0 |
| B · BM25 + domain table | 78.43% | 90.20% | 94.12% | 0.8464 | 0.00% | 4.31 | 0.0002s | 1802.8 |
| C · BM25 + generic table | 50.98% | 66.67% | 68.63% | 0.5791 | 7.84% | 3.43 | 0.0001s | 1453.6 |

## By query group

| method | group | n | Recall@1 | Recall@3 | Recall@5 | MRR |
|---|---|---|---|---|---|---|
| A | lexical | 12 | 91.67% | 100.00% | 100.00% | 0.9583 |
| A | paraphrase | 31 | 25.81% | 41.94% | 45.16% | 0.3237 |
| A | mixed | 8 | 100.00% | 100.00% | 100.00% | 1.0000 |
| B | lexical | 12 | 91.67% | 100.00% | 100.00% | 0.9583 |
| B | paraphrase | 31 | 70.97% | 83.87% | 90.32% | 0.7796 |
| B | mixed | 8 | 87.50% | 100.00% | 100.00% | 0.9375 |
| C | lexical | 12 | 91.67% | 100.00% | 100.00% | 0.9583 |
| C | paraphrase | 31 | 25.81% | 45.16% | 48.39% | 0.3398 |
| C | mixed | 8 | 87.50% | 100.00% | 100.00% | 0.9375 |

## Per-query (BM25 vs expanded)

| query | group | gold | A rank | B rank |
|---|---|---|---|---|
| reserve_stock | lexical | `src/shop/inventory.py:6` | 1 | 1 |
| MIN_STOCK | lexical | `src/shop/inventory.py:3` | 1 | 1 |
| estimate_shipping express | lexical | `src/shop/shipping.py:6` | 1 | 1 |
| loyalty_points | lexical | `src/shop/customers.py:8` | 1 | 1 |
| hash_password digest | lexical | `src/shop/security.py:6` | 2 | 2 |
| refund_window_days | lexical | `src/shop/refunds.py:14` | 1 | 1 |
| CACHE_TTL_SECONDS | lexical | `src/shop/config.py:4` | 1 | 1 |
| free_shipping_threshold | lexical | `src/shop/shipping.py:15` | 1 | 1 |
| basket_size items | lexical | `src/shop/analytics.py:8` | 1 | 1 |
| order_history customer | lexical | `src/shop/orders.py:17` | 1 | 1 |
| top_products qty | lexical | `src/shop/reporting.py:12` | 1 | 1 |
| verify_password digest | lexical | `src/shop/security.py:10` | 1 | 1 |
| how much the buyer has to pay | paraphrase | `src/shop/pricing.py:8` | miss | 4 |
| stop us selling things we do not have | paraphrase | `src/shop/inventory.py:6` | miss | 4 |
| money given back after a return | paraphrase | `src/shop/refunds.py:6` | 3 | 1 |
| how long a buyer has to ask for money back | paraphrase | `src/shop/refunds.py:14` | miss | miss |
| points a buyer earns for spending | paraphrase | `src/shop/customers.py:8` | 1 | 1 |
| who carries the parcel and what it costs | paraphrase | `src/shop/shipping.py:6` | miss | 2 |
| keep a record of what happened for later inspection | paraphrase | `src/shop/audit.py:4` | 1 | 1 |
| make guessing a secret phrase too slow to matter | paraphrase | `src/shop/security.py:6` | 3 | 1 |
| how many units are left in the depot | paraphrase | `src/shop/inventory.py:16` | miss | 1 |
| warn when we are running out | paraphrase | `src/shop/inventory.py:20` | miss | 1 |
| the percentage the government adds | paraphrase | `src/shop/tax.py:8` | miss | 1 |
| the biggest sellers | paraphrase | `src/shop/reporting.py:12` | miss | 1 |
| how often visitors end up buying | paraphrase | `src/shop/analytics.py:4` | miss | 1 |
| try again when something fails | paraphrase | `src/shop/utils.py:8` | 1 | 1 |
| cut a long list into small pieces | paraphrase | `src/shop/utils.py:4` | miss | miss |
| tell the buyer the parcel is on its way | paraphrase | `src/shop/notifications.py:8` | 3 | 1 |
| how long a login stays valid | paraphrase | `src/shop/security.py:14` | miss | 1 |
| the cheapest company to move the box | paraphrase | `src/shop/shipping.py:11` | 3 | 1 |
| what the buyer owes before any reduction | paraphrase | `src/shop/pricing.py:8` | miss | 3 |
| put stock back after a cancellation | paraphrase | `src/shop/inventory.py:12` | 1 | 1 |
| reject a request to take more than we hold | paraphrase | `src/shop/inventory.py:6` | 5 | 1 |
| the value kept back from a salary | paraphrase | `src/shop/tax.py:12` | miss | 1 |
| a short list of the best performers | paraphrase | `src/shop/search.py:12` | miss | miss |
| drain the pending work | paraphrase | `src/shop/workers.py:4` | 1 | 1 |
| collect the jobs that did not finish | paraphrase | `src/shop/workers.py:8` | 1 | 1 |
| confirm a service is up | paraphrase | `src/shop/api.py:8` | 1 | 1 |
| count how many things are in a basket | paraphrase | `src/shop/analytics.py:8` | 1 | 1 |
| sort products by how well they matched | paraphrase | `src/shop/search.py:12` | miss | 1 |
| the amount kept from a wage | paraphrase | `src/shop/tax.py:12` | miss | 1 |
| when a payment did not go through | paraphrase | `src/shop/payments.py:12` | 2 | 2 |
| give the money back for one item | paraphrase | `src/shop/refunds.py:10` | miss | 3 |
| refund total money returned | mixed | `src/shop/refunds.py:6` | 1 | 1 |
| stock reserve insufficient quantity | mixed | `src/shop/inventory.py:6` | 1 | 1 |
| sales summary daily totals rows | mixed | `src/shop/reporting.py:4` | 1 | 2 |
| customer tier loyalty points | mixed | `src/shop/customers.py:12` | 1 | 1 |
| queue retry failed jobs worker | mixed | `src/shop/workers.py:8` | 1 | 1 |
| product search rank results score | mixed | `src/shop/search.py:12` | 1 | 1 |
| cache ttl seconds expiry | mixed | `src/shop/config.py:4` | 1 | 1 |
| token expiry login valid | mixed | `src/shop/security.py:14` | 1 | 1 |

## Reading

Expansion is deterministic and bounded: a fixed synonym table plus plural
normalisation, capped at eight extra tokens.

**B is a tuned upper bound, not a general result.** The domain synonym table was written while looking at this corpus, so its gain mostly measures how much a human who already knows the vocabulary can help. Variant C uses a generic English table written without consulting the identifiers, and it barely moves the numbers (paraphrase Recall@3 41.94% -> 45.16%, overall Recall@3 64.71% -> 66.67%).

The honest conclusion: without someone supplying the domain vocabulary, keyword retrieval leaves roughly 40-45% of paraphrase queries unreachable at k=3. That gap is vocabulary, not ranking, which is the case a dense retriever is meant to cover - but it must be measured with a table-free, corpus-independent query set before any claim is made.
