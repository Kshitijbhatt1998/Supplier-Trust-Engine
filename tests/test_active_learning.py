"""
Tests for the Active Learning / Adaptive Threshold layer.

Verifies that:
  1. A clean supplier (no rejections) gets exactly BASE_THRESHOLD.
  2. A supplier with only rejections gets a raised threshold.
  3. A supplier whose verified aliases outnumber rejections gets a lower
     threshold than one with only rejections (clean-slate bonus).
  4. The threshold is hard-capped at MAX_THRESHOLD regardless of rejection count.
  5. Laplace smoothing prevents a *single* rejection from producing the same
     penalty as ten rejections (low-sample stabilisation).
  6. End-to-end: a match that would auto-register against a clean supplier is
     held as low_confidence=True when that supplier has accumulated rejections.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.storage.db import init_db
from api.resolver import EntityResolver


# ------------------------------------------------------------------ #
# Helpers                                                               #
# ------------------------------------------------------------------ #

def _make_db():
    """Fresh in-memory DuckDB with all tables and the resolver_config view."""
    return init_db(":memory:")


def _seed_supplier(con, sup_id="sup-1", name="Welspun India Ltd", country="India"):
    con.execute(
        "INSERT INTO suppliers (id, name, country) VALUES (?, ?, ?)",
        [sup_id, name, country]
    )
    return sup_id


def _add_rejections(con, canonical_id, count, alias_prefix="noise"):
    for i in range(count):
        con.execute("""
            INSERT INTO entity_rejections (alias_normalized, canonical_id, reason_code)
            VALUES (?, ?, 'admin_rejected')
            ON CONFLICT DO NOTHING
        """, [f"{alias_prefix}-{i}", canonical_id])


def _add_verifications(con, canonical_id, count, alias_prefix="good"):
    for i in range(count):
        alias_id = f"aid-{alias_prefix}-{i}"
        con.execute("""
            INSERT INTO entity_aliases
                (id, alias_name, alias_normalized, canonical_id, match_score, is_verified)
            VALUES (?, ?, ?, ?, 95.0, TRUE)
        """, [alias_id, f"{alias_prefix} {i}", f"{alias_prefix}-{i}", canonical_id])


# ------------------------------------------------------------------ #
# Unit tests — _get_adaptive_threshold()                               #
# ------------------------------------------------------------------ #

def test_clean_supplier_gets_neutral_threshold():
    """
    No rejection history, no verifications → Laplace neutral rate = 0.5.
    threshold = BASE + 0.5 × PENALTY_WEIGHT (= 91.0 with current defaults).

    BASE_THRESHOLD is an asymptotic floor approached only as verified aliases
    accumulate.  A brand-new supplier starts at the neutral (cautious) level.
    """
    con = _make_db()
    sup_id = _seed_supplier(con)
    resolver = EntityResolver(con)

    expected = resolver.BASE_THRESHOLD + 0.5 * resolver.PENALTY_WEIGHT
    threshold = resolver._get_adaptive_threshold(sup_id)
    assert threshold == expected, (
        f"Expected neutral threshold {expected}, got {threshold}"
    )


def test_rejections_raise_threshold():
    """10 rejections, 0 verifications → threshold above BASE."""
    con = _make_db()
    sup_id = _seed_supplier(con)
    _add_rejections(con, sup_id, count=10)
    resolver = EntityResolver(con)

    threshold = resolver._get_adaptive_threshold(sup_id)
    assert threshold > resolver.BASE_THRESHOLD, (
        f"Threshold should exceed BASE after rejections, got {threshold}"
    )


def test_verifications_lower_penalty_vs_pure_rejections():
    """
    Same rejection count but with equal verifications should produce
    a lower threshold than pure rejections (clean-slate bonus).
    """
    con = _make_db()
    noisy_id = _seed_supplier(con, "noisy", "Noisy Supplier Ltd")
    mixed_id = _seed_supplier(con, "mixed", "Mixed Supplier Ltd")

    _add_rejections(con, noisy_id, count=10)

    _add_rejections(con, mixed_id, count=10)
    _add_verifications(con, mixed_id, count=10)

    resolver = EntityResolver(con)
    t_noisy = resolver._get_adaptive_threshold(noisy_id)
    t_mixed = resolver._get_adaptive_threshold(mixed_id)

    assert t_mixed < t_noisy, (
        f"Mixed ({t_mixed:.2f}) should be below noisy ({t_noisy:.2f})"
    )


def test_threshold_capped_at_max():
    """Even with 1000 rejections, threshold must not exceed MAX_THRESHOLD."""
    con = _make_db()
    sup_id = _seed_supplier(con)
    _add_rejections(con, sup_id, count=1000)
    resolver = EntityResolver(con)

    threshold = resolver._get_adaptive_threshold(sup_id)
    assert threshold <= resolver.MAX_THRESHOLD, (
        f"Threshold {threshold} exceeds hard ceiling {resolver.MAX_THRESHOLD}"
    )


def test_laplace_single_rejection_vs_many():
    """
    1 rejection should produce a meaningfully lower threshold than 20 rejections,
    proving that the smoothing prevents single-click over-reaction.
    """
    con1 = _make_db()
    s1 = _seed_supplier(con1, "s1", "Supplier One")
    _add_rejections(con1, s1, count=1)
    r1 = EntityResolver(con1)
    t1 = r1._get_adaptive_threshold(s1)

    con2 = _make_db()
    s2 = _seed_supplier(con2, "s2", "Supplier Two")
    _add_rejections(con2, s2, count=20)
    r2 = EntityResolver(con2)
    t2 = r2._get_adaptive_threshold(s2)

    assert t1 < t2, (
        f"1 rejection (t={t1:.2f}) should yield lower threshold than "
        f"20 rejections (t={t2:.2f})"
    )


def test_unknown_canonical_id_returns_base():
    """Querying a canonical_id not in suppliers → falls back to BASE_THRESHOLD."""
    con = _make_db()
    resolver = EntityResolver(con)

    threshold = resolver._get_adaptive_threshold("does-not-exist")
    assert threshold == resolver.BASE_THRESHOLD


# ------------------------------------------------------------------ #
# End-to-end: resolve() honours adaptive threshold                     #
# ------------------------------------------------------------------ #

def test_resolve_sets_low_confidence_for_penalised_supplier():
    """
    A name that would auto-register against a clean supplier should be
    flagged low_confidence=True once the supplier has many rejections
    pushing its threshold above the fuzzy score.
    """
    con = _make_db()
    sup_id = _seed_supplier(con, "welspun-1", "Welspun India Ltd", "India")

    # Resolve cleanly first — should be low_confidence=False at score ≈ 100
    resolver = EntityResolver(con)
    result_clean = resolver.resolve("Welspun India Ltd", country="India")
    assert result_clean.get("supplier_id") == sup_id
    assert result_clean.get("low_confidence") is not True

    # Now flood with rejections to push the adaptive threshold above ~88
    _add_rejections(con, sup_id, count=50)

    # "Welsun" fuzzy-matches at ~85-88; with 50 rejections threshold is >> 90
    resolver2 = EntityResolver(con)
    result_noisy = resolver2.resolve("Welsun India", country="India")

    # Must still find the candidate (score >= MIN_THRESHOLD=75) …
    assert result_noisy.get("supplier_id") == sup_id, (
        "Candidate should still be returned (above MIN_THRESHOLD)"
    )
    # … but flag it as needing human review
    assert result_noisy.get("low_confidence") is True, (
        "Should be flagged low_confidence after supplier accumulates rejections"
    )


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
