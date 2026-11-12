"""Minimal unified-diff parser that keeps NEW-file line numbers for every line."""
import re
from dataclasses import dataclass, field
from typing import List, Optional

HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
TEST_PATH_RE = re.compile(
    r"(^|/)(tests?|__tests__)/|(^|/)test_[^/]*$|_test\.[a-z]+$|\.(test|spec)\.[a-z]+$"
)
CODE_EXTS = (".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".cs", ".rb", ".rs", ".php", ".c", ".cpp")


@dataclass
class DiffLine:
    kind: str                   # "+" added, "-" removed, " " context
    new_lineno: Optional[int]   # None for removed lines
    text: str


@dataclass
class FileChange:
    path: str
    lines: List[DiffLine] = field(default_factory=list)

    @property
    def added(self) -> List[DiffLine]:
        return [l for l in self.lines if l.kind == "+"]

    @property
    def added_linenos(self) -> set:
        return {l.new_lineno for l in self.added}

    @property
    def is_test(self) -> bool:
        return bool(TEST_PATH_RE.search(self.path))

    @property
    def is_code(self) -> bool:
        return self.path.endswith(CODE_EXTS)


def parse_diff(diff_text: str) -> List[FileChange]:
    files: List[FileChange] = []
    current: Optional[FileChange] = None
    new_ln = 0
    in_hunk = False

    for raw in diff_text.splitlines():
        if raw.startswith("diff --git"):
            current, in_hunk = None, False
        elif raw.startswith("+++ ") and not in_hunk:
            path = raw[4:].strip()
            if path == "/dev/null":          # file deleted: nothing to review
                current = None
            else:
                current = FileChange(path=path[2:] if path.startswith("b/") else path)
                files.append(current)
        elif raw.startswith("--- ") and not in_hunk:
            continue
        elif (m := HUNK_RE.match(raw)) and current is not None:
            new_ln, in_hunk = int(m.group(1)), True
        elif in_hunk and current is not None:
            if raw.startswith("+"):
                current.lines.append(DiffLine("+", new_ln, raw[1:]))
                new_ln += 1
            elif raw.startswith("-"):
                current.lines.append(DiffLine("-", None, raw[1:]))
            elif raw.startswith("\\"):        # "\ No newline at end of file"
                continue
            else:
                current.lines.append(DiffLine(" ", new_ln, raw[1:] if raw else ""))
                new_ln += 1
    return files


def annotate(files: List[FileChange]) -> str:
    """Render the diff with explicit new-file line numbers so the model can cite them."""
    out = []
    for f in files:
        out.append(f"=== FILE: {f.path} ===")
        for l in f.lines:
            if l.kind == "-":
                out.append(f"-     | {l.text}")
            else:
                out.append(f"{l.kind}{l.new_lineno:>4} | {l.text}")
    return "\n".join(out)
