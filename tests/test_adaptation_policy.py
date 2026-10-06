import pytest

from pipeline.adaptation_policy import (
    policy_for,
    target_length_bounds,
)


def test_lower_levels_allow_broad_omission():
    for level in ("hsk1", "hsk2", "hsk3"):
        policy = policy_for(level)
        assert policy.audit_source_omissions is False
        assert "omit" in policy.scope.lower()
    assert policy_for("hsk1").scope != policy_for("hsk3").scope


def test_higher_levels_keep_source_omission_audit():
    for level in ("hsk4", "hsk5", "hsk6"):
        assert policy_for(level).audit_source_omissions is True


def test_word_budgets_are_strict_for_beginners_and_allow_literary_layer_later():
    assert [
        policy_for(f"hsk{level}").max_above_level_ratio
        for level in range(1, 7)
    ] == [0.15, 0.18, 0.20, 0.25, 0.25, 0.30]


def test_unknown_level_fails_closed():
    with pytest.raises(ValueError, match="unsupported adaptation level"):
        policy_for("hsk0")


def test_default_adaptation_policy_contains_no_size_or_band_count_quotas():
    policy = policy_for("hsk4")
    assert not hasattr(policy, "min_target_chars")
    assert not hasattr(policy, "max_target_chars")
    assert not hasattr(policy, "min_target_band_unique")


def test_length_bounds_are_tighter_after_hsk1():
    assert target_length_bounds(1000, "hsk1") == (700, 1170)
    assert target_length_bounds(1000, "hsk4") == (850, 1150)
