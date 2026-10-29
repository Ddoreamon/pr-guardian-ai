# V4.5 sufficiency gate — offline evaluation

Assessment version `v4.5.1`. No model calls: dev evidence
comes from the recorded V4.4 traces, held-out evidence from scripted profiles.

## Summary

| metric | value |
|---|---|
| scored profiles | 33 |
| status accuracy | 1.0 |
| false sufficient | **0** [] |
| false insufficient | 0 [] |
| unknown rate | 0.0222 |
| obligation kind precision | 1.0 |
| obligation kind recall | 1.0 |
| mean assessment cost | 62.57 ms |
| mean derivation cost | 294.87 ms |

## Per profile

| set | case | profile | status | expected | kinds | required | missing | fulfilled |
|---|---|---|---|---|---|---|---|---|
| dev | B1-cents | baseline | sufficient | — | callee_contract, modified_definition | 3 | 0 | 3 |
| dev | B2-kwarg | baseline | sufficient | — | callee_contract, modified_definition | 2 | 0 | 2 |
| dev | B3-exception | baseline | sufficient | — | caller_usage, modified_definition | 2 | 0 | 2 |
| dev | C1-kwarg | baseline | sufficient | — | modified_definition | 1 | 0 | 1 |
| dev | C2-identity | baseline | sufficient | — | callee_contract, modified_definition | 2 | 0 | 2 |
| dev | C3-refactor | baseline | sufficient | — | modified_definition | 1 | 0 | 1 |
| dev | B1-cents | planner | sufficient | — | callee_contract, modified_definition | 3 | 0 | 3 |
| dev | B2-kwarg | planner | sufficient | — | callee_contract, modified_definition | 2 | 0 | 2 |
| dev | B3-exception | planner | sufficient | — | caller_usage, modified_definition | 2 | 0 | 2 |
| dev | C1-kwarg | planner | sufficient | — | modified_definition | 1 | 0 | 1 |
| dev | C2-identity | planner | sufficient | — | callee_contract, modified_definition | 2 | 0 | 2 |
| dev | C3-refactor | planner | sufficient | — | modified_definition | 1 | 0 | 1 |
| held-out | H1-signature | complete | sufficient | sufficient | caller_usage, modified_definition | 3 | 0 | 3 |
| held-out | H1-signature | callee-only | insufficient | insufficient | caller_usage, modified_definition | 3 | 2 | 1 |
| negative | H1-signature | wrong-sha | insufficient | insufficient | caller_usage, modified_definition | 1 | 3 | 0 |
| negative | H1-signature | truncated-read | insufficient | insufficient | caller_usage, modified_definition | 1 | 3 | 0 |
| negative | H1-signature | search-only | insufficient | insufficient | caller_usage, modified_definition | 1 | 3 | 0 |
| held-out | H2-return | complete | sufficient | sufficient | caller_usage, modified_definition | 3 | 0 | 3 |
| held-out | H2-return | callee-only | insufficient | insufficient | caller_usage, modified_definition | 3 | 2 | 1 |
| negative | H2-return | wrong-sha | insufficient | insufficient | caller_usage, modified_definition | 1 | 3 | 0 |
| negative | H2-return | truncated-read | insufficient | insufficient | caller_usage, modified_definition | 1 | 3 | 0 |
| negative | H2-return | search-only | insufficient | insufficient | caller_usage, modified_definition | 1 | 3 | 0 |
| held-out | H3-dynamic | complete | unknown | unknown | modified_definition, unknown_dependency | 1 | 0 | 1 |
| negative | H3-dynamic | wrong-sha | insufficient | insufficient | modified_definition, unknown_dependency | 1 | 1 | 0 |
| negative | H3-dynamic | truncated-read | insufficient | insufficient | modified_definition, unknown_dependency | 1 | 1 | 0 |
| negative | H3-dynamic | search-only | insufficient | insufficient | modified_definition, unknown_dependency | 1 | 1 | 0 |
| held-out | H4-multicaller | complete | sufficient | sufficient | callee_contract, modified_definition | 4 | 0 | 4 |
| held-out | H4-multicaller | one-caller | insufficient | insufficient | callee_contract, modified_definition | 4 | 2 | 2 |
| negative | H4-multicaller | wrong-sha | insufficient | insufficient | callee_contract, modified_definition | 1 | 4 | 0 |
| negative | H4-multicaller | truncated-read | insufficient | insufficient | callee_contract, modified_definition | 1 | 4 | 0 |
| negative | H4-multicaller | search-only | insufficient | insufficient | callee_contract, modified_definition | 1 | 4 | 0 |
| held-out | H5-config | complete | sufficient | sufficient | modified_definition | 1 | 0 | 1 |
| negative | H5-config | wrong-sha | insufficient | insufficient | modified_definition | 1 | 1 | 0 |
| negative | H5-config | truncated-read | insufficient | insufficient | modified_definition | 1 | 1 | 0 |
| negative | H5-config | search-only | insufficient | insufficient | modified_definition | 1 | 1 | 0 |
| held-out | H6-local | complete | sufficient | sufficient | modified_definition | 1 | 0 | 1 |
| held-out | H6-local | search-only | insufficient | insufficient | modified_definition | 1 | 1 | 0 |
| negative | H6-local | wrong-sha | insufficient | insufficient | modified_definition | 1 | 1 | 0 |
| negative | H6-local | truncated-read | insufficient | insufficient | modified_definition | 1 | 1 | 0 |
| negative | H6-local | search-only | insufficient | insufficient | modified_definition | 1 | 1 | 0 |
| held-out | H7-same-file | complete | sufficient | sufficient | caller_usage, modified_definition | 2 | 0 | 2 |
| held-out | H7-same-file | callee-only | insufficient | insufficient | caller_usage, modified_definition | 2 | 1 | 1 |
| negative | H7-same-file | wrong-sha | insufficient | insufficient | caller_usage, modified_definition | 1 | 2 | 0 |
| negative | H7-same-file | truncated-read | insufficient | insufficient | caller_usage, modified_definition | 1 | 2 | 0 |
| negative | H7-same-file | search-only | insufficient | insufficient | caller_usage, modified_definition | 1 | 2 | 0 |

