---
name: fact-checker
description: Comprehensive fact-checking and verification system for analyzing text, articles, or webpage content for factual accuracy. Use when users ask to verify claims, fact-check articles, assess credibility of information, check sources, validate statements, or determine if something is true or false. Also use for requests to analyze misinformation, verify news articles, check social media claims, or assess the reliability of sources.
---

# Fact Checker

## Overview

Analyze text, articles, and webpage content for factual accuracy using rigorous verification methodology. Extract verifiable claims, cross-reference against trusted academic and authoritative sources, assess credibility, and provide comprehensive verification reports with supporting evidence.

## Fact-Checking Workflow

Follow these steps for thorough fact-checking:

1. **Claim Extraction** - Identify all verifiable factual claims
2. **Source Research** - Search trusted sources for verification
3. **Credibility Assessment** - Evaluate each claim with evidence
4. **Quality Rating** - Score sources using established criteria
5. **Report Generation** - Compile findings in structured format

## Step 1: Claim Extraction

Identify and list all factual claims that can be verified:

**Extract claims that are:**
- Specific and measurable (statistics, dates, quotes)
- Objectively verifiable (events, facts, scientific statements)
- Attributable to specific sources (when cited)

**Exclude:**
- Opinions and subjective judgments
- Future predictions (unless assessing past predictions)
- Vague or ambiguous statements
- Questions or rhetorical statements

**Format extracted claims as:**
```
Claim 1: [Specific factual statement]
Claim 2: [Specific factual statement]
Claim 3: [Specific factual statement]
```

## Step 2: Source Research

For each claim, search trusted sources systematically:

**Search priority order:**
1. **Academic sources first** - Use web_search with terms like "site:scholar.google.com [claim]", "site:pubmed.ncbi.nlm.nih.gov [claim]", or "[claim] peer reviewed study"
2. **Government databases** - For statistics and official data, search "site:gov [claim]" or specific databases (census.gov, cdc.gov, etc.)
3. **Fact-checking organizations** - Search "site:snopes.com [claim]", "site:politifact.com [claim]", "site:factcheck.org [claim]"
4. **Reputable news sources** - Cross-reference with established news organizations
5. **Domain-specific experts** - For specialized topics, seek expert sources

**Search strategies:**
- Use web_search tool with specific keywords from the claim
- Search for both supporting and contradictory evidence
- Use web_fetch to retrieve full content of promising sources
- Check multiple independent sources for corroboration
- Look for original/primary sources when possible

**For each claim, aim to find:**
- At least 2-3 independent sources
- Primary sources when available
- Recent sources (especially for current events)
- Sources with documented methodology

## Step 3: Credibility Assessment

For each claim, provide a detailed assessment:

### Verification Status

Assign one of these statuses (see references/verification_methodology.md for detailed criteria):

**TRUE:** Supported by reliable evidence from multiple sources with no significant contradictions

**FALSE:** Contradicted by reliable evidence; demonstrably incorrect

**PARTIALLY TRUE:** Contains elements of truth but incomplete, misleading, or lacks crucial context

**UNVERIFIED:** Insufficient evidence to confirm or deny; conflicting sources without resolution

**OPINION/SUBJECTIVE:** Matter of opinion or judgment rather than verifiable fact

### Confidence Level

Rate confidence based on evidence quality:

**HIGH (85-100%):** Multiple independent reliable sources confirm; primary sources available; peer-reviewed research; expert consensus

**MEDIUM (60-84%):** Reliable secondary sources; some corroboration; expert opinion without full consensus; indirect evidence

**LOW (40-59%):** Limited sources; moderate credibility; circumstantial evidence; conflicting information exists

### Supporting Evidence

For each claim, document:
- **Source citations** with web_fetch URLs when available
- **Key evidence** from each source (paraphrased, never quoted directly)
- **Source type** (peer-reviewed study, government data, news report, etc.)
- **Publication date** and relevance
- **Author credentials** when relevant

### Contradictory Evidence

Document any conflicting information found:
- Note sources that contradict the claim
- Explain discrepancies when possible
- Highlight areas of ongoing debate
- Flag when context changes interpretation

## Step 4: Source Quality Rating

Evaluate each source using these criteria (see references/verification_methodology.md for scoring details):

**Calculate credibility score (1-100) based on:**

1. **Source Credibility (40% weight)**
   - Peer-reviewed academic: 90-100 points
   - Government/official statistics: 80-95 points
   - Reputable news organization: 70-85 points
   - Expert opinion: 60-80 points
   - General news/blogs: 30-60 points
   - Anonymous/unverified: 0-30 points

2. **Methodology Quality (30% weight)**
   - Research design rigor
   - Sample size adequacy
   - Statistical significance
   - Control measures
   - Transparency of methods

3. **Recency (15% weight)**
   - Within 6 months: 90-100 points
   - 6 months to 2 years: 70-89 points
   - 2-5 years: 50-69 points
   - Over 5 years: 0-49 points
   - (Note: Less important for historical facts)

4. **Corroboration (15% weight)**
   - 5+ independent sources: 90-100 points
   - 3-4 independent sources: 70-89 points
   - 1-2 sources: 50-69 points
   - Uncorroborated: 0-49 points

