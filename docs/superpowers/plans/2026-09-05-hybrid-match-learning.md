# Hybrid Match Learning Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a precision-first hybrid matcher that combines taxonomy, deterministic vetoes, lightweight probabilistic scoring, verified-only online learning, active-learning queues, hard-negative mining, and query/alias performance tracking.

**Architecture:** Keep `taxonomy.py` and `matching.py` authoritative for explicit contradictions. Add `match_learning.py` as a dependency-free learning layer; integrate it into `scan.py` and `verification.py` without allowing learned probability to override hard vetoes. Search evidence may populate queues/metrics but only detail/manual/LLM-reviewed labels may train.

**Tech Stack:** Python 3.12, dataclasses, standard-library math/collections, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-09-05-hybrid-match-learning-design.md`

## Global Constraints

- Primary scan remains every four minutes.
- No LLM call in the hot scan loop.
- Search price is preview-only; alert prices require detail verification.
- Explicit identity contradictions always reject regardless of learned score.
- Search-only observations never update model weights.
- Detail deterministic identity, not scorer probability, supplies automatic verified labels.
- No heavy ML dependency in v1.

---

### Task 1: Core feature scorer and hard-veto model

**Files:**
- Create: `src/pricewatch/match_learning.py`
- Create: `tests/test_match_learning.py`

**Interfaces:**
- Produces: `MatchFeatureVector`, `HybridMatchDecision`, `OnlineMatchModel`, `HybridMatchEngine`.

- [x] Write failing tests for identifier/variant hard vetoes, high-confidence exact evidence, and ambiguous queueing.
- [x] Run CI and confirm failure is caused by missing learning module/behavior.
- [x] Implement deterministic feature extraction and hard vetoes.
- [x] Implement a conservative pure-Python online logistic scorer.
- [x] Run the full suite.

### Task 2: Verified-only learning, evidence, and active-learning queue

**Files:**
- Modify: `src/pricewatch/match_learning.py`
- Modify: `tests/test_match_learning.py`

**Interfaces:**
- Produces: `LearningEvidence`, `LearningEvidenceSource`, `UncertainMatchQueue` and verified-only `learn()` methods.

- [x] Write failing tests proving search evidence cannot change weights.
- [x] Write failing tests proving verified positive/negative labels update weights and preserve provenance.
- [x] Implement the minimal evidence store and uncertainty queue.
- [x] Remove resolved candidates from the uncertainty queue after verified labeling.
- [x] Run the full suite.

### Task 3: Hard-negative mining and query performance

**Files:**
- Modify: `src/pricewatch/match_learning.py`
- Modify: `tests/test_match_learning.py`

**Interfaces:**
- Produces: hard-negative buckets and `QueryPerformanceTracker`.

- [x] Write failing tests for sibling/variant/accessory negative buckets.
- [x] Write failing tests that supplemental query ranking prefers verified unique yield and penalizes verified rejects.
- [x] Implement mining and query metrics without automatically training on mined negatives.
- [x] Deduplicate repeated hard negatives from recurring scans.
- [x] Add cold-start exploration, learned exploitation, and periodic alias re-exploration.
- [x] Run the full suite.

### Task 4: Scan integration

**Files:**
- Modify: `src/pricewatch/scan.py`
- Modify: `tests/test_scan_engine.py`

**Interfaces:**
- `scan_once(..., match_engine: HybridMatchEngine | None = None)`.

- [x] Write failing tests that taxonomy still runs first and hybrid ambiguity is queued.
- [x] Integrate the hybrid engine after taxonomy and before final accepted/ambiguous output.
- [x] Attribute query discovery results to the query-performance tracker.
- [x] Use verified query performance to select the supplemental alias without changing primary cadence.
- [x] Run the full suite.

### Task 5: Detail verification integration

**Files:**
- Modify: `src/pricewatch/verification.py`
- Modify: `tests/test_offer_verification.py`
- Modify: `tests/test_ozon_offer_verification.py` if needed.

**Interfaces:**
- `verify_candidate(..., match_engine: HybridMatchEngine | None = None)`.

- [x] Write failing tests that successful detail verification trains a positive and failed identity recheck records a verified negative.
- [x] Implement verified evidence recording without trusting search labels.
- [x] Add regression coverage proving an uncalibrated scorer cannot create its own negative detail label.
- [x] Preserve explicit brand/GTIN/EAN/UPC/MPN hints in SearchPlan while forbidding invented identifiers.
- [x] Run Ruff and full pytest suite.

### Task 6: Final verification

- [x] Confirm branch is ahead of `main` only with intended feature changes.
- [x] Confirm GitHub Actions is green for Ruff and pytest after adaptive scan integration.
- [x] Keep the feature branch/PR open unless the user explicitly chooses integration.

## Remaining production step

The learning engine is currently process-local. PostgreSQL persistence for verified evidence, model weights, query statistics, taxonomy observations, and unresolved active-learning items is intentionally deferred to the storage/runtime layer. Without that persistence, learned state resets when the worker process restarts.
