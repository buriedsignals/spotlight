# Passive Indicator Triage

## Purpose

Triage a domain, IP address, URL, or file hash without collapsing heterogeneous signals into a risk score. The result is a reproducible lead packet that can support—or fail to support—a narrowly worded finding.

## Workflow

### 1. Preserve and classify

Record the original input verbatim. Store a separate lookup form and explain every transformation. For URLs preserve scheme, host, port, path, query, fragment, punycode/Unicode form, and redirects as distinct fields when relevant. For hashes identify the algorithm from length only as a hypothesis until confirmed.

### 2. Set the passive collection plan

Choose only sources that answer an explicit question:

| Indicator | Useful passive questions |
|---|---|
| Domain | When was it observed, how did DNS change, what certificates covered it, what public classifications exist? |
| IP | Which network announced it at the relevant time, what passive DNS names pointed to it, is it shared infrastructure? |
| URL | What exact path was reported or archived, what redirects were previously observed, how do providers describe that exact URL? |
| File hash | Which public datasets observed that exact hash, what names/types/timestamps do they report, do analyses disagree? |

Prefer first-party records, transparent public APIs, archives, and sources whose collection limits are documented. Never upload private or unpublished material to a public analysis service without explicit approval.

WARNING: Do not open a suspicious URL in a normal browser, issue direct HTTP requests to it, scan an IP, enumerate services, detonate a file, validate credentials, or contact a target account. Those actions can alert a subject, expose the investigator, or cause harm.

### 3. Capture observations, not verdicts

For each result preserve:

- provider and source URL;
- exact value or label, without rewriting it as Spotlight's conclusion;
- collection timestamp and provider-supplied observation timestamp;
- query scope: exact URL versus host, hash algorithm, IP, or domain;
- access method, rate/coverage limitation, local artifact, and archive URL where permitted;
- whether the result was `observed`, `null`, `skipped`, or `blocked`.

Provider labels may describe different objects, time windows, or confidence systems. Do not count votes, average scores, or interpret a null result as evidence of safety.

### 4. Test alternatives and contradictions

For every adverse or exculpatory signal, ask what else could produce it. Shared hosting, compromised legitimate sites, stale blocklists, sinkholes, dynamic addresses, URL shorteners, and scanner-generated observations are common alternatives. Seek an independent source and a source capable of disproving the leading interpretation.

If sources conflict, preserve both observations with their time windows and scopes. Invoke `epistemic-grounding`; do not resolve the conflict by provider reputation alone.

### 5. Produce a lead packet

Write artifacts inside the case directory. The packet should contain the original input, lookup forms, questions, coverage ledger, source-linked observations, contradictions, alternative explanations, and remaining gaps. A finding should say only what was observed—for example, that a named provider classified an exact hash at a stated time—not that the subject is malicious unless independent evidence grounds that claim.

## Stop Conditions

Stop and ask the journalist before any step would submit private data, spend paid credits, contact live infrastructure, accept terms on their behalf, or cross from public/passive collection into active interaction.
