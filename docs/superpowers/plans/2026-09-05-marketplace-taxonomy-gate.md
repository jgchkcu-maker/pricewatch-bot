# Marketplace Taxonomy Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add marketplace-native category filtering before exact-product matching so accessory results are discarded early without sacrificing universality for unknown product types.

**Architecture:** Introduce neutral taxonomy metadata on search candidates, a registry that resolves product-type-specific marketplace constraints, and a fail-safe gate that rejects only explicit contradictions. Wildberries exposes taxonomy in search results; Ozon uses category-scoped search when a known category path exists. Unknown taxonomy falls back to the existing global-search-plus-matcher flow.

**Tech Stack:** Python 3.12, dataclasses, pytest, Ruff, existing marketplace adapters and scan engine.

**Spec:** `docs/superpowers/specs/2026-09-05-marketplace-taxonomy-gate-design.md`

## Global Constraints

- Keep the four-minute primary search cadence unchanged.
- No LLM calls in the per-candidate hot path.
- Unknown taxonomy must not cause rejection.
- Explicit taxonomy contradiction is rejected before exact identity matching.
- Category is not sufficient proof of exact product identity; existing matcher remains mandatory.
- Ozon category scope is used only when a known category path exists.
- Learned mappings are proposed in memory only; no persistence before PostgreSQL exists.

---

### Task 1: Neutral Taxonomy Model and Gate

**Files:**
- Create: `src/pricewatch/taxonomy.py`
- Modify: `src/pricewatch/marketplaces.py`
- Test: `tests/test_taxonomy.py`

**Interfaces:**
- Produces: `MarketplaceTaxonomy`, `TaxonomyConstraint`, `TaxonomyGateStatus`, `TaxonomyGateDecision`, `TaxonomyRegistry`, `taxonomy_gate(candidate, constraint)`.
- `SearchCandidate.taxonomy` becomes `MarketplaceTaxonomy | None`.

- [ ] **Step 1: Write failing tests**

```python
from pricewatch.marketplaces import SearchCandidate
from pricewatch.taxonomy import (
    MarketplaceTaxonomy,
    TaxonomyConstraint,
    TaxonomyGateStatus,
    taxonomy_gate,
)


def test_known_wb_subject_rejects_accessory_category() -> None:
    constraint = TaxonomyConstraint(
        marketplace="wildberries",
        product_type="tablet",
        subject_ids=frozenset({"107"}),
    )
    candidate = SearchCandidate(
        marketplace="wildberries",
        listing_id="1",
        title="Чехол для Xiaomi Pad 7",
        taxonomy=MarketplaceTaxonomy(subject_id="203", entity="Чехлы"),
    )
    assert taxonomy_gate(candidate, constraint).status is TaxonomyGateStatus.REJECT


def test_missing_taxonomy_is_unknown_not_reject() -> None:
    constraint = TaxonomyConstraint(
        marketplace="wildberries",
        product_type="tablet",
        subject_ids=frozenset({"107"}),
    )
    candidate = SearchCandidate(
        marketplace="wildberries",
        listing_id="1",
        title="Xiaomi Pad 7",
    )
    assert taxonomy_gate(candidate, constraint).status is TaxonomyGateStatus.UNKNOWN
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest tests/test_taxonomy.py -q`
Expected: import/module failures because taxonomy types do not exist.

- [ ] **Step 3: Implement the model, registry and gate**

`TaxonomyRegistry` contains evidence-backed seed constraints:

```python
("tablet", "wildberries") -> subject_ids={"107"}
("tablet", "ozon") -> category_path="/category/planshety-15525/"
```

Gate rules:
- no constraint -> UNKNOWN
- no candidate taxonomy -> UNKNOWN
- any configured subject/entity match -> PASS
- candidate has comparable explicit taxonomy and none match -> REJECT
- otherwise -> UNKNOWN

- [ ] **Step 4: Run tests and verify GREEN**

Run: `pytest tests/test_taxonomy.py -q`
Expected: all taxonomy tests pass.

---

### Task 2: Wildberries Native Taxonomy Extraction

**Files:**
- Modify: `src/pricewatch/adapters/wildberries.py`
- Modify: `tests/fixtures/wb_search_minimal.json`
- Test: `tests/test_marketplace_search_parsers.py`

**Interfaces:**
- Consumes: `MarketplaceTaxonomy`.
- Produces: WB `SearchCandidate.taxonomy` from `subjectId`, `subjectParentId`, `entity`.

- [ ] **Step 1: Add a failing parser test**

```python
def test_wb_parser_keeps_native_taxonomy() -> None:
    candidate = parse_wb_search(fixture("wb_search_minimal.json"))[0]
    assert candidate.taxonomy is not None
    assert candidate.taxonomy.subject_id == "107"
    assert candidate.taxonomy.parent_id == "9491"
    assert candidate.taxonomy.entity == "Планшеты"
```

Update fixture product with `subjectId: 107`, `subjectParentId: 9491`, `entity: "Планшеты"`.

- [ ] **Step 2: Run parser test and verify RED**

Run: `pytest tests/test_marketplace_search_parsers.py -q`
Expected: candidate has no taxonomy.

- [ ] **Step 3: Parse taxonomy into every variation candidate**

