# PriceWatch Fast Radar Design

## Goal
Build a universal consumer price radar that discovers marketplace offers for each globally deduplicated tracked product roughly every four minutes, judges prices against a rolling seven-day market window, and verifies deal candidates before alerting without putting an LLM in the hot polling loop.

## Core invariants
- A tracked product is global. User subscriptions point to it; user count never multiplies scraping work.
- The normal monitoring cadence target is 4 minutes per active tracked product.
- The primary marketplace search query runs on every fast scan. Semantic aliases are supplemental, not replacements for the primary query.
- Price intelligence uses a rolling 7-day window. Older events may be retained for audit/reprocessing but do not affect the main deal decision.
- LLM runs when a new product/SearchPlan is created or when a candidate is genuinely ambiguous. It does not run on every polling cycle.
- Product identity is universal and attribute-driven: optional `product_type` plus arbitrary `identity_attributes`. There is no mandatory region/revision taxonomy.
- Marketplace search is discovery, not proof of identity. Every result must pass the matcher.
- Search-result price is a cheap radar signal. A deal candidate must be verified from the concrete offer/card before it becomes a trusted alert price where a verification endpoint is available.
- Every marketplace is implemented through a dedicated adapter. There is no single universal HTML scraper.
- The network transport performs no internal retry loops. Rate-limit/access/backoff policy belongs to the scheduler/circuit breaker.
- The alert path must be idempotent. A suspicious dramatic low gets immediate verification before broadcast.

## Search behavior findings informing the design
### Wildberries
Current WB seller documentation says search visibility is influenced by card name, description and characteristics. Current card rules discourage synonym/SEO stuffing and special-character-heavy naming, and say search-relevant words should be natural while structured characteristics influence visibility.

Therefore query generation must not rely on exact title formatting. Separator variants such as `8/256`, `8+256` and `8-256` normalize to `8 256`; they do not deserve separate query-budget slots.

Fresh August 2026 captured responses use the `search.wb.ru/.../v9/search` catalog shape and expose product `sizes[].optionId` plus per-option prices. The card detail endpoint uses `card.wb.ru/cards/v4/detail`. Search and card prices can differ, so the card price is the verification source before a low-price alert.

### Ozon
Ozon seller analytics demonstrates that one product can receive impressions/orders through multiple distinct search queries and recommends improving attributes/content based on those queries. Search therefore behaves as semantic discovery rather than exact-title lookup.

Fresh August 2026 captures use the composer endpoint with `widgetStates` and `tileGridDesktop-*` product tiles. The endpoint is not treated as an official stable public scraping API; redirects/access blocks are operational states rather than empty search results.

Detailed notes live in `docs/marketplaces/search-behavior-2026-09-05.md`.

## Product/search model
```text
TrackedProduct
- id
- canonical_name
- product_type?
- identity_attributes: map[str, str]
- search_plan_version
- created_at

SearchPlan
- product_id
- canonical_name
- product_type?
- primary_query
- aliases[] (max 7)
- required_tokens[]
- excluded_terms[]
- identity_attributes{}
- generated_by
- generated_at
```

The LLM must return JSON matching the SearchPlan schema. Search-plan generation is provider-agnostic and its output is validated before use.

## Search execution
Each active product has `next_scan_at`. The scheduler leases due products and executes one fast scan per supported marketplace.

A normal 4-minute scan:
1. Always run `primary_query`.
2. On a configurable subset of cycles, add one rotating semantic alias.
3. Deduplicate results by marketplace/listing/variation/seller.
4. Apply deterministic normalization and hard exclusions.
5. Match candidates to the tracked product.
6. Route only genuinely ambiguous records to a later LLM ambiguity resolver.
7. Treat search prices as preview signals.
8. If an accepted search result represents a new/interesting low, fetch the concrete offer/card where supported and re-run identity matching against the detail snapshot.
9. Only the verified price can create the alert-worthy event.
10. Evaluate rolling seven-day low / target-price rules.

A deep-discovery sweep can run every 30-60 minutes using all aliases and additional pages, with repeated-page detection and hard page limits.

