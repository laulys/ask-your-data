"""Settings parsing and the provider registry."""

from __future__ import annotations

import httpx
import pytest

from nlsql.config import Settings
from nlsql.llm import OllamaLLM, build_llm


class TestSettings:
    def test_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in ("NLSQL_PROVIDER", "NLSQL_DATABASE_URL", "NLSQL_MAX_ROWS"):
            monkeypatch.delenv(key, raising=False)

        settings = Settings.from_env(load_dotenv_file=False)

        assert settings.provider == "ollama"
        assert settings.database_url == "sqlite:///data/chinook.db"
        assert settings.max_rows == 200

    def test_reads_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NLSQL_PROVIDER", "OpenAI")  # case is forgiven
        monkeypatch.setenv("NLSQL_DATABASE_URL", "sqlite:///other.db")
        monkeypatch.setenv("NLSQL_MAX_ROWS", "42")

        settings = Settings.from_env(load_dotenv_file=False)

        assert settings.provider == "openai"
        assert settings.database_url == "sqlite:///other.db"
        assert settings.max_rows == 42

    def test_unknown_provider_is_rejected_early(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NLSQL_PROVIDER", "gpt5-turbo-ultra")
        with pytest.raises(ValueError, match="Unknown NLSQL_PROVIDER"):
            Settings.from_env(load_dotenv_file=False)

    def test_non_numeric_max_rows_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NLSQL_MAX_ROWS", "lots")
        with pytest.raises(ValueError, match="whole number"):
            Settings.from_env(load_dotenv_file=False)


class TestProviderRegistry:
    def test_unknown_provider(self) -> None:
        with pytest.raises(ValueError, match="Unknown provider"):
            build_llm("telepathy")

    def test_ollama_needs_no_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OLLAMA_MODEL", "llama3")
        llm = build_llm("ollama")
        assert isinstance(llm, OllamaLLM)
        assert llm.model == "llama3"

    def test_missing_api_key_fails_with_a_useful_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        pytest.importorskip("openai")
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            build_llm("openai")


class TestOllamaTransport:
    def test_unreachable_server_explains_itself(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def refuse(*args, **kwargs):
            raise httpx.ConnectError("nope")

        monkeypatch.setattr(httpx, "post", refuse)
        with pytest.raises(RuntimeError, match="Is it running"):
            OllamaLLM(model="x", host="http://localhost:1").generate("sys", "hi")

    def test_missing_model_names_the_pull_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            httpx,
            "post",
            lambda url, **k: httpx.Response(
                404, text="model not found", request=httpx.Request("POST", url)
            ),
        )
        with pytest.raises(RuntimeError, match="ollama pull mistral"):
            OllamaLLM(model="mistral").generate("sys", "hi")

    def test_server_rejecting_the_think_flag_is_retried_without_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent: list[dict] = []

        def fake_post(url, json=None, timeout=None):
            sent.append(json)
            request = httpx.Request("POST", url)
            if "think" in json:
                return httpx.Response(
                    400, text='{"error":"model does not support think"}', request=request
                )
            return httpx.Response(200, json={"message": {"content": "SELECT 1"}}, request=request)

        monkeypatch.setattr(httpx, "post", fake_post)
        llm = OllamaLLM(model="llama3")

        assert llm.generate("sys", "hi") == "SELECT 1"
        assert len(sent) == 2
        assert "think" not in sent[1]
        # The flag stays off for later calls rather than failing again.
        assert llm._supports_think_flag is False
