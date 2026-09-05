# PriceWatch Fast Radar Design

## Goal
Build a universal consumer price radar that discovers and refreshes marketplace offers for each globally deduplicated tracked product roughly every four minutes, judges prices against a rolling seven-day market window, and sends fast Telegram alerts without putting an LLM in the hot polling loop.

## Core invariants
- A tracked product is global. User subscriptions point to it; user count never multiplies scraping work.
- The normal monitoring cadence target is 4 minutes per active tracked product.
- Price intelligence uses a rolling 7-day window. Older events may be retained for audit/reprocessing but do not affect the main deal decision.
- LLM runs when a new product/search plan is created or when a candidate is genuinely ambiguous. It does not run on every polling cycle.
- Product identity is universal and attribute-driven: `brand`, `product_name`, `product_type`, plus arbitrary `identity_attributes` generated for the specific product. No mandatory region/revision taxonomy.
- Marketplace search is discovery, not proof of identity. Every result must pass the matcher.
- Every marketplace is implemented through a dedicated adapter. There is no single universal HTML scraper.
- Search plans contain a small set of high-precision and high-recall query aliases. The primary query is run frequently; aliases are rotated; a deeper sweep runs less often.
- The alert path is idempotent. A suspicious dramatic low gets an immediate verification fetch before broadcast.

## Search behavior findings informing the design
### Wildberries
Current WB seller documentation says search visibility is influenced by a card's name, description and characteristics. WB also says necessary search words should be included naturally in description text without special characters, and warns against synonym stuffing. Search can originate from names, article numbers, suggestions, brands, sellers, categories and tags. Therefore query generation must not rely on exact title formatting and must include whitespace-normalized aliases such as `8 256` in addition to user forms like `8/256` or `8+256`.

### Ozon
Ozon exposes seller analytics showing that one product can receive views/sales from multiple distinct search queries and recommends adding terms users actually search for into card characteristics/content. Search ranking also incorporates relevance rather than being an exact-title lookup. Therefore Ozon discovery should likewise use a compact semantic query set rather than exhaustive title permutations.

## Product/search model
```text
TrackedProduct
- id
- canonical_name
- brand?
- product_name
- product_type?
- identity_attributes: map[str, str]
- negative_attributes: map[str, list[str]]
- accessory_terms: list[str]
- search_plan_version
- created_at

SearchPlan
- product_id
- primary_query
- high_precision_queries[]
- high_recall_queries[]
- required_tokens[]
- optional_tokens[]
- excluded_terms[]
- identity_attributes{}
- generated_by
- generated_at
```

The LLM must return JSON matching the SearchPlan schema. Search-plan generation is provider-agnostic.

## Search execution
Each active product has `next_scan_at`. The scheduler leases due products and executes one scan per marketplace.

A normal 4-minute scan:
1. Run marketplace adapter search using `primary_query`, except when alias rotation selects another query for this cycle.
2. Refresh already known cheap/recent offers where a direct listing fetch is available.
3. Extract candidates into marketplace-neutral records.
4. Apply deterministic normalization and hard exclusions.
5. Match candidate to tracked product.
6. Persist accepted offers/current price state and event-based price changes.
7. Evaluate rolling seven-day low / target-price rules.
8. If price is suspiciously low, enqueue immediate verification before alerting.

A deep-discovery sweep runs every 30-60 minutes using all aliases and may inspect more pages/results.

## Query rotation
For a plan with `Q0` as primary and aliases `Q1..Qn`:
```text
cycle 0 Q0
cycle 1 Q1
cycle 2 Q0
cycle 3 Q2
cycle 4 Q0
cycle 5 Q3
...
```
This maintains four-minute discovery while expanding recall without multiplying every cycle by the number of aliases.

## Matching
The initial matcher is deterministic and explainable.

Hard reject examples:
- explicitly different model/family (`Pad 7 Pro` vs `Pad 7`)
- explicitly contradictory critical identity attribute (`12 GB` vs required `8 GB`)
- accessory-only result (`case`, `cover`, `screen protector`, etc.)
- explicitly used/refurbished if the tracked target is new

Accept when required identity evidence is present and no hard contradiction exists.
Ambiguous results are returned as `AMBIGUOUS`, not forced into accept/reject. A later LLM ambiguity resolver can inspect only those records.

## Price storage
- `offer_current_state` holds current price, availability and last-seen timestamp.
- `price_event` is appended only when normalized price changes.
- Main intelligence queries only the last 7 days.
- Event rows retain provenance: marketplace, listing/offer id, query used, parser version and observed timestamp.

## Seven-day deal logic
Minimum MVP signals:
- current price
- 7-day minimum
- 7-day median
- percentage below 7-day median

A new seven-day low alerts only when it improves the previous low by a configurable meaningful delta. Extremely large drops are marked suspicious and require immediate re-fetch confirmation.

## Marketplace adapter contract
```python
class MarketplaceAdapter(Protocol):
    marketplace: str

    async def search(self, query: str, *, limit: int = 50) -> list[SearchCandidate]: ...
    async def fetch_offer(self, locator: OfferLocator) -> OfferSnapshot: ...
```
Extraction output must be marketplace-neutral. Search/fetch implementation details stay inside the adapter.

## Initial code slice
The first implementation slice will deliberately not perform live scraping yet. It creates:
- Python project scaffold and tests
- universal SearchPlan data model
- deterministic query normalizer/rotator
- candidate matcher with hard contradictions and ambiguity result
- marketplace adapter protocol and neutral candidate models
- four-minute scheduler primitives for globally deduplicated products
- event-based seven-day price state helpers

Real Ozon and WB adapters will be the next slice after their live request/response behavior is captured safely as fixtures.

## Non-goals for the first slice
- Telegram UI
- database migrations
- browser automation
- CAPTCHA handling/bypass
- production proxy infrastructure
- seller rating
- region/revision catalog
- prediction/ML
- ClickHouse/Redis/Timescale

## Acceptance criteria for the first slice
- Search alias normalization produces separator-free aliases appropriate for WB/Ozon search.
- Query rotation guarantees the primary query at least every second cycle while eventually exercising every alias.
- Matcher rejects wrong model, wrong critical attribute and accessory examples; accepts exact examples; returns ambiguity when evidence is missing.
- Scheduler produces ~4-minute next-scan times and operates per unique tracked product, not per subscription.
- Price-state helper emits a new event only on actual price change and computes seven-day minimum/median from in-window events.
- All behavior is covered by tests.
