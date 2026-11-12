import os
from typing import List, Optional, Protocol

from dotenv import load_dotenv

load_dotenv()

DEFAULT_MODEL = "openai/gpt-oss-120b"


class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class GroqLLM:
    """Thin wrapper over the Groq chat API using JSON mode."""

    def __init__(self, model: Optional[str] = None, api_key: Optional[str] = None):
        from groq import Groq  # imported lazily so tests/offline mode need no key

        key = api_key or os.getenv("GROQ_API_KEY")
        if not key:
            raise RuntimeError("GROQ_API_KEY is not set (see .env.example), or run with --no-llm")
        self.client = Groq(api_key=key)
        self.model = model or os.getenv("GROQ_MODEL", DEFAULT_MODEL)

    def complete(self, system: str, user: str) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            temperature=0.1,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content or ""


class ScriptedLLM:
    """Returns pre-baked responses in order. Used by tests to simulate bad model behaviour."""

    def __init__(self, responses: List[str]):
        self.responses = list(responses)
        self.calls: List[str] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append(user)
        return self.responses.pop(0) if self.responses else ""