**Total Score Formula:**
(Source Credibility × 0.40) + (Methodology × 0.30) + (Recency × 0.15) + (Corroboration × 0.15)

## Step 5: Report Generation

Compile findings using this structure:

### Executive Summary
Provide a concise overview (2-4 sentences):
- Overall credibility assessment
- Number of claims verified/debunked
- Key findings or concerns
- Overall credibility score (1-100)

### Detailed Findings

For each claim, include:

```
**Claim [number]: [Statement of claim]**

**Verification Status:** TRUE/FALSE/PARTIALLY TRUE/UNVERIFIED/OPINION
**Confidence Level:** HIGH/MEDIUM/LOW ([percentage]%)

**Assessment:**
[2-3 sentence explanation of verification outcome]

**Supporting Evidence:**
- [Source 1] (Credibility: [score]/100) - [Key evidence paraphrased]
- [Source 2] (Credibility: [score]/100) - [Key evidence paraphrased]
- [Additional sources as needed]

**Contradictory Evidence:**
[Any conflicting information, or "None found"]

**Context Notes:**
[Important context, limitations, or caveats]
```

### Source Bibliography

List all sources with credibility ratings:

```
1. [Source Title/Organization]
   - URL: [link]
   - Type: [Peer-reviewed/Government/News/etc.]
   - Date: [publication date]
   - Credibility Score: [score]/100
   - Strengths: [key strengths]
   - Limitations: [any concerns]

2. [Continue for all sources...]
```

### Overall Credibility Score

Provide aggregate assessment:

**Content Credibility: [score]/100**

**Breakdown:**
- Claims verified as TRUE: [X]
- Claims verified as FALSE: [X]
- Claims PARTIALLY TRUE: [X]
- Claims UNVERIFIED: [X]
- Average source credibility: [score]/100

**Interpretation:**
[Explanation of what the score means]

### Recommendations

Provide actionable recommendations:

**For further verification:**
- [Specific actions to improve verification]
- [Additional sources to consult]
- [Areas requiring expert review]

**Red flags identified:**
- [Any concerning patterns or issues]
- [Potential biases or conflicts of interest]
- [Methodological concerns]

## Domain-Specific Guidance

### Medical/Health Claims
- Prioritize PubMed, peer-reviewed medical journals, CDC, WHO
- Check for FDA approval status when relevant
- Look for clinical trial data and meta-analyses
- Be extremely cautious with anecdotal evidence
- Consider medical consensus

### Scientific Claims
- Search Google Scholar and peer-reviewed journals
- Check for replication studies
- Verify methodology and sample sizes
- Look for scientific consensus
- Note any conflicts of interest

### Statistical Claims
- Verify with original government databases
- Check sampling methodology and margin of error
- Look for statistical significance
- Consider time period and context
- Watch for cherry-picking

### Historical Claims
- Seek primary sources and archival records
- Cross-reference with multiple historians
- Check academic historical journals
- Consider historiographical debates
- Note source dating

### Political Claims
- Use nonpartisan fact-checkers (Snopes, PolitiFact, FactCheck.org)
- Verify with official government records
- Check for full context and complete quotes
- Compare multiple news sources
- Note partisan framing

### Breaking News
- Exercise extra caution with very recent claims
- Flag information as preliminary when appropriate
- Verify with multiple independent sources
- Note when situation is still developing
- Update assessment as more information emerges

## Important Guidelines

**Always:**
- Search for both supporting and contradictory evidence
- Paraphrase information from sources (never quote directly)
- Cite sources with URLs when available
- Acknowledge uncertainty when evidence is limited
- Consider context and nuance
- Note when claims are matters of opinion
- Flag outdated information
- Disclose methodological limitations

**Never:**
- Accept first source without verification
- Ignore publication dates
- Overlook conflicts of interest
- Cherry-pick supporting evidence
- Quote copyrighted content directly
- Make overconfident claims
- Ignore credible contradictory evidence
- Conflate correlation with causation

**When evidence is limited:**
- Clearly state limitations
- Mark claims as UNVERIFIED
- Suggest additional verification methods
- Note what information would be needed for verification

## Reference Materials

This skill includes detailed reference documentation:

### references/trusted_sources.md
Comprehensive guide to reliable sources organized by category:
- Academic and research databases (Google Scholar, PubMed, arXiv, etc.)
- Government and official statistics (Census, CDC, WHO, etc.)
- Fact-checking organizations (Snopes, PolitiFact, FactCheck.org, etc.)
- Reputable news organizations with editorial standards
- Domain-specific sources (medical, scientific, legal, economic)
- Source quality criteria and red flags

**When to reference:** For guidance on which sources to prioritize for different types of claims.

### references/verification_methodology.md
Detailed methodology for verification and credibility assessment:
- Verification status definitions and criteria
- Confidence level guidelines
- Credibility scoring methodology with formulas
- Special considerations for different domains
- Common verification pitfalls and best practices
- Context assessment frameworks

**When to reference:** For detailed guidance on assigning verification statuses, calculating credibility scores, or applying domain-specific verification methods.
