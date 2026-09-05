# Ozon / Wildberries search behavior — 2026-09-05

This note records the assumptions used by PriceWatch search adapters and query planning. Marketplace internals are not treated as stable contracts; response parsers are fixture-tested and must fail loudly on schema drift.

## Product-search strategy

PriceWatch does **not** generate every word-order permutation of a product name.

For each tracked product:
- one high-precision `primary_query` runs on every fast scan (~4 minutes);
- every second fast scan adds one rotating semantic alias;
- deep discovery can sweep all aliases/pages at a slower cadence;
- results from all queries are deduplicated by marketplace/listing/variation/seller before matching;
- search membership is candidate generation only, never proof that the listing is the right product.

Aliases should represent genuinely different marketplace expressions: compact model spelling, common transliteration, abbreviation, or a known model code already present in the user's request. Separator-only variants (`8/256`, `8+256`, `8-256`) collapse to the same whitespace-normalized query (`8 256`).

## Wildberries

### Search semantics

Current Wildberries seller documentation says that search/ranking uses product name, description, and characteristics. The current card-creation rules explicitly discourage SEO/synonym stuffing and special-character-heavy names, and say words required for search should be present naturally while structured characteristics improve search visibility.

Implications for PriceWatch:
- exact title matching is insufficient;
- slash/plus variants should not each consume a query slot;
- structured attributes are valuable matching evidence even when the short title omits them;
- top search position is not pure lexical relevance because seller/card/delivery/conversion/promotion factors also affect ranking;
- discovery needs enough breadth and periodic deeper sweeps rather than assuming page 1 is exhaustive.

Current captured WB search responses also expose `metadata.normquery`, which confirms server-side query normalization. PriceWatch therefore treats the LLM as a semantic alias generator, not a permutation generator.

### Current search response shape used by the adapter

Current live captures in August 2026 use:

```text
GET https://search.wb.ru/exactmatch/ru/common/v9/search
```

with catalog parameters such as:

```text
appType=1
curr=rub
dest=<configured crawl destination>
locale=ru
query=<query>
resultset=catalog
page=<n>
spp=30
```

Relevant response fields:

```text
products[].id
products[].brand
products[].name
products[].supplier
products[].supplierId
products[].totalQuantity
products[].sizes[].optionId
products[].sizes[].price.basic
products[].sizes[].price.product
```

WB price integers are interpreted as kopecks and converted to RUB by the parser.

A listing can contain several `sizes` / option IDs. PriceWatch emits one candidate per option instead of choosing the lowest option blindly; collapsing these variants would create false lows.

### Search price is a preview

Recent live capture evidence shows a WB search response and card endpoint can expose different prices for the same SKU within the same observation period. Therefore:
- search price is suitable as the cheap 4-minute radar signal;
- a new/suspicious low must be verified from the concrete offer/card before alert fan-out;
- a verified observation must record whether its price came from `search` or `offer/card`.

### Operational guardrails

WB endpoint versions have changed historically. The `v9` URL is an implementation detail, not a permanent API contract. Parser drift must be observable and the endpoint/version must remain isolated inside the adapter.

Deep discovery also needs hard page limits and repeated-result detection because community captures have observed later pages repeating earlier results rather than returning a clean empty page.

## Ozon

### Search semantics

Ozon's seller analytics exposes the multiple search queries through which a single product receives impressions/orders and encourages sellers to reflect important material/product attributes in card data. This is sufficient reason not to model Ozon search as exact-title lookup.

Implications for PriceWatch:
- use a concise semantic query set rather than word-order permutations;
- validate the returned SKU/title/attributes independently;
- treat search result ranking as discovery, not product identity.

### Current composer search shape used by the adapter

Fresh August 2026 open-source live captures use:

```text
GET https://www.ozon.ru/api/composer-api.bx/page/json/v2
    ?url=/search/?text=<query>&page=<n>
```

The response has `widgetStates`. Product tiles are encoded in values whose key starts with `tileGridDesktop-`. The widget value is JSON text containing `items`.

Relevant fields currently used:

```text
item.sku (fallback item.id)
item.action.link
item.mainState[]
```

Within `mainState`:
- `type == "priceV2"` contains price rows;
- `textStyle == "PRICE"` is treated as current search price;
- `textStyle == "ORIGINAL_PRICE"` is treated as original/strikethrough price;
- `type == "textDS"` with `id == "name"` or `automatizationId == "tile-name"` supplies title text.

Tracking query parameters are removed when a canonical Ozon product URL is stored.

### Access / anti-bot behavior

The composer endpoint is not an official public scraping API. Public captures report redirect/access differences between network environments. PriceWatch does not attempt to bypass access controls. Its transport therefore:
- does not automatically follow redirects;
- classifies 401/403/3xx separately from parser errors;
- classifies 429 with `Retry-After` where available;
- treats HTML/challenge pages returned with HTTP 200 as access errors rather than `0 results`;
- performs no internal retries; scheduler-level backoff/circuit breaking owns retry policy.

## Query-plan implications

A good universal SearchPlan for `Xiaomi Pad 7 8/256` is conceptually:

```json
{
  "canonical_name": "Xiaomi Pad 7 8/256",
  "product_type": "tablet",
  "primary_query": "xiaomi pad 7 8 256",
  "aliases": [
    "xiaomi pad7 8 256",
    "сяоми пад 7 8 256"
  ],
  "required_tokens": ["xiaomi"],
  "excluded_terms": ["pad 7 pro", "чехол", "case", "клавиатура"],
  "identity_attributes": {
    "model": "pad 7",
    "ram": "8 gb",
    "storage": "256 gb"
  }
}
```

This is deliberately not a category-specific schema. For an SSD, television, drill, sneakers, or refrigerator the identity attribute keys can be completely different.

## What still requires live validation before production

- Ozon/WB request success rate from the actual deployment network.
- Search-result count/yield per query alias.
- How quickly a newly created/discounted offer propagates into marketplace search.
- Card/detail verification endpoints and their price semantics.
- Repeated-page behavior under deep discovery.
- Which aliases produce unique accepted listings rather than duplicated noise.

The last point should eventually feed an adaptive alias score: aliases that discover no unique valid offers for a sustained period should be queried less often, while aliases that discover unique offers should retain a larger search budget.
