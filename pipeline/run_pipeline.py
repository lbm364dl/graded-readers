#!/usr/bin/env python3
"""Graded Reader Generation Pipeline — orchestrator.

This script ties together the full pipeline:
  1. Extract character sets from word lists
  2. Generate an AI prompt for a given source text + level
  3. (Manual step) Feed prompt to AI, paste output back
  4. Validate the AI output against character constraints
  5. If validation fails, generate a retry prompt with feedback
  6. Repeat until passing or max iterations reached

Usage:
  # Step 1: Extract charsets (do this once)
  python -m pipeline.extract_characters

  # Step 2: Run the pipeline for a specific text + level
  python -m pipeline.run_pipeline \\
      --source books/chinese/kong_yiji_original.txt \\
      --level hsk4 \\
      --language chinese \\
      --title "孔乙己"

  # This will:
  #   - Generate a prompt file in pipeline/prompts/
  #   - Wait for you to paste the AI output into pipeline/outputs/
  #   - Validate the output
  #   - If needed, generate a retry prompt
"""

import json
from pathlib import Path

from pipeline.extract_characters import build_hsk_charsets, build_jlpt_charsets, save_charsets
from pipeline.validate_text import validate_characters, strip_markdown_headers
from pipeline.generate_prompt import build_prompt, load_charset as load_charset_str


def ensure_charsets(language: str) -> Path:
    """Ensure character sets exist, build them if not."""
    pipeline_dir = Path(__file__).resolve().parent
    project_root = pipeline_dir.parent

    if language == "chinese":
        out_dir = pipeline_dir / "charsets" / "chinese"
        if not (out_dir / "charset_summary.json").exists():
            print("Building HSK character sets...")
            charsets = build_hsk_charsets(project_root / "data" / "chinese")
            save_charsets(charsets, out_dir)
    else:
        out_dir = pipeline_dir / "charsets" / "japanese"
        if not (out_dir / "charset_summary.json").exists():
            print("Building JLPT character sets...")
            charsets = build_jlpt_charsets(project_root / "data" / "japanese")
            save_charsets(charsets, out_dir)

    return out_dir


