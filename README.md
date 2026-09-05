# PriceWatch Bot

Universal high-frequency marketplace price radar core.

## Current design

PriceWatch treats each tracked product as a globally deduplicated entity. Ten or ten thousand users watching the same product do not create duplicate scraping jobs.

Core monitoring rules:
- active tracked product scan target: ~4 minutes;
- primary search query runs every fast scan;
- one semantic alias is added periodically; aliases are not word-order permutations;
- product identity is universal and attribute-driven;
- LLM creates/updates a SearchPlan outside the hot polling loop;
- marketplace search is discovery, not proof of product identity;
- search price is a preview signal; alert-worthy lows should be verified against the concrete offer/card where supported;
- main deal logic uses a rolling 7-day price window;
- unchanged prices do not create duplicate price events.

See:
- `docs/superpowers/specs/2026-09-05-pricewatch-fast-radar-design.md`
- `docs/marketplaces/search-behavior-2026-09-05.md`

## Implemented

- SearchPlan normalization and fast query budget
- strict LLM SearchPlan contract (`gemini-3.5-flash-lite` default model identifier)
- deterministic product matcher with `ACCEPT` / `REJECT` / `AMBIGUOUS`
- unit/compact-name normalization (`8ГБ` vs `8 GB`, `Pad7` vs `Pad 7`)
- globally deduplicated four-minute scheduler primitives
- event-based rolling seven-day price state
- marketplace-neutral adapter contracts
- Wildberries v9 search parser and request adapter
- Ozon composer search parser and request adapter
- bounded non-retrying HTTP JSON transport
- Wildberries card-v4 exact-offer verification
- product identity recheck after a concrete offer fetch
- fixture-driven schema-drift tests

## Intentionally not implemented yet

- PostgreSQL persistence/migrations
- production worker/scheduler loop
- Telegram bot UX
- Ozon concrete-offer verification (waiting for a current detail-response fixture)
- LLM API transport/provider wiring
- alert outbox / Telegram rate limiting
- browser automation or any CAPTCHA/access-control bypass
- Redis / ClickHouse / TimescaleDB

## Development

Requires Python 3.12+.

```bash
python -m pip install -e ".[dev]"
ruff check .
pytest -q
```

CI runs the same Ruff + pytest checks on GitHub Actions.

## Marketplace safety

Marketplace response structures are treated as unstable implementation details. Parsing is separated from transport and covered by saved fixtures. Access/rate-limit responses are classified explicitly and must be handled by scheduler backoff/circuit-breaking rather than aggressive retry loops.