Keep malformed/missing optional taxonomy fields non-fatal. Do not invent ids.

- [ ] **Step 4: Run parser tests and verify GREEN**

Run: `pytest tests/test_marketplace_search_parsers.py -q`
Expected: parser tests pass.

---

### Task 3: Ozon Category-Scoped Search

**Files:**
- Modify: `src/pricewatch/marketplaces.py`
- Modify: `src/pricewatch/adapters/ozon.py`
- Test: `tests/test_marketplace_search_adapters.py`

**Interfaces:**
- Add optional `category_path: str | None = None` keyword to `MarketplaceSearchAdapter.search()`.
- Ozon builds scoped inner URL when category path is supplied.
- WB accepts and ignores `category_path` because WB filtering happens on returned subject ids.

- [ ] **Step 1: Add failing request-building tests**

```python
def test_ozon_search_uses_category_scope_when_supplied() -> None:
    adapter.search("xiaomi pad 7", category_path="/category/planshety-15525/")
    assert request.params["url"].startswith("/category/planshety-15525/?text=xiaomi pad 7")


def test_ozon_search_without_scope_remains_global() -> None:
    adapter.search("xiaomi pad 7")
    assert request.params["url"].startswith("/search/?text=xiaomi pad 7")
```

- [ ] **Step 2: Run adapter tests and verify RED**

Run: `pytest tests/test_marketplace_search_adapters.py -q`
Expected: unexpected `category_path` keyword / wrong URL.

- [ ] **Step 3: Extend adapter protocol and both adapters**

Normalize category path by requiring it to start with `/category/`; otherwise raise `ValueError`. Do not accept arbitrary absolute URLs.

- [ ] **Step 4: Run adapter tests and verify GREEN**

Run: `pytest tests/test_marketplace_search_adapters.py -q`
Expected: all adapter tests pass.

---

### Task 4: Integrate Taxonomy Gate Into Scan Engine

**Files:**
- Modify: `src/pricewatch/scan.py`
- Test: `tests/test_scan_engine.py`

**Interfaces:**
- `scan_once(..., taxonomy_registry: TaxonomyRegistry | None = None)`.
- `ScanOutcome` adds `taxonomy_rejected_count`.
- Scan resolves constraint once per marketplace/product type.
- Ozon receives the resolved `category_path` in adapter search.
- Taxonomy gate runs after deduplication and before `match_candidate`.

- [ ] **Step 1: Add failing scan tests**

Create fake WB candidates containing one tablet (`subject_id=107`) and one case (`subject_id=203`) where both titles contain the required lexical anchors. Assert only tablet reaches accepted/ambiguous matching and `taxonomy_rejected_count == 1`.

Add fake Ozon adapter that records `category_path` and assert tablet plan receives `/category/planshety-15525/`.

- [ ] **Step 2: Run scan tests and verify RED**

Run: `pytest tests/test_scan_engine.py -q`
Expected: no taxonomy registry integration / outcome field.

- [ ] **Step 3: Implement gate ordering and scoped request propagation**

Unknown gate results continue to normal matching. Rejected taxonomy candidates never enter `match_candidate`.

- [ ] **Step 4: Run scan tests and verify GREEN**

Run: `pytest tests/test_scan_engine.py -q`
Expected: scan tests pass.

---

### Task 5: Trusted Taxonomy Observation Accumulator

**Files:**
- Modify: `src/pricewatch/taxonomy.py`
- Test: `tests/test_taxonomy.py`

**Interfaces:**
- `TaxonomyObservationAccumulator.observe(product_type, candidate)` records distinct accepted listing ids only.
- `propose(product_type, marketplace, minimum_distinct=3) -> TaxonomyConstraint | None`.

- [ ] **Step 1: Add failing learning tests**

Assert one/two matching listings produce no proposal. Three distinct listings with the same WB subject produce a proposed constraint. Duplicate listing ids do not increase evidence. A tied competing taxonomy returns no proposal.

- [ ] **Step 2: Run taxonomy tests and verify RED**

Run: `pytest tests/test_taxonomy.py -q`
Expected: accumulator missing.

- [ ] **Step 3: Implement deterministic accumulator**

Use sets of listing ids keyed by `(product_type, marketplace, taxonomy_signature)`. Only non-empty stable signatures participate.

- [ ] **Step 4: Run taxonomy tests and verify GREEN**

Run: `pytest tests/test_taxonomy.py -q`
Expected: taxonomy tests pass.

---

### Task 6: Regression and CI Verification

**Files:**
- No functional changes unless verification exposes a real regression.

- [ ] **Step 1: Run full lint**

Run: `ruff check .`
Expected: exit 0.

- [ ] **Step 2: Run complete test suite**

Run: `pytest -q`
Expected: 0 failed.

- [ ] **Step 3: Inspect GitHub Actions for current head**

Expected: Ruff and pytest steps both conclude `success`.

- [ ] **Step 4: Report exact remaining limitations**

Call out that only evidence-backed seed mappings are active immediately, learned mappings are not persisted until PostgreSQL is implemented, and Ozon category-path discovery for arbitrary unknown product types remains a later resolver/persistence feature rather than an LLM hot-path guess.
