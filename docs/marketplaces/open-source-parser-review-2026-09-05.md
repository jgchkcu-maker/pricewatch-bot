# Open-source marketplace parser review — 2026-09-05

This document records implementation ideas verified from multiple current public projects and
captured marketplace payloads. PriceWatch reimplements the useful patterns; it does not vendor or
copy external parser source files.

## Ozon sources reviewed

### `eduard256/ozon-mcp-server`
Useful patterns:
- pure, side-effect-free parsers over saved composer payloads;
- treat `widgetStates` values as either JSON strings or decoded objects;
- identify volatile widget keys by the exact semantic name before the first `-`;
- detail data is split across stable semantic widgets such as `webGallery`,
  `webProductHeading`, `webPrice`, `webShortCharacteristics`, and `webCurrentSeller`;
- `webPrice` exposes distinct `cardPrice`, `price`, `originalPrice`, and `isAvailable` fields;
- a bare numeric SKU can be resolved through `/product/<sku>/` inside composer.

PriceWatch deliberately differs in one important place: `cardPrice` is conditional Ozon-card/bank
pricing and is **not** the default historical price. `webPrice.price` is used as the public price,
while `cardPrice` is stored separately in `conditional_prices`.

### `Vladimir-Human/ru-marketplace-mcp`
Useful patterns:
- missing/ambiguous price is `None`, never `0`;
- distinguish transport/access failures from parser/schema drift;
- bounded response bodies and wall-clock timeouts;
- fail loudly when required payload shape disappears rather than returning plausible empty data;
- stable widget-name/shape fingerprints for drift detection;
- fixture-driven offline parser tests and separate live probes;
- strict path/host validation when a browser/session transport is used;
- no automatic CAPTCHA solving.

PriceWatch already adopts the first four rules in the core transport/parser. A tri-state adapter
self-check (`healthy`, `drift`, `inconclusive/access`) is a follow-up observability task.

### `d3c0r1x/ozon-price-tracker`
Useful patterns:
- detect Ozon anti-bot/self-referential `307` redirects and do not follow/retry them indefinitely;
- keep notification rules separate from transport/parsing;
- parse product URLs/SKUs explicitly.

Rejected pattern:
- inferring whether an arbitrary large numeric price is kopecks by magnitude. A legitimate
  expensive product can exceed the heuristic threshold and become a false ultra-low price.
  PriceWatch parses Ozon detail prices from explicit display-price strings instead.

### `Metridat/ozon_parser`
Useful findings:
- current search data is carried in `widgetStates["tileGridDesktop-..."]`;
- the search-card identity is `item["sku"]`, not an unrelated button `skuId`;
- search order/top-N is unstable and can change between requests, reinforcing the need for
  primary-query scans plus rotating aliases and deeper periodic discovery.

Browser anti-detection tactics from this project are intentionally not part of PriceWatch core.

### Older Ozon parsers/gists
Older examples confirmed the long-lived composer/widget concept but often rely on one hard-coded
widget (`webSale`), static cookies/headers, regex extraction, or obsolete response shapes. They are
useful historical evidence, not implementation references.

## Wildberries sources reviewed

### `Vladimir-Human/ru-marketplace-mcp`
Strong current reference with captured 2026 payloads:
- search: `search.wb.ru/exactmatch/ru/common/v9/search`;
- detail: `card.wb.ru/cards/v4/detail`;
- search cards expose product data and `sizes[]` variations;
- detail v4 exposes exact `optionId`, stock and price;
- captured search and card prices can differ for the same product at nearly the same time.

PriceWatch therefore uses search as a cheap radar and re-fetches the exact card/variation before
trusting an important low-price alert.

### Other WB parsers (`shndo1337`, `eduard256`, older community projects)
They confirm the broad internal-API approach but many older projects still use v2/v4/v7 search
routes or collapse price/variation data. PriceWatch prefers the freshest verified v9 search + v4
card shapes and keeps each `sizes[].optionId` as a separate candidate.

## Adopted invariants

1. Search is candidate generation, never proof of product identity or final price.
2. Every accepted alert-worthy candidate gets a concrete detail verification when the marketplace
   supports it.
3. Ozon SKU and WB `nm`/`optionId` are preserved as marketplace identities.
4. Marketplace conditional prices are never mixed silently with the public price series.
5. Missing price is never converted to zero.
6. Required-schema disappearance produces `ParserDriftError`, not an empty product list.
7. Access denial/rate limiting/redirect challenges are transport states, not parser states.
8. Parsers are pure enough to test against trimmed real-shape fixtures offline.
9. Volatile widget instance ids are ignored; semantic widget names are matched exactly.
10. No hot-loop LLM calls and no automatic CAPTCHA-solving/bypass behavior.

## Current PriceWatch status after this review

- WB v9 search parser: implemented.
- WB v4 exact-offer verification: implemented.
- Ozon composer search parser: implemented.
- Ozon PDP/detail verification: implemented.
- Ozon public vs Ozon-card conditional price separation: implemented.
- Ozon exact SKU verification: implemented.
- Ozon detail availability cross-check (`webPrice` vs `webSale`): implemented.
- HTTP body cap, no redirect following, 429/access distinction: implemented.
- Marketplace self-check/circuit-breaker and parser-shape canaries: next reliability layer.
