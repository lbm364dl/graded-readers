"""Shared evidence limits for learner-facing Chinese glosses and explanations."""

CHINESE_TRANSLATION_POLICY = """TRANSLATION EVIDENCE POLICY:
Do not make an English gloss or explanation more specific than the Chinese and
available context support. Preserve unspecified details instead of silently
choosing a subtype, material, motive, identity or historical detail. A plausible
story-world inference or conventional literary translation is not textual evidence.
For example 酒 is alcohol/an alcoholic drink, not necessarily wine, grape wine,
rice wine or spirits; narrow it only when the evidence actually identifies the
beverage. Apply this principle generally, not just to drinks. A contextual gloss
may be narrower than a shared definition only when that occurrence supports it.
Treat inherited glosses as fallible: do not propagate unsupported specificity.
Separate explicit meaning from any useful inference, marking the latter clearly.
Reviewers must check this as semantic accuracy, not dismiss it as mere style."""
