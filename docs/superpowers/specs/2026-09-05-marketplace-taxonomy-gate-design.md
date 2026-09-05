# Marketplace Taxonomy Gate Design

## Goal

Reduce irrelevant marketplace search results before exact-product matching, especially accessories such as cases, glass and stands returned for a tablet query.

The gate must remain universal: it may use marketplace-native taxonomy when known, but it must not reject results for a new product type merely because no taxonomy mapping exists yet.

## Decision

Use a hybrid taxonomy registry.

1. `SearchPlan.product_type` remains the universal semantic type produced by the LLM, for example `tablet`, `smartphone`, `ssd`.
2. The registry maps `(product_type, marketplace)` to marketplace-native taxonomy constraints.
3. A known constraint is strict: candidates that explicitly contradict it are rejected before the identity matcher.
4. Missing taxonomy evidence is not treated as a contradiction. The candidate continues to the identity matcher.
5. Unknown product types fall back to global search and matching; the system can accumulate trusted observations for later mapping rather than guessing.

This is preferred over a giant accessory stop-word list because words are context-dependent (`keyboard` is a product type itself but an accessory for many tablets) and over LLM-per-result classification because the hot path runs every four minutes.

## Data model

Add neutral marketplace taxonomy metadata:

- `MarketplaceTaxonomy`
  - `subject_id: str | None` — WB native `subjectId`
  - `parent_id: str | None` — WB native `subjectParentId`
  - `entity: str | None` — WB native entity/category label
  - `category_path: str | None` — Ozon category-scoped search path

`SearchCandidate` carries `taxonomy: MarketplaceTaxonomy | None`.

Add `TaxonomyConstraint`:

- `marketplace`
- `product_type`
- optional `subject_ids`
- optional `entities`
- optional `category_path`

The first seed mapping is intentionally small and evidence-backed:

- `tablet` + Wildberries: subject id `107`
- `tablet` + Ozon: category path `/category/planshety-15525/`

The registry API allows adding mappings without changing adapters or matching code.

## Retrieval flow

For every four-minute scan:

1. Resolve the taxonomy constraint for `(SearchPlan.product_type, marketplace)`.
2. Build marketplace search requests.
   - WB still uses its v9 global search API; native taxonomy is read from each returned product and filtered locally before exact matching.
   - Ozon uses category-scoped retrieval when `category_path` is known: `<category_path>?text=<query>&page=<page>` through composer-api. Unknown types keep `/search/?text=...`.
3. Parse candidates including marketplace taxonomy metadata.
4. Apply taxonomy gate.
   - explicit match -> pass
   - explicit contradiction -> reject
   - missing taxonomy metadata / unknown constraint -> pass as unknown
5. Deduplicate remaining candidates.
6. Run existing exact identity matcher.
7. Only exact/verified candidates may contribute trusted taxonomy observations.

## Gate semantics

The gate is fail-safe for recall:

- Known WB subject id `107`, candidate subject id `107`: PASS.
- Known WB subject id `107`, candidate subject id `203`: REJECT.
- Known WB subject id `107`, candidate has no subject id but entity says `Планшеты`: PASS if normalized entity matches a configured entity; otherwise UNKNOWN, not reject.
- No mapping for product type: UNKNOWN, not reject.
- Marketplace payload schema disappears: parser drift remains a parser/health error; do not convert it into taxonomy mismatch.

Taxonomy rejection is independent from identity rejection. This keeps diagnostics useful: `wrong_category` is different from `wrong_model` or `wrong_storage`.

## Learning

Add an in-memory observation accumulator as a domain primitive, not yet database persistence.

A taxonomy mapping may be proposed only from candidates that already passed exact identity matching. The accumulator counts distinct listing ids by taxonomy signature. A mapping becomes learnable only after at least three distinct accepted listings agree on the same non-empty native taxonomy signature and no competing signature has equal support.

Automatic persistence/application of learned mappings is deferred until PostgreSQL exists. The current implementation exposes the proposed constraint so a later persistence layer can store it.

## Error handling

- Unknown taxonomy: continue; never invent a category.
- Explicit contradictory taxonomy: reject before exact matching.
- Missing Ozon scoped category mapping: use global search.
- Malformed marketplace taxonomy fields: preserve candidate with missing taxonomy unless the surrounding marketplace response itself violates an existing parser contract.
- A category-scoped Ozon request that returns transport/challenge errors is handled by the existing transport/circuit-breaker path; no fallback retry storm inside the adapter.

## Testing

Fixture and unit tests must cover:

- WB parser extracts `subjectId`, `subjectParentId`, `entity`.
- Tablet gate accepts WB subject `107` and rejects a case/accessory subject.
- Missing taxonomy remains `UNKNOWN` and reaches the identity matcher.
- Ozon tablet search builds `/category/planshety-15525/?text=...` instead of global `/search/`.
- Unknown Ozon product type remains global search.
- Scan outcome tracks taxonomy-rejected candidates separately.
- Taxonomy learning requires three distinct accepted listings and does not learn from one result.
- Existing matching, verification and price tests remain green.

## Non-goals

- Building a complete marketplace taxonomy database now.
- Calling an LLM for every candidate.
- Treating category as sufficient proof of exact product identity.
- Rejecting bundles solely because an accessory word appears in a title.
- Persistent learned taxonomy storage before the PostgreSQL layer exists.
