"""Provider-agnostic LLM access layer.

**The only LLM entry point.** Everything else (graph nodes, api, cli) calls this
module and never imports `langchain_anthropic`, `langchain_openai`, or any other
concrete implementation.

Switching provider is an environment change, not a code change:

    LLM_PROVIDER=anthropic  LLM_MODEL=claude-sonnet-4-5-20250929
    LLM_PROVIDER=openai     LLM_MODEL=gpt-4.1-mini
    LLM_PROVIDER=openai     LLM_MODEL=qwen2.5  LLM_BASE_URL=http://localhost:11434/v1
    LLM_PROVIDER=mock                          # keyword rules, no key needed

This works because every node uses exactly two capabilities: `invoke(messages)`
and `with_structured_output(PydanticModel)`. The latter runs as tool-use on
Anthropic and as a JSON schema on OpenAI; LangChain already hides the seam.
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any, TypeVar

from .config import settings

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.runnables import Runnable
    from pydantic import BaseModel

TModel = TypeVar("TModel", bound="BaseModel")

# provider -> (extra package to install, expected key env var) for readable errors
_PROVIDER_PACKAGES = {
    "anthropic": ("langchain-anthropic", "ANTHROPIC_API_KEY"),
    "openai": ("langchain-openai", "OPENAI_API_KEY"),
    "azure_openai": ("langchain-openai", "AZURE_OPENAI_API_KEY"),
    "google_genai": ("langchain-google-genai", "GOOGLE_API_KEY"),
    "groq": ("langchain-groq", "GROQ_API_KEY"),
    "ollama": ("langchain-ollama", None),
}


class LLMConfigError(RuntimeError):
    """A configuration problem (missing package or key), distinct from a call failure."""


def _provider_kwargs(provider: str) -> dict[str, Any]:
    """Translate the generic settings into each provider's parameter names.

    The differences are few; keep them contained here rather than leaking into
    calling code.
    """
    kwargs: dict[str, Any] = {"temperature": settings.llm_temperature}

    # ollama rejects max_tokens; every other provider accepts it.
    if provider != "ollama":
        kwargs["max_tokens"] = settings.llm_max_tokens

    if settings.llm_base_url:
        kwargs["base_url"] = settings.llm_base_url

    return kwargs


def is_mock() -> bool:
    """Whether the stub provider is active. The UI must badge this - a keyword
    stub may never pass itself off as real inference."""
    return settings.llm_provider.strip().lower() == "mock"


@functools.lru_cache(maxsize=8)
def get_chat_model(
    model: str | None = None,
    provider: str | None = None,
) -> BaseChatModel:
    """Return a LangChain chat model. Cached, so repeated calls reuse the client.

    Args:
        model: overrides LLM_MODEL.
        provider: overrides LLM_PROVIDER.
    """
    from langchain.chat_models import init_chat_model

    provider = (provider or settings.llm_provider).strip().lower()
    model = model or settings.llm_model

    try:
        return init_chat_model(model=model, model_provider=provider, **_provider_kwargs(provider))
    except ImportError as exc:
        pkg, _ = _PROVIDER_PACKAGES.get(provider, (f"langchain-{provider}", None))
        raise LLMConfigError(
            f"Provider '{provider}' needs the extra dependency {pkg}.\n"
            f"  uv add {pkg}          # or: uv sync --extra {provider}"
        ) from exc
    except Exception as exc:  # usually a missing API key
        _, key_env = _PROVIDER_PACKAGES.get(provider, (None, None))
        hint = f"\n  Set {key_env} in .env" if key_env else ""
        raise LLMConfigError(
            f"Failed to initialise provider '{provider}' (model={model}): {exc}{hint}"
        ) from exc


def structured(schema: type[TModel], *, model: str | None = None) -> Runnable[Any, TModel]:
    """Return a runnable that is forced to emit an instance of `schema`.

    This is the **only** sanctioned way for a node to call the LLM. The premise
    of Task 1 is reducing errors, so every step asks the model to fill in a form
    rather than write prose.
    """
    if is_mock():
        from .mock_llm import MockStructuredRunnable

        return MockStructuredRunnable(schema)  # type: ignore[return-value]
    return get_chat_model(model=model).with_structured_output(schema)


def describe_llm() -> str:
    """One-line description for the CLI and the health check."""
    if is_mock():
        return "mock (keyword rules, not real inference)"
    base = f" @ {settings.llm_base_url}" if settings.llm_base_url else ""
    return f"{settings.llm_provider}:{settings.llm_model}{base}"


def check_llm_ready() -> tuple[bool, str]:
    """Startup self-check: can a client be constructed? Sends no real request."""
    if is_mock():
        return True, describe_llm()
    try:
        get_chat_model()
    except LLMConfigError as exc:
        return False, str(exc)
    return True, describe_llm()
