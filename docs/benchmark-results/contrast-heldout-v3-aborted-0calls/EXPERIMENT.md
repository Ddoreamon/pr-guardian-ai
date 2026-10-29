# Experiment record

| Field | Value |
|---|---|
| `started_at` | `2026-10-27T15:39:25+00:00` |
| `finished_at` | `2026-10-27T15:39:33+00:00` |
| `git_branch` | `feature/v2-function-calling` |
| `git_sha` | `ebcd05fd21081196052c101c960668d6ab5820e0` |
| `git_tree_clean` | `true` |
| `dataset_version` | `v3` |
| `split` | `heldout` |
| `methods` | `["v1-diff-only", "v2-tools"]` |
| `model` | `deepseek-flash` |
| `base_url` | `https://api.deepseek.com` |
| `public_mode` | `false` |
| `allow_test_execution` | `true` |
| `max_live_llm_calls` | `50` |
| `tool_budget` | `{"max_rounds": 3, "max_calls": 4}` |
| `evaluator` | `benchmark/evaluate_contrast.py — label classes v3, strict and decided readings, exact McNemar` |
| `python` | `3.12.13` |
| `platform` | `Windows-11-10.0.26200-SP0` |
| `api_key_recorded` | `false` |

## Pairs

| Pair | Split | Mechanism | Clean class |
|---|---|---|---|
| P6 | heldout | M6 | context_dependent |
| P7 | heldout | M1 | unambiguous |
| P8 | heldout | M7 | context_dependent |
| P9 | heldout | M6 | context_dependent |
| P10 | heldout | M7 | context_dependent |
| P11 | heldout | M7 | context_dependent |
