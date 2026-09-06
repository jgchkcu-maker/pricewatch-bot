# Hybrid Match Learning Engine Design

## Goal

Combine marketplace-native taxonomy, deterministic product identity rules, entity-resolution patterns, probabilistic scoring, active learning, hard-negative mining, and query-performance learning into one precision-first pipeline for PriceWatch.

## Safety invariant

The learning layer may rank or resolve uncertain candidates, but it may never override an explicit hard contradiction. Only detail-verified or explicitly reviewed evidence may update model weights. Search output alone never trains the matcher.

A learned probability is never ground truth. During exact detail verification, deterministic identity evidence plus hard-veto checks supply the verified label. The scorer is calibrated toward that label, even when its pre-update probability was wrong.

## Hot-path pipeline

1. Search in marketplace-native category scope when known.
2. Deduplicate concrete marketplace offers.
3. Taxonomy gate: explicit native category contradiction -> reject.
4. Blocking/features: product type, brand/model anchors, identifiers, identity attributes.
5. Hard vetoes: excluded accessory concept, explicit attribute contradiction, exact identifier contradiction, conflicting model/variant tokens.
6. Deterministic matcher.
7. Lightweight probabilistic scorer for remaining uncertainty.
8. Result: ACCEPT, REJECT, or AMBIGUOUS/active-learning queue.
9. Alert-worthy ACCEPT candidates still require detail verification before a price is trusted.
10. Exact detail identity provides the verified learning label; scorer probability only records pre-learning confidence.

## Identifier hierarchy

When both sides provide identifiers, compare in this order:

1. GTIN
2. UPC
3. EAN
4. MPN + brand
5. Model + brand
6. Identity attributes
7. Normalized title/required-token similarity

Exact identifier conflicts are hard vetoes. Exact identifier matches are strong evidence, but do not bypass a different marketplace category or explicit variant contradiction.

The SearchPlan LLM may preserve strong identifiers and explicit brand/model hints, but must never invent GTIN, EAN, UPC, MPN, SKU, article numbers, or manufacturer codes.

## Feature vector

The scorer uses deterministic features only:

- taxonomy agreement
- exact identifier agreement
- brand evidence
- model evidence
- required-token coverage
- identity-attribute coverage
- normalized title token similarity
- model-like token overlap
- deterministic matcher status
- explicit contradictions/veto flags

No price is used as positive identity evidence. Extreme price differences may trigger verification later, but a cheap price never makes two products more likely to be identical.

## Online probabilistic model

Use a tiny pure-Python online logistic model. Ship conservative default weights so the system works before training. Update weights only from verified positive/negative labels. Hard vetoes always dominate the probability.

The first implementation is intentionally dependency-free; a future offline job may replace/calibrate the weights with LightGBM/Splink-style statistics once enough clean labels exist.

## Verified-label authority

Detail verification deliberately separates two questions:

- deterministic identity: does the concrete detail card satisfy the requested product identity without a hard contradiction?
- learned confidence: how confident was the current scorer before seeing the verified label?

The first answer becomes `verified_label`. The second is stored as evidence and then updated toward the verified label. This prevents a self-training feedback loop where an uncalibrated model could manufacture its own negative labels.

## Active learning

Candidates near the decision boundary are placed into an uncertainty queue. Queue priority is highest near probability 0.5 and for candidates that are commercially dangerous (close sibling variant rather than random unrelated item).

Resolution sources may later be detail parsing, an LLM judge, or human review. Only resolved labels become training evidence. Once a candidate receives a verified label, it is removed from the uncertainty queue so a long-lived worker does not accumulate already-resolved items.

## Hard-negative mining

Mine confusing rejected candidates into buckets without training on them automatically:

- sibling_model
- variant_conflict
- identifier_conflict
- accessory
- taxonomy_conflict

Examples: Pad 7 Pro vs Pad 7, 128 GB vs 256 GB, 12/256 vs 8/256, case vs tablet. These are more valuable than random negatives.

Hard negatives are deduplicated by concrete marketplace offer identity plus bucket so repeated four-minute scans do not grow memory indefinitely with the same bad listing.

## Query/alias learning

Track per query:

- runs
- raw candidates
- unique candidates
- accepted candidates
- verified unique matches
- verified rejects

Rank aliases primarily by verified unique yield and secondarily by candidate yield, with penalties for verified rejects. The primary query remains mandatory every four minutes; learning only changes which supplemental alias gets the limited extra slot.

Alias selection uses exploration/exploitation:

1. cold aliases are explored before learned ranking is trusted;
2. before any verified evidence exists, preserve deterministic round-robin behavior;
3. after verified evidence exists, prefer the best verified-yield alias;
4. periodically force exploration of less-used aliases so early noise cannot permanently lock the system onto one spelling.

## Evidence and provenance

Each learning record stores:

- tracked product identity
- marketplace/listing/variation identity
- feature vector
- probability before learning
- decision and reasons
- source query/queries
- taxonomy snapshot where available
- label source: detail, manual, llm_judge
- verified label

The in-memory v1 exposes persistence-ready records; PostgreSQL persistence is the next storage step. Until persistence is added, learning survives within a long-lived worker process but not a worker restart.

## Relationship to existing components

- `taxonomy.py` remains the marketplace-native first gate and blocking scope.
- `matching.py` remains the deterministic source of hard identity rules.
- `match_learning.py` adds feature extraction, scoring, uncertainty, hard-negative mining, evidence, adaptive alias selection, and query performance.
- `scan.py` invokes the hybrid engine after taxonomy and before returning accepted/ambiguous candidates; it uses query performance to choose the supplemental alias while preserving the fixed primary cadence.
- `verification.py` records verified positive/negative evidence and is the only automatic training source. Deterministic detail identity, not scorer probability, supplies the automatic label.

## Multimodal evidence

Image hashes/embeddings are intentionally not in the four-minute hot path. A future discovery-only stage may attach an image fingerprint as supporting evidence. Image similarity may increase confidence but may never override model/capacity/category/identifier contradictions.
