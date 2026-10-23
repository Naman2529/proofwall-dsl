"""The one place the project talks to a language model.

STATUS: stub. `default_client()` raises NotImplementedError.
TODO(AI part): implement default_client() for the model we choose (API or
local). Keep the LLMClient shape (prompt string in, completion string out) so
generator.py, llm_optimizer.py and the tests keep working unchanged. Read API
keys from environment variables, never commit them.
"""

from __future__ import annotations

from typing import Callable

# A client is just a function: prompt in, model text out.
LLMClient = Callable[[str], str]


def default_client() -> LLMClient:
    raise NotImplementedError(
        "No LLM is wired in yet. Implement policyc/ai/llm_client.py:default_client(), "
        "or pass your own `llm=` function to generate_policy() / optimize_with_llm().")


def strip_code_fence(text: str) -> str:
    """Models like to wrap code in ``` fences. Keep only the inside."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text
