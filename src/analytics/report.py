from src.analytics.coverage import CoverageStats
from src.config import LEVEL_LABELS


def print_coverage_report(stats: CoverageStats) -> str:
    """Format a coverage report as a string."""
    lines = [
        f"=== Coverage Report for HSK {stats.target_level} ===",
        f"Total tokens:       {stats.total_tokens}",
        f"Unique tokens:      {stats.unique_tokens}",
        f"In-level tokens:    {stats.in_level_tokens}",
        f"Above-level tokens: {stats.above_level_tokens}",
        f"Unknown tokens:     {stats.unknown_tokens}",
        f"Coverage:           {stats.coverage_percent:.1f}%",
        f"Above-level:        {stats.above_level_percent:.1f}%",
        f"Passes 95/5 rule:   {'YES' if stats.passes else 'NO'}",
        "",
        "Level distribution:",
    ]

    for level, count in sorted(stats.level_distribution.items(),
                                key=lambda x: (x[0] is None, x[0] or 0)):
        label = LEVEL_LABELS.get(level, "Unknown") if level is not None else "Unknown"
        pct = count / stats.total_tokens * 100 if stats.total_tokens > 0 else 0
        lines.append(f"  {label:12s}: {count:4d} ({pct:5.1f}%)")

    if stats.above_level_words:
        lines.append("")
        lines.append("Above-level vocabulary:")
        for word, level in stats.above_level_words:
            label = f"HSK {level}" if level is not None else "Non-HSK"
            lines.append(f"  {word} [{label}]")

    return "\n".join(lines)
