# Hybrid retrieval evaluation v1 (BM25 vs Dense vs RRF)

Frozen: gold `(file_path, qualname, start_line)`, queries, model revision,
chunk rules, top-k = 5, RRF k = 60. Nothing here is a review-accuracy claim.

* model `BAAI/bge-small-en-v1.5` @ `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`, dim 384,
  pooling `mean` (implementation deviation: the model card recommends CLS), normalised True, local ONNX, available: True

## synthetic — <synthetic-v2> (dev (queries authored earlier))

* commit `24192166d62ececf7b22179968ba546bd5fc858f`
* 103 chunks across 28 files, 31 queries
* cold query 2.305s, warm 0.006s, RSS 63.9 → 272.8 MB

| method | Recall@1 | Recall@3 | Recall@5 | MRR | no-result | latency | chars |
|---|---|---|---|---|---|---|---|
| bm25 | 25.81% | 41.94% | 45.16% | 0.3237 | 12.90% | 0.0003s | 1603 |
| dense | 58.06% | 90.32% | 96.77% | 0.7366 | 0.00% | 0.007s | 2249.0 |
| hybrid | 45.16% | 74.19% | 87.10% | 0.5973 | 0.00% | 0.0072s | 2776.0 |

* dense-only hits (gold in dense top-k, absent from BM25 top-k): **16**
* bm25-only hits: 0
* hybrid gains over BM25: 13
* reference D (domain table, tuned): Recall@3 83.87%, MRR 0.7796
* ambiguous-gold queries dropped: 0

## click — pallets/click (held-out (written from the public API, run once))

* commit `2247b35ea1c47c727d7a06e51fa280e12a863ff6`
* 864 chunks across 17 files, 20 queries
* index + vector cache on disk: 5002395 bytes

| method | Recall@1 | Recall@3 | Recall@5 | MRR | no-result | latency | chars |
|---|---|---|---|---|---|---|---|
| bm25 | 20.00% | 20.00% | 25.00% | 0.2100 | 0.00% | 0.0143s | 3449.2 |
| dense | 30.00% | 40.00% | 60.00% | 0.3950 | 0.00% | 0.0088s | 3090.7 |
| hybrid | 15.00% | 35.00% | 40.00% | 0.2458 | 0.00% | 0.0227s | 3780 |

* dense-only hits (gold in dense top-k, absent from BM25 top-k): **7**
* bm25-only hits: 0
* hybrid gains over BM25: 3

## Reading

Dense retrieval clearly beats BM25 on both corpora, and it does so by
reaching chunks BM25 never returned (dense-only counts above). That answers
the first research question positively.

Hybrid, as specified here, does **not**: RRF with k = 60 fuses a weak BM25
list into a much stronger dense list, so the final top-k is worse than dense
alone on both corpora. On the held-out corpus it is even below BM25 at
Recall@1. The parameters were fixed before measurement and have not been
tuned to the results.

Consequence: if a retrieval mode is kept, it should be `dense` (or BM25 where
the model cannot be installed), not `hybrid`. A weighted fusion that trusts
each retriever's own rank distribution, or RRF over equal-depth lists sized by
the final k, is the obvious next experiment - but it must be pre-registered
and evaluated on a fresh held-out set, not fitted to these numbers.


## Per-query ranks

### synthetic

