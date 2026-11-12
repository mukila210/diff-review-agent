import argparse
import sys

from review_agent.agent import review_diff
from review_agent.llm import GroqLLM
from review_agent.schemas import ReviewResult


def format_result(r: ReviewResult) -> str:
    lines = ["", "SUMMARY", "  " + r.summary, ""]
    if r.degraded:
        lines += ["  (!) LLM output unusable: results below are automated checks only", ""]
    if r.truncated:
        lines += ["  (!) Diff was truncated before being sent to the model", ""]
    for f in r.findings:
        loc = f"{f.file}:{f.line}" if f.line else f.file
        lines.append(f"[{f.severity.upper():6}] [{f.category}] {loc}  ({f.source})")
        lines.append(f"         {f.description}")
        if f.suggestion:
            lines.append(f"         -> {f.suggestion}")
    if r.dropped_ungrounded:
        lines += ["", f"  ({r.dropped_ungrounded} model finding(s) dropped: cited code not in the diff)"]
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="review_agent", description="AI review of a git diff / PR")
    p.add_argument("diff", help="path to a .diff/.patch file, or - to read stdin")
    p.add_argument("--no-llm", action="store_true", help="run only the deterministic checks")
    p.add_argument("--model", help="Groq model id (default: GROQ_MODEL or llama-3.3-70b-versatile)")
    p.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = p.parse_args(argv)

    text = sys.stdin.read() if args.diff == "-" else open(args.diff, encoding="utf-8").read()
    llm = None if args.no_llm else GroqLLM(model=args.model)
    result = review_diff(text, llm)
    print(result.model_dump_json(indent=2) if args.json else format_result(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
