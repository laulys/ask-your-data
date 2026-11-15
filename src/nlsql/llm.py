"""Language-model backends.

The agent only needs one thing from a model: give it a system prompt and a
question, get text back. That is the whole :class:`LLM` interface, which is why
swapping IBM watsonx for a model running on your laptop is a one-line change in
``.env`` rather than a rewrite.

Each provider imports its SDK lazily, so you only install the one you use.
"""

from __future__ import annotations

import abc
import os

import httpx

__all__ = [
    "LLM",
    "AnthropicLLM",
    "OllamaLLM",
    "OpenAILLM",
    "WatsonxLLM",
    "build_llm",
]

DEFAULT_TIMEOUT = 180.0


class LLM(abc.ABC):
    """Minimal text-in, text-out interface."""

    name: str = "llm"
    model: str = ""

    @abc.abstractmethod
    def generate(
        self,
        system: str,
        prompt: str,
        *,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        """Return the model's reply as plain text."""

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"{type(self).__name__}(model={self.model!r})"


class OllamaLLM(LLM):
    """A model running locally through Ollama. Free, private, no API key."""

    name = "ollama"

    def __init__(
        self,
        model: str | None = None,
        host: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.model = model or os.getenv("OLLAMA_MODEL", "qwen3:8b")
        self.host = (host or os.getenv("OLLAMA_HOST", "http://localhost:11434")).rstrip("/")
        self.timeout = timeout
        # Reasoning models (qwen3, deepseek-r1) burn time and tokens thinking
        # before answering. Turning that off roughly halves latency here, but
        # older servers and non-reasoning models reject the flag, so the first
        # rejection disables it for the rest of the session.
        self._supports_think_flag = True

    def generate(
        self,
        system: str,
        prompt: str,
        *,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        payload: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if self._supports_think_flag:
            payload["think"] = False

        try:
            response = httpx.post(f"{self.host}/api/chat", json=payload, timeout=self.timeout)
        except httpx.ConnectError as err:
            raise RuntimeError(
                f"Could not reach Ollama at {self.host}. Is it running? "
                "Start it with `ollama serve`, or install it from https://ollama.com"
            ) from err

        if response.status_code == 400 and "think" in response.text.lower():
            self._supports_think_flag = False
            payload.pop("think", None)
            response = httpx.post(f"{self.host}/api/chat", json=payload, timeout=self.timeout)

        if response.status_code == 404:
            raise RuntimeError(
                f"Ollama does not have the model {self.model!r}. Pull it with: "
                f"ollama pull {self.model}"
            )
        response.raise_for_status()
        return response.json().get("message", {}).get("content", "")


class OpenAILLM(LLM):
    """OpenAI, or any server that speaks the OpenAI chat-completions API."""

    name = "openai"

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        try:
            from openai import OpenAI
        except ImportError as err:  # pragma: no cover - depends on extras installed
            raise RuntimeError("The OpenAI backend needs its SDK: pip install '.[openai]'") from err

        key = api_key or os.getenv("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY is not set. Add it to your .env file.")

        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self._client = OpenAI(api_key=key, base_url=os.getenv("OPENAI_BASE_URL") or None)

    def generate(
        self,
        system: str,
        prompt: str,
        *,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        completion = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return completion.choices[0].message.content or ""


class AnthropicLLM(LLM):
    """Anthropic's Claude models."""

    name = "anthropic"

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        try:
            import anthropic
        except ImportError as err:  # pragma: no cover - depends on extras installed
            raise RuntimeError(
                "The Anthropic backend needs its SDK: pip install '.[anthropic]'"
            ) from err

        key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set. Add it to your .env file.")

        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5")
        self._client = anthropic.Anthropic(api_key=key)

    def generate(
        self,
        system: str,
        prompt: str,
        *,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        message = self._client.messages.create(
            model=self.model,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return "".join(block.text for block in message.content if block.type == "text")


class WatsonxLLM(LLM):
    """IBM watsonx.ai — the backend used by the original IBM Skills Network lab.

    The lab passes ``project_id="skills-network"`` with no credentials, which
    only works inside IBM's hosted lab environment. Outside it you need your own
    (free tier) API key and project id.
    """

    name = "watsonx"

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        project_id: str | None = None,
        url: str | None = None,
    ) -> None:
        try:
            from ibm_watsonx_ai import Credentials
            from ibm_watsonx_ai.foundation_models import ModelInference
        except ImportError as err:  # pragma: no cover - depends on extras installed
            raise RuntimeError(
                "The watsonx backend needs its SDK: pip install '.[watsonx]'"
            ) from err

        key = api_key or os.getenv("WATSONX_API_KEY")
        project = project_id or os.getenv("WATSONX_PROJECT_ID")
        if not key or not project:
            raise RuntimeError(
                "watsonx needs WATSONX_API_KEY and WATSONX_PROJECT_ID. "
                "Create them at https://cloud.ibm.com (the free tier is enough)."
            )

        self.model = model or os.getenv("WATSONX_MODEL", "ibm/granite-4-h-small")
        self._project_id = project
        self._credentials = Credentials(
            url=url or os.getenv("WATSONX_URL", "https://us-south.ml.cloud.ibm.com"),
            api_key=key,
        )
        self._model_inference = ModelInference

    def generate(
        self,
        system: str,
        prompt: str,
        *,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        model = self._model_inference(
            model_id=self.model,
            credentials=self._credentials,
            project_id=self._project_id,
            params={
                "max_new_tokens": max_tokens,
                "temperature": temperature,
                "decoding_method": "greedy" if temperature == 0 else "sample",
            },
        )
        return model.generate_text(prompt=f"{system}\n\n{prompt}")


_PROVIDERS: dict[str, type[LLM]] = {
    "ollama": OllamaLLM,
    "openai": OpenAILLM,
    "anthropic": AnthropicLLM,
    "watsonx": WatsonxLLM,
}


def build_llm(provider: str, model: str | None = None) -> LLM:
    """Create the backend named by ``provider``."""
    key = provider.strip().lower()
    if key not in _PROVIDERS:
        raise ValueError(
            f"Unknown provider {provider!r}. Choose one of: {', '.join(sorted(_PROVIDERS))}."
        )
    return _PROVIDERS[key](model=model)