## Explanations

* **held-out/H1-signature/callee-only** — required evidence was not delivered for: ob-8122ce7f87, ob-23634cf8f6
* **negative/H1-signature/wrong-sha** — required evidence was not delivered for: ob-ff73d366c2, ob-8122ce7f87, ob-23634cf8f6
* **negative/H1-signature/truncated-read** — required evidence was not delivered for: ob-ff73d366c2, ob-8122ce7f87, ob-23634cf8f6
* **negative/H1-signature/search-only** — required evidence was not delivered for: ob-ff73d366c2, ob-8122ce7f87, ob-23634cf8f6
* **held-out/H2-return/callee-only** — required evidence was not delivered for: ob-8122ce7f87, ob-23634cf8f6
* **negative/H2-return/wrong-sha** — required evidence was not delivered for: ob-ff73d366c2, ob-8122ce7f87, ob-23634cf8f6
* **negative/H2-return/truncated-read** — required evidence was not delivered for: ob-ff73d366c2, ob-8122ce7f87, ob-23634cf8f6
* **negative/H2-return/search-only** — required evidence was not delivered for: ob-ff73d366c2, ob-8122ce7f87, ob-23634cf8f6
* **held-out/H3-dynamic/complete** — required evidence is covered, but dynamic constructs mean the obligation set cannot be proven complete: ob-c9656ce66b
* **negative/H3-dynamic/wrong-sha** — required evidence was not delivered for: ob-f37ff538fc
* **negative/H3-dynamic/truncated-read** — required evidence was not delivered for: ob-f37ff538fc
* **negative/H3-dynamic/search-only** — required evidence was not delivered for: ob-f37ff538fc
* **held-out/H4-multicaller/one-caller** — required evidence was not delivered for: ob-519f1d0775, ob-b9f92e5e4e
* **negative/H4-multicaller/wrong-sha** — required evidence was not delivered for: ob-cc5a682a3b, ob-519f1d0775, ob-76c7de7cfb, ob-b9f92e5e4e
* **negative/H4-multicaller/truncated-read** — required evidence was not delivered for: ob-cc5a682a3b, ob-519f1d0775, ob-76c7de7cfb, ob-b9f92e5e4e
* **negative/H4-multicaller/search-only** — required evidence was not delivered for: ob-cc5a682a3b, ob-519f1d0775, ob-76c7de7cfb, ob-b9f92e5e4e
* **negative/H5-config/wrong-sha** — required evidence was not delivered for: ob-72ca274f5a
* **negative/H5-config/truncated-read** — required evidence was not delivered for: ob-72ca274f5a
* **negative/H5-config/search-only** — required evidence was not delivered for: ob-72ca274f5a
* **held-out/H6-local/search-only** — required evidence was not delivered for: ob-cf1617404f
* **negative/H6-local/wrong-sha** — required evidence was not delivered for: ob-cf1617404f
* **negative/H6-local/truncated-read** — required evidence was not delivered for: ob-cf1617404f
* **negative/H6-local/search-only** — required evidence was not delivered for: ob-cf1617404f
* **held-out/H7-same-file/callee-only** — required evidence was not delivered for: ob-5dd067b9ab
* **negative/H7-same-file/wrong-sha** — required evidence was not delivered for: ob-d81a39d8a0, ob-5dd067b9ab
* **negative/H7-same-file/truncated-read** — required evidence was not delivered for: ob-d81a39d8a0, ob-5dd067b9ab
* **negative/H7-same-file/search-only** — required evidence was not delivered for: ob-d81a39d8a0, ob-5dd067b9ab
