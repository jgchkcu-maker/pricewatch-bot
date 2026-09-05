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
- No heavy ML dependency in v1.

---

### Task 1: Core feature scorer and hard-veto model

**Files:**
- Create: `src/pricewatch/match_learning.py`
- Create: `tests/test_match_learning.py`

**Interfaces:**
- Produces: `MatchFeatureVector`, `HybridMatchDecision`, `OnlineMatchModel`, `HybridMatchEngine`.

- [ ] Write failing tests for identifier/variant hard vetoes, high-confidence exact evidence, and ambiguous queueing.
- [ ] Run CI and confirm failure is caused by missing learning module/behavior.
- [ ] Implement deterministic feature extraction and hard vetoes.
- [ ] Implement a conservative pure-Python online logistic scorer.
- [ ] Run the full suite.

### Task 2: Verified-only learning, evidence, and active-learning queue

**Files:**
- Modify: `src/pricewatch/match_learning.py`
- Modify: `tests/test_match_learning.py`

**Interfaces:**
- Produces: `LearningEvidence`, `LearningEvidenceSource`, `UncertainMatchQueue` and verified-only `learn()` methods.

- [ ] Write failing tests proving search evidence cannot change weights.
- [ ] Write failing tests proving verified positive/negative labels update weights and preserve provenance.
- [ ] Implement the minimal evidence store and uncertainty queue.
- [ ] Run the full suite.

### Task 3: Hard-negative mining and query performance

**Files:**
- Modify: `src/pricewatch/match_learning.py`
- Modify: `tests/test_match_learning.py`

**Interfaces:**
- Produces: `HardNegativeMiner`, `QueryPerformanceTracker`.

- [ ] Write failing tests for sibling/variant/accessory negative buckets.
- [ ] Write failing tests that supplemental query ranking prefers verified unique yield and penalizes verified rejects.
- [ ] Implement mining and query metrics without automatically training on mined negatives.
- [ ] Run the full suite.

### Task 4: Scan integration

**Files:**
- Modify: `src/pricewatch/scan.py`
- Modify: `tests/test_scan_engine.py`

**Interfaces:**
- `scan_once(..., match_engine: HybridMatchEngine | None = None)`.

- [ ] Write failing tests that taxonomy still runs first and hybrid ambiguity is queued.
- [ ] Integrate the hybrid engine after taxonomy and before final accepted/ambiguous output.
- [ ] Attribute query discovery results to the query-performance tracker.
- [ ] Run the full suite.

### Task 5: Detail verification integration

**Files:**
- Modify: `src/pricewatch/verification.py`
- Modify: `tests/test_offer_verification.py`
- Modify: `tests/test_ozon_offer_verification.py` if needed.

**Interfaces:**
- `verify_candidate(..., match_engine: HybridMatchEngine | None = None)`.

- [ ] Write failing tests that successful detail verification trains a positive and failed identity recheck records a verified negative.
- [ ] Implement verified evidence recording without trusting search labels.
- [ ] Run Ruff and full pytest suite.

### Task 6: Final verification

- [ ] Confirm branch is ahead of `main` only with intended feature changes.
- [ ] Confirm latest GitHub Actions run is green for Ruff and pytest.
- [ ] Keep the feature branch/PR open unless the user explicitly chooses integration.
