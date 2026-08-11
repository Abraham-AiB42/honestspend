"""Multi-provider LLM categorizer selection (no live network)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from financial_os.config import settings
from financial_os.services import secrets_store as ss
from financial_os.services.categorizer import _build_categorize_prompt, suggest_from_llm


def test_build_prompt_shape():
    class T:
        txn_date = None
        amount = "12.34"
        payee = "SHELL"
        memo = "gas"

    class C:
        id = 1
        code = "PER_GAS"
        display_name = "Gas"
        tax_form = None
        tax_line = None
        budget_group = "transport"

    p = _build_categorize_prompt(T(), [C()])  # type: ignore[arg-type]
    assert p["transaction"]["payee"] == "SHELL"
    assert p["categories"][0]["code"] == "PER_GAS"


def test_suggest_from_llm_openai_compatible(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    monkeypatch.setattr(settings, "xai_api_key", None)
    ss.save_ai_credentials(provider="openai", api_key="sk-test")

    class T:
        id = 1
        profile_id = 1
        txn_date = None
        amount = "40"
        payee = "SHELL OIL"
        memo = ""

    class C:
        id = 7
        code = "PER_GAS"
        display_name = "Gas"
        tax_form = None
        tax_line = None
        budget_group = "transport"
        profile_id = None
        scope = "system"

    class FakeSession:
        def get(self, model, cid):
            return C() if cid == 7 else None

    fake_json = '{"category_id": 7, "confidence": 0.9, "reason": "gas station", "is_transfer": false}'

    with patch(
        "financial_os.services.categorizer._chat_openai_compatible",
        return_value=fake_json,
    ) as mock_chat:
        sug = suggest_from_llm(FakeSession(), T(), candidates=[C()])  # type: ignore[arg-type]
        assert sug is not None
        assert sug.category_id == 7
        assert sug.source == "openai"
        assert mock_chat.called


def test_suggest_from_llm_tries_next_provider(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    monkeypatch.setattr(settings, "xai_api_key", None)
    ss.save_ai_credentials(provider="openai", api_key="sk-o")
    ss.save_ai_credentials(provider="anthropic", api_key="sk-a")

    class T:
        id = 1
        profile_id = 1
        txn_date = None
        amount = "10"
        payee = "X"
        memo = ""

    class C:
        id = 3
        code = "PER_OTHER"
        display_name = "Other"
        tax_form = None
        tax_line = None
        budget_group = None
        profile_id = None
        scope = "system"

    class FakeSession:
        def get(self, model, cid):
            return C() if cid == 3 else None

    def boom(*a, **k):
        raise RuntimeError("openai down")

    with patch(
        "financial_os.services.categorizer._chat_openai_compatible",
        side_effect=boom,
    ), patch(
        "financial_os.services.categorizer._chat_anthropic",
        return_value='{"category_id": 3, "confidence": 0.8, "reason": "fallback", "is_transfer": false}',
    ):
        sug = suggest_from_llm(FakeSession(), T(), candidates=[C()])  # type: ignore[arg-type]
        assert sug is not None
        assert sug.source == "anthropic"
        assert sug.category_id == 3
