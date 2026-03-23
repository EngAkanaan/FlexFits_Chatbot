from __future__ import annotations

from typing import Any, Dict, cast

from modules.prompts import missing_policy_coverage, resolve_policy_name, supported_intents
from modules.rag_service import RagService


def test_all_supported_intents_have_policy_coverage() -> None:
    missing = missing_policy_coverage(supported_intents())
    assert missing == []


def test_core_intents_resolve_expected_policy_files() -> None:
    assert resolve_policy_name("search_product") == "product_availability_policy.xml"
    assert resolve_policy_name("sale_query") == "sale_policy.xml"
    assert resolve_policy_name("recommend") == "upsell_policy.xml"
    assert resolve_policy_name("delivery_info") == "delivery_policy.xml"
    assert resolve_policy_name("return_policy") == "return_policy.xml"
    assert resolve_policy_name("human_handoff") == "human_escalation_policy.xml"


def test_support_flow_preserves_intent_and_policy_metadata() -> None:
    service = RagService()

    result = cast(
        Dict[str, Any],
        service.generate_support_reply(
        user_message="i need return policy",
        intent="return_policy",
        state_entities={"brand": "Nike"},
        ),
    )

    assert result["metadata"]["intent"] == "return_policy"
    assert result["metadata"]["policy_file"] == "return_policy.xml"
    assert isinstance(result["context_chunks"], list)
    assert isinstance(result["reply"], str)
    assert result["reply"].strip() != ""
