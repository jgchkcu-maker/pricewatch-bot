# Hybrid Match Learning Engine Design

## Goal

Combine marketplace-native taxonomy, deterministic product identity rules, entity-resolution patterns, probabilistic scoring, active learning, hard-negative mining, and query-performance learning into one precision-first pipeline for PriceWatch.

## Safety invariant

The learning layer may rank or resolve uncertain candidates, but it may never override an explicit hard contradiction. Only detail-verified or explicitly reviewed evidence may update model weights. Search output alone never trains the matcher.

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

## Active learning

Candidates near the decision boundary are placed into an uncertainty queue. Queue priority is highest near probability 0.5 and for candidates that are commercially dangerous (close sibling variant rather than random unrelated item).

Resolution sources may later be detail parsing, an LLM judge, or human review. Only resolved labels become training evidence.

## Hard-negative mining

Mine confusing rejected candidates into buckets without training on them automatically:

- sibling_model
- variant_conflict
- identifier_conflict
- accessory
- taxonomy_conflict

Examples: Pad 7 Pro vs Pad 7, 128 GB vs 256 GB, 12/256 vs 8/256, case vs tablet. These are more valuable than random negatives.

## Query/alias learning

Track per query:

- runs
- raw candidates
- unique candidates
- accepted candidates
- verified unique matches
- verified rejects

Rank aliases primarily by verified unique yield and secondarily by candidate yield, with penalties for verified rejects. The primary query remains mandatory every four minutes; learning only changes which supplemental alias gets the limited extra slot.

## Evidence and provenance

Each learning record stores:

- tracked product identity
- marketplace/listing/variation identity
- feature vector
- probability before learning
- decision and reasons
- source query/queries
- taxonomy snapshot
- label source: detail, manual, llm_judge
- verified label

The in-memory v1 exposes persistence-ready records; PostgreSQL persistence is the next storage step.

## Relationship to existing components

- `taxonomy.py` remains the marketplace-native first gate.
- `matching.py` remains the deterministic source of hard identity rules.
- `match_learning.py` adds feature extraction, scoring, uncertainty, hard-negative mining, evidence, and query performance.
- `scan.py` invokes the hybrid engine after taxonomy and before returning accepted/ambiguous candidates.
- `verification.py` records verified positive/negative evidence and is the only automatic training source.

## Multimodal evidence

Image hashes/embeddings are intentionally not in the four-minute hot path. A future discovery-only stage may attach an image fingerprint as supporting evidence. Image similarity may increase confidence but may never override model/capacity/category/identifier contradictions.
