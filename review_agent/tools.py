"""
Deterministic "tools" the review workflow calls before the LLM sees the diff.

Why deterministic: they are cheap, never hallucinate, and give the LLM verified facts
to reason over (and let the agent still return something useful if the LLM fails).
"""
import re
from typing import List

from review_agent.diffparse import FileChange
from review_agent.schemas import Finding

PY = (".py",)
JS = (".js", ".jsx", ".ts", ".tsx")

# (category, severity, regex, message, suggestion, applies_to_extensions or None for any code file)
PATTERNS = [
    ("risk", "high",
     r"""(?i)\b(api[_-]?key|secret|passwd|password|token)\b\s*[:=]\s*['"][^'"]{8,}['"]""",
     "Possible hardcoded secret committed to source.",
     "Load it from an environment variable or secret manager and rotate the exposed value.", None),
    ("risk", "high",
     r"""(?i)\.execute\(\s*(?:f["']|"(?:[^"\\]|\\.)*"\s*[+%]|'(?:[^'\\]|\\.)*'\s*[+%])""",
     "SQL built with string concatenation/formatting (SQL injection risk).",
     "Use parameterized queries, e.g. cur.execute('... WHERE name = ?', (name,)).", PY),
    ("risk", "high", r"verify\s*=\s*False",
     "TLS certificate verification is disabled.",
     "Remove verify=False; trust the proper CA bundle instead.", PY),
    ("risk", "high", r"subprocess\.\w+\(.*shell\s*=\s*True",
     "subprocess call with shell=True can allow command injection.",
     "Pass an argument list and leave shell=False.", PY),
    ("risk", "high", r"\b(eval|exec)\(",
     "eval/exec on dynamic input.",
     "Use ast.literal_eval or a safe parser instead.", PY),
    ("bug", "medium", r"^\s*except\s*:\s*$",
     "Bare except swallows every error, including KeyboardInterrupt.",
     "Catch specific exceptions and log or re-raise.", PY),
    ("bug", "medium", r"def\s+\w+\(.*=\s*(\[\]|\{\}|set\(\))\s*[,)]",
     "Mutable default argument is shared between calls.",
     "Default to None and create the object inside the function.", PY),
    ("style", "low", r"[=!]=\s*None\b",
     "Comparison to None with ==/!=.",
     "Use 'is None' / 'is not None'.", PY),
    ("style", "low", r"^\s*print\(",
     "Debug print left in code.",
     "Use the logging module or remove it.", PY),
    ("style", "low", r"console\.log\(",
     "console.log left in code.",
     "Remove it or use a proper logger.", JS),
    ("style", "low", r"(?i)\b(TODO|FIXME|HACK)\b",
     "Unresolved TODO/FIXME marker.",
     "Resolve it or link it to a tracked issue.", None),
]
_COMPILED = [(c, s, re.compile(p), m, sug, ext) for c, s, p, m, sug, ext in PATTERNS]


def scan_risky_patterns(files: List[FileChange]) -> List[Finding]:
    """Regex scan over ADDED lines only (we never flag code the author did not touch)."""
    findings: List[Finding] = []
    for f in files:
        if not f.is_code:
            continue
        for line in f.added:
            for cat, sev, rx, msg, sug, exts in _COMPILED:
                if exts and not f.path.endswith(exts):
                    continue
                if rx.search(line.text):
                    findings.append(Finding(
                        category=cat, severity=sev, file=f.path, line=line.new_lineno,
                        description=msg, suggestion=sug, source="static"))
    return findings


def check_missing_tests(files: List[FileChange]) -> List[Finding]:
    """Flag changed source files when the diff touches no test file at all."""
    if any(f.is_test for f in files):
        return []
    return [
        Finding(
            category="missing_test", severity="medium", file=f.path, line=None,
            description=f"{len(f.added)} lines added in {f.path} but this change includes no test updates.",
            suggestion="Add tests covering the new or changed behaviour, including error paths.",
            source="static")
        for f in files if f.is_code and not f.is_test and f.added
    ]
