import json
from pathlib import Path

from review_agent.agent import MAX_ATTEMPTS, review_diff
from review_agent.diffparse import parse_diff
from review_agent.llm import ScriptedLLM
from review_agent.tools import check_missing_tests, scan_risky_patterns

SAMPLE = (Path(__file__).parent.parent / "samples" / "sample.diff").read_text()


def llm_reply(findings, summary="Looks risky."):
    return json.dumps({"summary": summary, "findings": findings})


def finding(**kw):
    base = dict(category="bug", severity="medium", file="app/users.py", line=18,
                description="Function returns None on failure so callers may crash.",
                suggestion="Raise or return a typed error.")
    base.update(kw)
    return base


# ---------- parser ----------
def test_parser_tracks_new_file_line_numbers():
    f = parse_diff(SAMPLE)[0]
    assert f.path == "app/users.py"
    by_no = {l.new_lineno: l.text for l in f.added}
    assert by_no[4].startswith("API_KEY")
    assert by_no[17].strip() == "except:"


def test_parser_ignores_deleted_files():
    diff = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-print('x')\n"
    assert parse_diff(diff) == []


# ---------- deterministic tools ----------
def test_static_scan_catches_planted_issues():
    found = {(f.line, f.category) for f in scan_risky_patterns(parse_diff(SAMPLE))}
    assert (4, "risk") in found      # hardcoded secret
    assert (15, "risk") in found     # verify=False
    assert (11, "bug") in found      # mutable default
    assert (17, "bug") in found      # bare except
    assert (12, "style") in found    # debug print


def test_sql_concat_with_quote_inside_string_is_caught():
    """Regression: first regex missed '... name = ' + name because of the inner apostrophe."""
    lines = {f.line for f in scan_risky_patterns(parse_diff(SAMPLE)) if "SQL" in f.description}
    assert lines == {24, 31}


def test_missing_tests_flagged_only_when_no_test_file_changed():
    files = parse_diff(SAMPLE)
    assert len(check_missing_tests(files)) == 1
    extra = "diff --git a/tests/test_users.py b/tests/test_users.py\n--- a/tests/test_users.py\n+++ b/tests/test_users.py\n@@ -0,0 +1 @@\n+def test_x(): pass\n"
    assert check_missing_tests(parse_diff(SAMPLE + extra)) == []


# ---------- agent behaviour with a misbehaving model ----------
def test_happy_path_merges_llm_and_static_findings():
    llm = ScriptedLLM([llm_reply([finding()])])
    r = review_diff(SAMPLE, llm)
    assert not r.degraded and r.llm_attempts == 1
    assert any(f.source == "llm" and f.line == 18 for f in r.findings)
    assert any(f.source == "static" for f in r.findings)
    assert r.findings[0].severity == "high"          # sorted by severity


def test_malformed_json_is_retried_with_error_fed_back():
    llm = ScriptedLLM(["Sure! Here is my review: not json", llm_reply([finding()])])
    r = review_diff(SAMPLE, llm)
    assert r.llm_attempts == 2 and not r.degraded
    assert "rejected" in llm.calls[1]                # retry prompt includes the failure reason


def test_json_wrapped_in_code_fence_is_accepted():
    llm = ScriptedLLM(["```json\n" + llm_reply([finding()]) + "\n```"])
    assert review_diff(SAMPLE, llm).llm_attempts == 1


def test_schema_violation_is_rejected_then_retried():
    bad = llm_reply([finding(category="catastrophe")])
    llm = ScriptedLLM([bad, llm_reply([finding()])])
    assert review_diff(SAMPLE, llm).llm_attempts == 2


def test_persistent_garbage_degrades_to_static_only():
    llm = ScriptedLLM(["nope"] * MAX_ATTEMPTS)
    r = review_diff(SAMPLE, llm)
    assert r.degraded and r.llm_attempts == MAX_ATTEMPTS
    assert r.findings and all(f.source == "static" for f in r.findings)
    assert "unavailable" in r.summary


def test_api_failure_is_handled_like_bad_output():
    class Boom:
        def complete(self, s, u):
            raise TimeoutError("network down")
    r = review_diff(SAMPLE, Boom())
    assert r.degraded and "network down" in r.summary


def test_hallucinated_file_is_dropped_and_bad_line_is_nulled():
    llm = ScriptedLLM([llm_reply([
        finding(file="app/payments.py", line=3),        # file not in diff
        finding(line=999, description="Cites a line that does not exist in the diff."),
    ])])
    r = review_diff(SAMPLE, llm)
    assert r.dropped_ungrounded == 1
    llm_f = [f for f in r.findings if f.source == "llm"]
    assert len(llm_f) == 1 and llm_f[0].line is None


def test_llm_duplicate_of_static_finding_is_not_repeated():
    llm = ScriptedLLM([llm_reply([finding(category="bug", line=17,
                                          description="Bare except hides real errors.")])])
    r = review_diff(SAMPLE, llm)
    assert len([f for f in r.findings if f.line == 17 and f.category == "bug"]) == 1


def test_no_llm_mode_and_empty_diff():
    r = review_diff(SAMPLE, None)
    assert not r.degraded and r.llm_attempts == 0 and r.findings
    assert review_diff("", ScriptedLLM([])).summary.startswith("No reviewable")
