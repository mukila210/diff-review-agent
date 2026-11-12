"""
LangGraph workflow for reviewing a code change.

   parse -> static_tools -> llm_review <-(retry on invalid output)-+
                                |  ^______________________________|
                                v
                              merge -> END

* parse         deterministic: unified diff -> files/lines with new-file line numbers
* static_tools  deterministic tools: risky-pattern scan + missing-test check
* llm_review    LLM call; reply must be valid JSON matching LLMReview, else retry with the
                validation error fed back (up to MAX_ATTEMPTS)
* merge         grounding check (drop findings that cite files/lines not in the diff),
                de-duplicate against static findings, sort, build summary

If the LLM never produces valid output the agent degrades to static findings only and says so.
"""
import json
from typing import List, Optional, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import ValidationError

from review_agent.diffparse import FileChange, annotate, parse_diff
from review_agent.llm import LLMClient
from review_agent.prompts import SYSTEM_PROMPT, build_user_prompt
from review_agent.schemas import Finding, LLMReview, ReviewResult
from review_agent.tools import check_missing_tests, scan_risky_patterns

MAX_ATTEMPTS = 3
MAX_PROMPT_CHARS = 24000
SEV_ORDER = {"high": 0, "medium": 1, "low": 2}


class ReviewState(TypedDict, total=False):
    diff_text: str
    files: List[FileChange]
    annotated: str
    truncated: bool
    static_findings: List[Finding]
    llm_review: Optional[LLMReview]
    attempts: int
    last_error: Optional[str]
    result: ReviewResult


def extract_json(text: str) -> dict:
    """Models often wrap JSON in prose or code fences; pull out the outermost object."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in reply")
    return json.loads(text[start:end + 1])


def _static_summary(findings: List[Finding]) -> str:
    return "\n".join(
        f"- [{f.severity}][{f.category}] {f.file}:{f.line if f.line else '-'} {f.description}"
        for f in findings
    )


def build_graph(llm: Optional[LLMClient]):
    def parse_node(state: ReviewState) -> ReviewState:
        files = parse_diff(state["diff_text"])
        annotated = annotate(files)
        truncated = len(annotated) > MAX_PROMPT_CHARS
        if truncated:
            annotated = annotated[:MAX_PROMPT_CHARS] + "\n[... diff truncated ...]"
        return {"files": files, "annotated": annotated, "truncated": truncated,
                "attempts": 0, "last_error": None, "llm_review": None}

    def static_node(state: ReviewState) -> ReviewState:
        files = state["files"]
        return {"static_findings": scan_risky_patterns(files) + check_missing_tests(files)}

    def llm_node(state: ReviewState) -> ReviewState:
        attempts = state.get("attempts", 0) + 1
        user = build_user_prompt(state["annotated"], _static_summary(state["static_findings"]),
                                 state.get("last_error"))
        try:
            raw = llm.complete(SYSTEM_PROMPT, user)
            review = LLMReview.model_validate(extract_json(raw))
            return {"llm_review": review, "last_error": None, "attempts": attempts}
        except (ValueError, ValidationError) as e:      # JSONDecodeError is a ValueError
            err = f"{type(e).__name__}: {str(e)[:300]}"
        except Exception as e:                           # API/network failure
            err = f"LLM call failed ({type(e).__name__}): {str(e)[:200]}"
        return {"llm_review": None, "last_error": err, "attempts": attempts}

    def merge_node(state: ReviewState) -> ReviewState:
        files = {f.path: f for f in state.get("files", [])}
        static = state.get("static_findings", [])
        review = state.get("llm_review")
        attempts = state.get("attempts", 0)

        dropped = 0
        llm_findings: List[Finding] = []
        seen = {(f.file, f.line, f.category) for f in static}
        if review:
            for lf in review.findings:
                fc = files.get(lf.file)
                if fc is None:                      # cites a file that is not in the diff
                    dropped += 1
                    continue
                data = lf.model_dump()
                if lf.line is not None and lf.line not in fc.added_linenos:
                    data["line"] = None             # line can't be verified against the diff
                if (data["file"], data["line"], data["category"]) in seen:
                    continue                        # duplicate of a static finding
                llm_findings.append(Finding(**data, source="llm"))

        findings = sorted(static + llm_findings,
                          key=lambda f: (SEV_ORDER[f.severity], f.file, f.line or 0))
        degraded = bool(llm and files and review is None)

        counts = {s: sum(f.severity == s for f in findings) for s in SEV_ORDER}
        tally = f"{counts['high']} high, {counts['medium']} medium, {counts['low']} low findings."
        if not files:
            summary = "No reviewable changes found in the diff."
        elif degraded:
            summary = (f"LLM review unavailable after {attempts} attempts "
                       f"({state.get('last_error')}); showing automated checks only. {tally}")
        elif review:
            summary = f"{review.summary} {tally}"
        else:
            summary = f"Automated checks only (LLM disabled). {tally}"

        return {"result": ReviewResult(
            summary=summary, findings=findings, degraded=degraded, llm_attempts=attempts,
            dropped_ungrounded=dropped, truncated=state.get("truncated", False))}

    def after_parse(state: ReviewState) -> str:
        return "static" if state.get("files") else "merge"

    def after_static(state: ReviewState) -> str:
        return "llm" if llm else "merge"

    def after_llm(state: ReviewState) -> str:
        if state.get("llm_review") is not None or state.get("attempts", 0) >= MAX_ATTEMPTS:
            return "merge"
        return "llm"

    g = StateGraph(ReviewState)
    g.add_node("parse", parse_node)
    g.add_node("static", static_node)
    g.add_node("llm", llm_node)
    g.add_node("merge", merge_node)
    g.set_entry_point("parse")
    g.add_conditional_edges("parse", after_parse, {"static": "static", "merge": "merge"})
    g.add_conditional_edges("static", after_static, {"llm": "llm", "merge": "merge"})
    g.add_conditional_edges("llm", after_llm, {"llm": "llm", "merge": "merge"})
    g.add_edge("merge", END)
    return g.compile()


def review_diff(diff_text: str, llm: Optional[LLMClient] = None) -> ReviewResult:
    return build_graph(llm).invoke({"diff_text": diff_text})["result"]
