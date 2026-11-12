# Diff Review Agent

An AI agent that reviews a git diff / pull request and returns structured feedback: **bugs, risks,
missing tests, and style issues**, each with file, line, severity, and a suggested fix.

```
git diff main | python -m review_agent -        # or: python -m review_agent change.diff [--json] [--no-llm]
```

## How I built it
- **Orchestration:** LangGraph state machine: `parse → static tools → llm_review ⟲ → merge`.
- **Model:** Groq `llama-3.3-70b-versatile` (override with `GROQ_MODEL`), called in JSON mode at temperature 0.1.
- **Tools (called by the graph, not chosen by the LLM):**
  a diff parser that keeps new-file line numbers; a risky-pattern scanner (hardcoded secrets, SQL built by
  string concatenation, `verify=False`, `shell=True`, `eval`, bare `except`, mutable defaults); and a
  missing-test checker. Their findings are shown to the model so it does not repeat them, and they are
  still returned if the model fails.
- **Reliability layer:** the model reply must validate against a Pydantic schema; if not, the error is fed
  back and it retries (max 3). After that the agent degrades to static findings and says so. A
  **grounding check** drops findings that cite files not in the diff and clears line numbers that are not
  added lines. LLM findings that duplicate a static one are removed.
- **Stack:** Python 3.12, LangGraph, Pydantic, Groq SDK, pytest (14 tests). AI assistance (Claude) was used
  to build it.

## What went wrong, and how I handled it
1. **A bug in my own scanner.** Running it on the sample diff showed the SQL-injection regex missed
   `execute("... name = '" + name + "'")` because of the apostrophe inside the string. I rewrote it to
   match string literals properly and added a regression test.
2. **Unreliable model output** (prose around JSON, code fences, invalid enum values, invented files or
   lines) is the main design risk. I handled it with schema validation, retry with the error fed back,
   fence stripping, and the grounding check. The tests simulate each failure with a scripted model.
   **Honest limit:** these cases are simulated; I have not yet measured how often a live model triggers them.
3. **Large diffs** exceed what I want to send in one prompt. For now the prompt is truncated at 24k
   characters and the result is flagged `truncated`; this is a known gap, not a solution.

## What I would do next
- Let the model **choose tools** via tool calling (e.g. fetch the full file around a hunk) instead of the
  fixed pipeline.
- Review large diffs **per file and merge** instead of truncating.
- Add real linters (ruff, eslint) as tools and support more languages.
- Build a small **labelled eval set** of diffs to measure precision/recall and compare models.
- Post findings as inline comments on a GitHub PR, and add a confidence score per finding.

## Run it
```
python -m venv venv && venv\Scripts\activate      # Python 3.12 recommended
pip install -r requirements.txt
copy .env.example .env                            # add GROQ_API_KEY
python -m review_agent samples/sample.diff        # includes planted issues
python -m review_agent samples/sample.diff --no-llm   # deterministic checks only, no API key needed
pytest
```
