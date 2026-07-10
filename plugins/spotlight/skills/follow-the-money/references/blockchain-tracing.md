# Public-Ledger Transaction Tracing

Use public blockchain records to trace a stated transaction question. On-chain observations can establish that a ledger recorded a transaction; wallet ownership, service labels, intent, and control require separate evidence.

## Start with a Bounded Question

Record the original address or transaction identifier, claimed chain, relevant time window, currency/unit, and the financial question. Confirm the chain from more than an address-shape guess: formats overlap and copied identifiers can be malformed.

WARNING: Never request a signing action, connect a wallet, broadcast a transaction, validate a seed phrase/key, or contact an address owner. Do not place private financial material into a public service. Public read-only exploration is the limit of this method.

## Trace the Ledger

1. Use a reputable public explorer or lawfully available dataset for the claimed chain. Record source URL, collection UTC, block height, confirmation/finality state, and raw/local artifact where possible.
2. Verify transaction identifier, addresses, asset/contract, amount in base and display units, fees, block timestamp, inputs/outputs or calls, and status.
3. Build a chronological flow table. Preserve transaction IDs and edge direction; distinguish gross transferred value from balance, change outputs, token movements, internal calls, and fees.
4. Follow only case-relevant hops. State stopping rules such as time window, maximum justified depth, value threshold, or arrival at a labeled service.
5. Check the same critical transaction in an independent explorer or raw chain source when feasible.
6. Record null, skipped, and blocked paths and provider coverage limits.

## Labels and Clusters

Provider labels, scam reports, and wallet clusters are leads. Preserve the provider's exact label, observation date, source, and stated method if available. Do not silently convert “deposit address associated with service X” into “owned by X” or “person X cashed out.”

Clustering heuristics can fail because of shared custody, exchanges, smart contracts, payment processors, change-address error, CoinJoin, bridges, privacy protocols, address reuse, or adversarial behavior. Treat cross-chain continuation as a new evidence lane with its own asset, bridge, transaction, and time records.

## Off-ramp and Identity Boundary

A flow into a provider-labeled exchange or service can identify a potential records-holder. It does not reveal the customer. Any request for non-public subscriber/KYC records requires the journalist's legal and editorial process; this skill neither acquires nor infers those records.

## Output

Produce:

- a source-linked transaction table;
- a directed flow graph whose edges carry transaction IDs, time, asset, and amount;
- separate sections for ledger observations, third-party labels, heuristic inferences, contradictions, and gaps;
- explicit alternative explanations and confidence caps.

Invoke `epistemic-grounding` before asserting wallet control, common ownership, criminality, exchange use, or a real-world identity.

Adapted from [CTI Expert](https://github.com/7onez/cti-expert) by Hieu Ngo / chongluadao.vn (GitHub: 7onez; MIT License with upstream Ethical Use Addendum), with active/darknet methods, raw shell recipes, and fixed confidence labels removed.