## Fast query budget
For aliases `Q1..Qn`, default fast scans use:
```text
cycle 0: Q0
cycle 1: Q0 + Q1
cycle 2: Q0
cycle 3: Q0 + Q2
cycle 4: Q0
cycle 5: Q0 + Q3
...
```

This preserves four-minute freshness for the strongest query while paying only about 50% extra search requests for alias coverage. Alias frequency can later become adaptive based on unique valid-offer yield.

## Matching
The initial matcher is deterministic and explainable.

Hard reject examples:
- explicitly different model/family (`Pad 7 Pro` vs `Pad 7`)
- explicitly contradictory critical identity attribute (`12 GB` vs required `8 GB`)
- accessory-only result (`case`, `cover`, `screen protector`, etc.)
- explicitly used/refurbished if the tracked target is new

The matcher normalizes common marketplace unit forms (`8ГБ`, `8 GB`) and compact model spelling (`Pad7`, `Pad 7`). Missing evidence yields `AMBIGUOUS`; it is never silently treated as a match.

## Marketplace adapters
Current contracts split search and offer verification:
```python
class MarketplaceSearchAdapter(Protocol):
    async def search(self, query: str, *, limit: int = 50, page: int = 1) -> list[SearchCandidate]: ...

class MarketplaceOfferAdapter(Protocol):
    async def fetch_offer(self, locator: OfferLocator) -> OfferSnapshot: ...
```

`WildberriesSearchAdapter` currently implements both search and card-v4 verification. Ozon search parsing/request construction is implemented; Ozon detail verification remains intentionally unimplemented until its current detail payload is captured and tested.

## Network transport
`HttpJsonFetcher` is deliberately small and non-retrying:
- shared `httpx.AsyncClient` supplied by the application;
- redirects are not followed automatically;
- 429 is classified with `Retry-After` when possible;
- 401/403/3xx become access errors;
- HTTP 200 HTML/challenge content is not misread as empty marketplace JSON;
- response size is bounded;
- retry/circuit-breaker logic belongs above the transport.

No CAPTCHA bypass or access-control circumvention is part of the design.

## Price storage
- `offer_current_state` will hold current price, availability and last-seen timestamp.
- `price_event` will append only when normalized verified price changes.
- Main intelligence queries only the last 7 days.
- Event rows will retain provenance: marketplace, listing/variation ID, query source, parser version, verification source and observed timestamp.

## Seven-day deal logic
Minimum MVP signals:
- current verified price
- 7-day minimum
- 7-day median
- percentage below 7-day median

A new seven-day low alerts only after offer verification. Extremely large deviations may require an additional immediate confirmation fetch before fan-out.

## Implemented first slice
- Python project scaffold and CI
- universal SearchPlan data model
- strict LLM SearchPlan system prompt/output parser
- deterministic query normalizer and fast-query budget
- deterministic candidate matcher with ambiguity state
- marketplace-neutral search/offer contracts
- WB v9 search response parser/request builder
- Ozon composer search response parser/request builder
- bounded non-retrying JSON transport
- WB card-v4 exact-offer verification
- detail-snapshot product-identity recheck
- four-minute scheduler primitives for globally deduplicated products
- event-based seven-day price-state helpers
- fixture-based tests for marketplace response schemas

## Non-goals of this slice
- Telegram UI
- database migrations
- production worker process
- browser automation
- CAPTCHA handling/bypass
- production proxy infrastructure
- seller rating
- mandatory region/revision catalog
- prediction/ML
- ClickHouse/Redis/Timescale

## Acceptance criteria
- Primary search runs every fast cycle; aliases supplement it without N-way permutation spam.
- Search normalization produces separator-free queries appropriate for WB/Ozon behavior.
- Matcher rejects wrong model, wrong critical attribute and accessories; accepts exact examples; returns ambiguity when evidence is missing.
- WB and Ozon search payload shapes are protected by fixture tests and parser-drift errors.
- WB search low can be verified by the exact listing/option through card-v4 and identity is rechecked after detail fetch.
- Transport distinguishes access/rate-limit/schema errors from healthy empty results.
- Scheduler produces ~4-minute next-scan times and operates per unique tracked product, not per subscription.
- Price-state helper emits a new event only on actual price change and computes seven-day minimum/median from in-window events.
- All implemented behavior passes Ruff and pytest in GitHub CI.
