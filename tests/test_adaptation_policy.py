import pytest

from pipeline.adaptation_policy import policy_for


def test_lower_levels_allow_broad_omission():
    for level in ("hsk1", "hsk2", "hsk3"):
        policy = policy_for(level)
        assert policy.audit_source_omissions is False
        assert "omit" in policy.scope.lower()
    assert policy_for("hsk1").max_above_level_ratio > policy_for("hsk3").max_above_level_ratio


def test_higher_levels_keep_source_omission_audit():
    for level in ("hsk4", "hsk5", "hsk6"):
        assert policy_for(level).audit_source_omissions is True


def test_unknown_level_fails_closed():
    with pytest.raises(ValueError, match="unsupported adaptation level"):
        policy_for("hsk0")
