from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Dict, List


BASE_DIR = Path(__file__).resolve().parents[1]
POLICY_DIR = BASE_DIR / "knowledge" / "policies"


DEFAULT_POLICY_FILE = "system_policy.xml"


SUPPORTED_INTENTS: List[str] = [
    "greeting",
    "small_talk",
    "thank_you",
    "goodbye",
    "create_order",
    "intent_to_order",
    "cancel_order",
    "finish_order",
    "list_categories",
    "check_category_availability",
    "recommend",
    "help",
    "search_product",
    "ask_color",
    "ask_size",
    "ask_brand",
    "ask_type",
    "ask_price",
    "about_authenticity",
    "about_location",
    "about_showroom",
    "about_exchange",
    "about_refund",
    "payment_methods",
    "delivery_timeline",
    "sale_query",
    "delivery_info",
    "return_policy",
    "human_handoff",
    "check_availability",
    "recommend_fallback_keywords",
    "price_extreme",
    "check_price",
    "show_all",
    "fallback",
]


INTENT_POLICY_MAP: Dict[str, str] = {
    "greeting": DEFAULT_POLICY_FILE,
    "small_talk": DEFAULT_POLICY_FILE,
    "thank_you": DEFAULT_POLICY_FILE,
    "goodbye": DEFAULT_POLICY_FILE,
    "create_order": DEFAULT_POLICY_FILE,
    "intent_to_order": DEFAULT_POLICY_FILE,
    "cancel_order": DEFAULT_POLICY_FILE,
    "finish_order": "order_finalization_policy.xml",
    "list_categories": DEFAULT_POLICY_FILE,
    "search_product": "product_availability_policy.xml",
    "ask_color": DEFAULT_POLICY_FILE,
    "ask_size": DEFAULT_POLICY_FILE,
    "ask_brand": DEFAULT_POLICY_FILE,
    "ask_type": DEFAULT_POLICY_FILE,
    "ask_price": DEFAULT_POLICY_FILE,
    "about_authenticity": DEFAULT_POLICY_FILE,
    "about_location": DEFAULT_POLICY_FILE,
    "about_showroom": DEFAULT_POLICY_FILE,
    "about_exchange": DEFAULT_POLICY_FILE,
    "about_refund": "return_policy.xml",
    "payment_methods": DEFAULT_POLICY_FILE,
    "delivery_timeline": "delivery_policy.xml",
    "check_availability": "product_availability_policy.xml",
    "check_category_availability": "product_availability_policy.xml",
    "sale_query": "sale_policy.xml",
    "recommend": "upsell_policy.xml",
    "help": DEFAULT_POLICY_FILE,
    "recommend_fallback_keywords": "upsell_policy.xml",
    "delivery_info": "delivery_policy.xml",
    "return_policy": "return_policy.xml",
    "human_handoff": "human_escalation_policy.xml",
    "price_extreme": DEFAULT_POLICY_FILE,
    "check_price": DEFAULT_POLICY_FILE,
    "show_all": DEFAULT_POLICY_FILE,
    "fallback": DEFAULT_POLICY_FILE,
}


def _read_text_file(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def load_system_policy() -> str:
    return _read_text_file(POLICY_DIR / DEFAULT_POLICY_FILE)


def resolve_policy_name(intent: str) -> str:
    if not intent:
        return DEFAULT_POLICY_FILE
    return INTENT_POLICY_MAP.get(intent.strip().lower(), DEFAULT_POLICY_FILE)


def load_policy_for_intent(intent: str) -> str:
    policy_file = resolve_policy_name(intent)
    return _read_text_file(POLICY_DIR / policy_file)


def build_system_prompt(intent: str) -> str:
    system_policy = load_system_policy()
    active_policy = load_policy_for_intent(intent)

    parts: List[str] = []
    if system_policy:
        parts.append(system_policy)
    if active_policy and active_policy != system_policy:
        parts.append(active_policy)

    return "\n\n".join(parts).strip()


def serialize_state_entities(state_entities: Dict[str, object]) -> str:
    """Create stable, deterministic entity serialization for prompt payloads."""
    return json.dumps(state_entities, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_user_prompt(
    user_message: str,
    intent: str,
    state_entities: Dict[str, object],
    context_chunks: List[str],
) -> str:
    resolved_policy = resolve_policy_name(intent)
    escaped_message = escape(user_message)
    serialized_entities = escape(serialize_state_entities(state_entities))
    context_text = "\n".join(f"    <chunk>- {escape(chunk)}</chunk>" for chunk in context_chunks if chunk)

    return (
        "<runtime_context>\n"
        f"  <intent>{escape(intent)}</intent>\n"
        f"  <active_policy_file>{escape(resolved_policy)}</active_policy_file>\n"
        f"  <message>{escaped_message}</message>\n"
        f"  <session_entities_json>{serialized_entities}</session_entities_json>\n"
        "  <retrieved_context>\n"
        f"{context_text}\n"
        "  </retrieved_context>\n"
        "</runtime_context>"
    )


def build_prompt_package(
    user_message: str,
    intent: str,
    state_entities: Dict[str, object],
    context_chunks: List[str],
) -> Dict[str, str]:
    system_prompt = build_system_prompt(intent)
    user_prompt = build_user_prompt(
        user_message=user_message,
        intent=intent,
        state_entities=state_entities,
        context_chunks=context_chunks,
    )

    return {
        "system": system_prompt,
        "user": user_prompt,
    }


def supported_intents() -> List[str]:
    return list(SUPPORTED_INTENTS)


def missing_policy_coverage(required_intents: List[str] | None = None) -> List[str]:
    intents = required_intents or SUPPORTED_INTENTS
    return [intent for intent in intents if intent not in INTENT_POLICY_MAP]
