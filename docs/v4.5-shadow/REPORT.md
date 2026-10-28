# V4.5 shadow replay — dev samples

## R4-alias/complete

- review SHA: `1c47089073c41969f91dbc8fa1a520660e18b701`
- report SHA: `1c47089073c41969f91dbc8fa1a520660e18b701`
- assessment: `v4.5.2`
- replay verification: **exact**
- sufficiency: **sufficient** (diff state: `analysable`)
- missing obligations: none
- unknown obligations: none
- elapsed: 561.1 ms

> every derived obligation is covered by evidence that replays exactly from the saved report

## R4-alias/partial

- review SHA: `1c47089073c41969f91dbc8fa1a520660e18b701`
- report SHA: `1c47089073c41969f91dbc8fa1a520660e18b701`
- assessment: `v4.5.2`
- replay verification: **exact**
- sufficiency: **insufficient** (diff state: `analysable`)
- missing obligations: ['ob-ae824bd3cf', 'ob-8122ce7f87']
- unknown obligations: none
- elapsed: 551.7 ms

> required evidence was not delivered for: ob-ae824bd3cf, ob-8122ce7f87

## R9-default/partial

- review SHA: `5f714707f8d301e261aa201109bae0efc826bf00`
- report SHA: `5f714707f8d301e261aa201109bae0efc826bf00`
- assessment: `v4.5.2`
- replay verification: **exact**
- sufficiency: **insufficient** (diff state: `analysable`)
- missing obligations: ['ob-74dde708f6']
- unknown obligations: none
- elapsed: 504.4 ms

> required evidence was not delivered for: ob-74dde708f6

## R10-registry/partial

- review SHA: `51e86b187e9b97d54bfe62d9910bdd21784409ba`
- report SHA: `51e86b187e9b97d54bfe62d9910bdd21784409ba`
- assessment: `v4.5.2`
- replay verification: **exact**
- sufficiency: **unknown** (diff state: `analysable`)
- missing obligations: none
- unknown obligations: ['ob-65cc18af5f']
- elapsed: 510.7 ms

> required evidence is covered, but dynamic constructs mean the obligation set cannot be proven complete: ob-65cc18af5f
