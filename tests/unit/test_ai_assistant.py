from backend.app.services.ai_assistant import (
    OfflineProvider,
    provider_from_environment,
    validate_recommendation,
)


def test_offline_assistant_produces_structured_fallback_without_live_provider() -> None:
    result = OfflineProvider().recommend({"conflicts": [], "optimizationPlans": []})

    assert result["recommendedVariantId"] is None
    assert result["planId"] is None
    assert result["summary"]
    assert result["risks"]


def test_ai_cannot_recommend_variant_outside_server_allowlist() -> None:
    result = validate_recommendation(
        {"recommendedVariantId": "untrusted", "summary": "Use this route"},
        {"server-approved"},
    )

    assert result["recommendedVariantId"] is None


def test_ai_cannot_pair_an_allowed_variant_with_another_plan() -> None:
    result = validate_recommendation(
        {"recommendedVariantId": "variant-1", "planId": "forged-plan"},
        {"variant-1": "server-plan"},
    )

    assert result["recommendedVariantId"] == "variant-1"
    assert result["planId"] == "server-plan"


def test_none_template_and_compatible_ai_modes_are_configurable(monkeypatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "none")
    assert provider_from_environment().name == "OFFLINE"
    monkeypatch.setenv("AI_PROVIDER", "template")
    assert provider_from_environment().name == "OFFLINE"
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    assert provider_from_environment().name == "OPENAI_COMPATIBLE"
