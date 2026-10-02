from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any, Protocol
from urllib.error import URLError
from urllib.request import Request, urlopen


class AIProvider(Protocol):
    name: str

    def recommend(self, context: Mapping[str, object]) -> dict[str, object]: ...


class OfflineProvider:
    name = "OFFLINE"

    def recommend(self, context: Mapping[str, object]) -> dict[str, object]:
        conflicts = context.get("conflicts", [])
        plans = context.get("optimizationPlans", [])
        conflict_list = conflicts if isinstance(conflicts, list) else []
        plan_list = plans if isinstance(plans, list) else []
        best_variant: dict[str, Any] | None = None
        best_plan: dict[str, Any] | None = None
        candidates: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
        for plan in plan_list:
            if not isinstance(plan, dict) or plan.get("status") != "FEASIBLE":
                continue
            variants = plan.get("variants", [])
            if not isinstance(variants, list):
                continue
            for variant in variants:
                if isinstance(variant, dict):
                    candidates.append((float(variant.get("score", 0)), plan, variant))
        if candidates:
            _, best_plan, best_variant = max(candidates, key=lambda item: item[0])
        conflict = (
            conflict_list[0] if conflict_list and isinstance(conflict_list[0], dict) else None
        )
        reasons = best_variant.get("explanation", {}) if best_variant else {}
        efficiency = context.get("efficiency")
        return {
            "summary": "Обнаружен конфликт инфраструктуры."
            if conflict
            else "Состояние станции проверено; применяйте только вариант актуального плана.",
            "problem": conflict.get("kind", "План оптимизации не рассчитан")
            if conflict
            else (
                "Нет актуального плана"
                if best_variant is None
                else "Требуется подтверждение диспетчера"
            ),
            "reasoningSummary": (
                "Предложение сформировано из серверных данных; безопасность "
                "проверяется повторно при принятии."
            ),
            "recommendedVariantId": best_variant.get("id") if best_variant else None,
            "planId": best_plan.get("id") if best_plan else None,
            "advantages": list(reasons.get("reasons", [])) if isinstance(reasons, dict) else [],
            "risks": ["Вариант станет недействительным после изменения stateVersion."],
            "affectedTrains": sorted(
                {
                    str(operation.get("trainId"))
                    for operation in (best_variant.get("operations", []) if best_variant else [])
                    if isinstance(operation, dict) and operation.get("trainId")
                }
            ),
            "expectedImpact": {
                "delayMs": reasons.get("totalDelayMs") if isinstance(reasons, dict) else None,
                "efficiencyScore": efficiency.get("score")
                if isinstance(efficiency, dict)
                else None,
            },
        }


class OpenAICompatibleProvider:
    name = "OPENAI_COMPATIBLE"

    def recommend(self, context: Mapping[str, object]) -> dict[str, object]:
        base_url = os.getenv("STATION_AI_BASE_URL", "").rstrip("/")
        model = os.getenv("STATION_AI_MODEL", "local-model")
        if not base_url:
            raise ValueError("ai_provider_not_configured")
        body = {
            "model": model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return a JSON dispatch explanation from the supplied state. "
                        "Never issue commands."
                    ),
                },
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
        }
        headers = {"Content-Type": "application/json"}
        api_key = os.getenv("STATION_AI_API_KEY")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request = Request(
            f"{base_url}/chat/completions",
            data=json.dumps(body).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=5) as response:
                result = json.loads(response.read(512_000))
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ValueError("ai_provider_unavailable") from exc
        content = result["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("ai_response_invalid")
        decoded = json.loads(content)
        if not isinstance(decoded, dict):
            raise ValueError("ai_response_invalid")
        return decoded


def provider_from_environment() -> AIProvider:
    configured = os.getenv("STATION_AI_PROVIDER", os.getenv("AI_PROVIDER", "template"))
    configured = configured.strip().replace("-", "_").upper()
    if configured in {"OPENAI_COMPATIBLE", "OLLAMA", "LM_STUDIO", "EXTERNAL"}:
        return OpenAICompatibleProvider()
    return OfflineProvider()


def validate_recommendation(
    payload: Mapping[str, object], allowed_variants: set[str] | Mapping[str, str]
) -> dict[str, object]:
    variant = payload.get("recommendedVariantId")
    variant_to_plan = allowed_variants if isinstance(allowed_variants, Mapping) else {}
    allowed = set(allowed_variants)
    validated_variant = variant if isinstance(variant, str) and variant in allowed else None
    trusted_plan_id = variant_to_plan.get(validated_variant) if validated_variant else None
    advantages = payload.get("advantages")
    risks = payload.get("risks")
    affected_trains = payload.get("affectedTrains")
    return {
        "summary": str(payload.get("summary", "AI recommendation unavailable"))[:500],
        "problem": str(payload.get("problem", ""))[:300],
        "reasoningSummary": str(payload.get("reasoningSummary", ""))[:1000],
        "recommendedVariantId": validated_variant,
        "planId": trusted_plan_id
        if trusted_plan_id is not None
        else payload.get("planId")
        if isinstance(payload.get("planId"), str) and not variant_to_plan
        else None,
        "advantages": [str(item)[:250] for item in advantages[:8]]
        if isinstance(advantages, list)
        else [],
        "risks": [str(item)[:250] for item in risks[:8]] if isinstance(risks, list) else [],
        "affectedTrains": [str(item)[:80] for item in affected_trains[:30]]
        if isinstance(affected_trains, list)
        else [],
        "expectedImpact": payload.get("expectedImpact", {})
        if isinstance(payload.get("expectedImpact"), dict)
        else {},
    }