| query | gold | bm25 | dense | hybrid |
|---|---|---|---|---|
| how much the buyer has to pay | `src/shop/pricing.py:8` order_total | miss | 3 | 5 |
| stop us selling things we do not have | `src/shop/inventory.py:6` reserve_stock | miss | 4 | 4 |
| money given back after a return | `src/shop/refunds.py:6` refund_total | 3 | 2 | 1 |
| how long a buyer has to ask for money back | `src/shop/refunds.py:14` refund_window_days | miss | 2 | 5 |
| points a buyer earns for spending | `src/shop/customers.py:8` loyalty_points | 1 | 1 | 1 |
| who carries the parcel and what it costs | `src/shop/shipping.py:6` estimate_shipping | miss | 1 | 2 |
| keep a record of what happened for later inspection | `src/shop/audit.py:4` log_event | 1 | 1 | 1 |
| make guessing a secret phrase too slow to matter | `src/shop/security.py:6` hash_password | 3 | 1 | 1 |
| how many units are left in the depot | `src/shop/inventory.py:16` available_quantity | miss | 2 | 3 |
| warn when we are running out | `src/shop/inventory.py:20` low_stock | miss | miss | miss |
| the percentage the government adds | `src/shop/tax.py:8` vat_rate | miss | 1 | 1 |
| the biggest sellers | `src/shop/reporting.py:12` top_products | miss | 2 | 2 |
| how often visitors end up buying | `src/shop/analytics.py:4` conversion_rate | miss | 3 | 5 |
| try again when something fails | `src/shop/utils.py:8` retry_call | 1 | 1 | 1 |
| cut a long list into small pieces | `src/shop/utils.py:4` chunked | miss | 1 | 3 |
| tell the buyer the parcel is on its way | `src/shop/notifications.py:8` send_shipping_update | 3 | 1 | 1 |
| how long a login stays valid | `src/shop/security.py:14` token_expiry | miss | 3 | 3 |
| the cheapest company to move the box | `src/shop/shipping.py:11` cheapest_carrier | 3 | 2 | 2 |
| what the buyer owes before any reduction | `src/shop/pricing.py:8` order_total | miss | 3 | 3 |
| put stock back after a cancellation | `src/shop/inventory.py:12` release_stock | 1 | 1 | 1 |
| reject a request to take more than we hold | `src/shop/inventory.py:6` reserve_stock | 5 | 1 | 1 |
| the value kept back from a salary | `src/shop/tax.py:12` withholding | miss | 1 | 2 |
| a short list of the best performers | `src/shop/search.py:12` rank_results | miss | 4 | miss |
| drain the pending work | `src/shop/workers.py:4` process_queue | 1 | 1 | 1 |
| collect the jobs that did not finish | `src/shop/workers.py:8` retry_failed_jobs | 1 | 1 | 1 |
| confirm a service is up | `src/shop/api.py:8` health_check | 1 | 1 | 1 |
| count how many things are in a basket | `src/shop/analytics.py:8` basket_size | 1 | 1 | 1 |
| sort products by how well they matched | `src/shop/search.py:12` rank_results | miss | 2 | 3 |
| the amount kept from a wage | `src/shop/tax.py:12` withholding | miss | 1 | miss |
| when a payment did not go through | `src/shop/payments.py:12` payment_status | 2 | 1 | 1 |
| give the money back for one item | `src/shop/refunds.py:10` partial_refund | miss | 1 | miss |

### click

| query | gold | bm25 | dense | hybrid |
|---|---|---|---|---|
| print text with colour and styling | `src/click/termui.py:780` secho | miss | miss | miss |
| parse a timestamp written by the user | `src/click/types.py:566` DateTime.convert | miss | miss | miss |
| accept a true or false value from the user | `src/click/types.py:870` BoolParamType.convert | miss | 2 | 4 |
| reject bad input with a message and a non-zero status | `src/click/exceptions.py:87` UsageError.show | miss | miss | miss |
| show a friendly error when something goes wrong | `src/click/exceptions.py:57` ClickException.show | 1 | 4 | 2 |
| restrict the input to a fixed set of allowed values | `src/click/types.py:454` Choice.convert | miss | miss | miss |
| accept several values at once for one parameter | `src/click/types.py:1289` Tuple.convert | miss | 5 | miss |
| render the list of options and their help text | `src/click/formatting.py:229` HelpFormatter.write_dl | miss | miss | miss |
| choose a text encoding that will work on this stream | `src/click/_compat.py:51` get_best_encoding | miss | 4 | miss |
| hand the running context to the decorated function | `src/click/decorators.py:28` pass_context | miss | 1 | 2 |
| combine the short flags of one option into a single token | `src/click/formatting.py:302` join_options | miss | 5 | miss |
| turn bytes into text without losing characters | `src/click/utils.py:52` make_str | miss | 2 | miss |
| check whether a charset only covers plain ascii | `src/click/_compat.py:43` is_ascii_encoding | 1 | 1 | 1 |
| open a file that the user named on the command line | `src/click/types.py:984` File.convert | miss | miss | miss |
| render one option''s help entry for the help screen | `src/click/core.py:3589` Option.get_help_record | miss | miss | miss |
| print the usage line for a command | `src/click/formatting.py:158` HelpFormatter.write_usage | miss | miss | miss |
| take a value from the command line for a parameter | `src/click/core.py:2680` Parameter.consume_value | miss | 1 | 3 |
| secho styled text | `src/click/termui.py:780` secho | 1 | 1 | 1 |
| Choice convert values | `src/click/types.py:454` Choice.convert | 5 | 1 | 3 |
| get_best_encoding stream | `src/click/_compat.py:51` get_best_encoding | 1 | 1 | 1 |