def run_pipeline(
    source_path: str,
    level_key: str,
    language: str,
    title: str = "",
    max_iterations: int = 3,
    glossary_chars: str = "",
    threshold: float = 0.05,
):
    """Run the full generation pipeline."""
    pipeline_dir = Path(__file__).resolve().parent

    # Parse level number
    if level_key.startswith("hsk"):
        level_num = int(level_key.replace("hsk", "").replace("7to9", "7"))
    elif level_key.startswith("n"):
        level_num = int(level_key.replace("n", ""))
    else:
        raise ValueError(f"Unknown level key: {level_key}")

    # Step 1: Ensure charsets exist
    charset_dir = ensure_charsets(language)
    charset_path = charset_dir / f"{level_key}_chars.txt"
    charset = load_charset_str(charset_path)
    allowed_chars = set(charset)

    # Set up output directories
    prompts_dir = pipeline_dir / "prompts"
    outputs_dir = pipeline_dir / "outputs"
    reports_dir = pipeline_dir / "reports"
    prompts_dir.mkdir(exist_ok=True)
    outputs_dir.mkdir(exist_ok=True)
    reports_dir.mkdir(exist_ok=True)

    # Load source text
    source_text = Path(source_path).read_text(encoding="utf-8")
    base_name = f"{level_key}_{Path(source_path).stem}"

    extra_chars = set(glossary_chars) if glossary_chars else None

    for iteration in range(1, max_iterations + 1):
        print(f"\n{'='*60}")
        print(f"Iteration {iteration}/{max_iterations}")
        print(f"{'='*60}")

        # Check if output already exists for this iteration
        output_path = outputs_dir / f"{base_name}_v{iteration}.txt"
        prompt_path = prompts_dir / f"{base_name}_v{iteration}_prompt.md"

        # Load previous attempt if this is a retry
        previous_attempt = ""
        previous_validation = None
        if iteration > 1:
            prev_output = outputs_dir / f"{base_name}_v{iteration-1}.txt"
            prev_report = reports_dir / f"{base_name}_v{iteration-1}_report.json"
            if prev_output.exists():
                previous_attempt = prev_output.read_text(encoding="utf-8")
            if prev_report.exists():
                with open(prev_report, encoding="utf-8") as f:
                    previous_validation = json.load(f)

        # Generate prompt
        prompt = build_prompt(
            source_text=source_text,
            charset=charset,
            level_key=level_key,
            level_num=level_num,
            language=language,
            title=title,
            iteration=iteration,
            previous_attempt=previous_attempt,
            previous_validation=previous_validation,
            glossary_chars=glossary_chars,
        )
        prompt_path.write_text(prompt, encoding="utf-8")
        print(f"\nPrompt saved to: {prompt_path}")

        # Check if output file exists (user may have already generated it)
        if output_path.exists():
            print(f"Output file found: {output_path}")
        else:
            print(f"\n>>> ACTION REQUIRED <<<")
            print(f"1. Feed the prompt from {prompt_path} to an AI (Claude, etc.)")
            print(f"2. Save the AI's output to: {output_path}")
            print(f"3. Re-run this script to continue validation.")
            print(f"\nOr paste the output now (Ctrl+D when done):")

            try:
                lines = []
                while True:
                    line = input()
                    lines.append(line)
            except EOFError:
                pass

            if lines:
                output_text = "\n".join(lines)
                output_path.write_text(output_text, encoding="utf-8")
                print(f"\nOutput saved to: {output_path}")
            else:
                print("No output provided. Stopping pipeline.")
                return

        # Validate
        output_text = output_path.read_text(encoding="utf-8")
        if output_path.suffix == ".md":
            output_text = strip_markdown_headers(output_text)

        result = validate_characters(
            output_text, allowed_chars, extra_chars, threshold,
        )

        # Save report
        report_path = reports_dir / f"{base_name}_v{iteration}_report.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        # Print result
        status = "PASS ✓" if result["passes"] else "FAIL ✗"
        print(f"\n[{status}] Character validation")
        print(f"  In-level     : {result['in_level_percent']}%")
        print(f"  Above-level  : {result['above_level_percent']}% (max: {threshold*100:.0f}%)")
        print(f"  Total chars  : {result['total_chars']}")

        if result["above_level_list"]:
            above = "".join(result["above_level_list"][:80])
            print(f"  Above chars  : {above}")

        print(f"  Report saved : {report_path}")

        if result["passes"]:
            print(f"\n✓ Text passes at iteration {iteration}!")
            print(f"  Final output: {output_path}")
            return result

        if iteration < max_iterations:
            print(f"\n  → Will generate retry prompt for iteration {iteration + 1}")
        else:
            print(f"\n✗ Max iterations reached. Best result: {result['in_level_percent']}% in-level.")

    return result


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Run the graded reader generation pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Extract character sets first (one-time setup)
  python -m pipeline.extract_characters

  # Generate a graded reader
  python -m pipeline.run_pipeline \\
      --source books/chinese/kong_yiji_original.txt \\
      --level hsk4 --language chinese --title "孔乙己"

  # Validate an existing reader
  python -m pipeline.validate_text readers/hsk4_02_kong_yiji.md -l hsk4
        """,
    )
    parser.add_argument("--source", required=True, help="Source text file")
    parser.add_argument("-l", "--level", required=True, help="Level key (hsk3, n4, etc.)")
    parser.add_argument("--language", choices=["chinese", "japanese"], required=True)
    parser.add_argument("--title", default="", help="Title for the reader")
    parser.add_argument("--max-iterations", type=int, default=3)
    parser.add_argument("--glossary-chars", default="", help="Extra allowed characters")
    parser.add_argument("--threshold", type=float, default=0.05)

    args = parser.parse_args()

    run_pipeline(
        source_path=args.source,
        level_key=args.level,
        language=args.language,
        title=args.title,
        max_iterations=args.max_iterations,
        glossary_chars=args.glossary_chars,
        threshold=args.threshold,
    )


if __name__ == "__main__":
    main()
