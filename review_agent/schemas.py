from typing import List, Literal, Optional

from pydantic import BaseModel, Field

Category = Literal["bug", "risk", "missing_test", "style"]
Severity = Literal["high", "medium", "low"]


class LLMFinding(BaseModel):
    """Shape the model is asked to return. Validated strictly before use."""
    category: Category
    severity: Severity
    file: str
    line: Optional[int] = None
    description: str = Field(min_length=5)
    suggestion: Optional[str] = None


class LLMReview(BaseModel):
    summary: str
    findings: List[LLMFinding] = Field(default_factory=list)


class Finding(LLMFinding):
    source: Literal["static", "llm"] = "llm"


class ReviewResult(BaseModel):
    summary: str
    findings: List[Finding] = Field(default_factory=list)
    degraded: bool = False          # True when the LLM output could not be used
    llm_attempts: int = 0
    dropped_ungrounded: int = 0     # LLM findings discarded for citing code not in the diff
    truncated: bool = False         # True when the diff was too large to send in full
