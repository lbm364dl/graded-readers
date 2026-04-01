from src.analytics.coverage import CoverageStats
from src.config import LEVEL_LABELS


def print_coverage_report(stats: CoverageStats) -> str:
    label = LEVEL_LABELS.get(stats.level, str(stats.level))
    status = "PASS" if stats.passes else "FAIL"
    lines = [
        f"[{status}] {label} coverage report",
        f"  Total tokens : {stats.total_tokens}",
        f"  In-level     : {stats.in_level_tokens}  ({stats.coverage_percent:.1f}%)",
        f"  Above-level  : {stats.above_level_tokens}  ({stats.above_level_percent:.1f}%)",
    ]
    if stats.top_unknown:
        lines.append(f"\n  Top above-level words ({len(stats.top_unknown)}):")
        for word, freq in stats.top_unknown[:20]:
            lines.append(f"    {word}  {freq}x")
    return "\n".join(lines)
