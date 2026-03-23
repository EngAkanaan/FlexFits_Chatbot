from __future__ import annotations

import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from modules.intent_router import detect_intent, extract_color, extract_gender
from modules.entity_extractor import extract_entities
from modules.rag_service import RagService
from modules.response_generator import generate_support_response
from modules.conversation_memory import (
    reset_session_preserve_profile,
    update_search_memory,
    has_stored_search,
    get_stored_context,
    load_delivery_profile,
    save_delivery_profile,
)
from modules.intent_loader import get_intent_loader
from modules.supabase_gateway import get_supabase_client

_SEARCH_INTENTS = {
    "search_product", "sale_query", "recommend", "check_availability",
    "check_category_availability", "recommend_fallback_keywords",
}

STATE_IDLE = "STATE_IDLE"
STATE_BROWSING = "STATE_BROWSING"
STATE_PRODUCT_SELECTION = "STATE_PRODUCT_SELECTION"
STATE_CART = "STATE_CART"
STATE_CHECKOUT = "STATE_CHECKOUT"
STATE_ORDER_CONFIRMATION = "STATE_ORDER_CONFIRMATION"


BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs" / "chat_history"


@dataclass
class ChatState:
    language: str = "en"
    last_entities: Dict[str, object] = field(default_factory=dict)
    conversation_state: str = STATE_IDLE
    order_stage: str | None = None
    order_data: Dict[str, str] = field(default_factory=dict)
    last_results: list[str] = field(default_factory=list)
    bag: list[str] = field(default_factory=list)
    cancel_confirmation_pending: bool = False
    pending_cart_candidates: list[str] = field(default_factory=list)
    last_seen_at: int = field(default_factory=lambda: int(time.time()))


_ORDER_STAGE_SEQUENCE = [
    ("name", "Please provide your full name."),
    ("phone", "What's your phone number?"),
    ("email", "What's your email address?"),
    ("governorate", "Which governorate are you in?"),
    ("district", "What's your district?"),
    ("village", "What's your village or area?"),
    ("address_details", "Street and building details?"),
]

_BRAND_ALIASES: dict[str, tuple[str, ...]] = {
    "new balance": ("new balance", "newbalance", "nb"),
    "adidas": ("adidas",),
    "asics": ("asics",),
    "nike": ("nike",),
    "puma": ("puma",),
    "champion": ("champion",),
    "rocktail": ("rocktail",),
}


def _extract_structured_request(text: str) -> dict[str, str | None]:
    """Parse user text into structured brand/model/size fields for cart matching."""
    lowered = text.lower().strip()
    size_match = re.search(r"\bsize\s*(3[5-9]|4[0-9]|50)(?:\.(5|3))?\b", lowered)
    size_value: str | None = None
    if size_match:
        whole = size_match.group(1)
        frac = size_match.group(2)
        size_value = f"{whole}.{frac}" if frac else whole

    canonical_brand: str | None = None
    matched_alias: str | None = None
    for brand, aliases in _BRAND_ALIASES.items():
        for alias in aliases:
            if re.search(rf"\b{re.escape(alias)}\b", lowered):
                canonical_brand = brand
                matched_alias = alias
                break
        if canonical_brand:
            break

    model_source = lowered
    if matched_alias:
        model_source = re.sub(rf"\b{re.escape(matched_alias)}\b", " ", model_source)
    model_source = re.sub(r"\bsize\s*(3[5-9]|4[0-9]|50)(?:\.(5|3))?\b", " ", model_source)
    model_source = re.sub(
        r"\b(i|need|want|to|order|add|cart|bag|please|show|me|the|shoes|shoe|my|it|this|that|one)\b",
        " ",
        model_source,
    )
    model_tokens = [token for token in re.findall(r"[a-z0-9]+", model_source) if token]
    model_value = " ".join(model_tokens) if model_tokens else None

    return {
        "brand": canonical_brand,
        "model": model_value,
        "size": size_value,
    }


def _parse_inventory_snippet(snippet: str) -> dict[str, object] | None:
    parts = [part.strip() for part in snippet.split("|")]
    if len(parts) < 3:
        return None

    raw_name_part = parts[1]
    type_text = ""
    color_text = ""
    price_text = ""
    sizes_text = ""
    for part in parts[3:]:
        if part.startswith("type:"):
            type_text = part.replace("type:", "").strip()
        if part.startswith("color:"):
            color_text = part.replace("color:", "").strip()
        if part.startswith("price:"):
            price_text = part.replace("price:", "").replace("$", "").strip()
        if part.startswith("sizes:"):
            sizes_text = part.replace("sizes:", "").strip()
    if not type_text and len(parts) >= 3:
        type_text = parts[2].replace("type:", "").strip()

    lowered_name = raw_name_part.lower()
    brand_value: str | None = None
    model_value = raw_name_part
    for brand in _BRAND_ALIASES:
        if lowered_name.startswith(f"{brand} "):
            brand_value = brand
            model_value = raw_name_part[len(brand) :].strip()
            break
        if lowered_name == brand:
            brand_value = brand
            model_value = ""
            break

    if brand_value and model_value and model_value.lower() == brand_value:
        model_value = ""

    if brand_value and model_value:
        name_part = f"{brand_value.title()} {model_value}".strip()
    elif brand_value:
        name_part = brand_value.title()
    else:
        name_part = raw_name_part

    size_tokens = re.findall(r"\d+(?:\.\d+)?", sizes_text)
    size_set = {token for token in size_tokens}

    label = f"{name_part} ({type_text})"
    if price_text:
        label = f"{label} ${price_text}"

    return {
        "name": name_part,
        "brand": brand_value,
        "model": model_value.lower(),
        "type": type_text,
        "color": color_text,
        "price": f"${price_text}" if price_text else "",
        "sizes": size_set,
        "label": label,
    }


def _sorted_size_labels(size_values: set[str]) -> list[str]:
    def _key(value: str) -> float:
        try:
            return float(value)
        except ValueError:
            return 999.0

    return sorted(size_values, key=_key)


def _format_catalog_card(parsed: dict[str, object]) -> str:
    sizes = _sorted_size_labels(set(parsed.get("sizes") or set()))
    sizes_text = ", ".join(sizes) if sizes else "none"
    color_text = str(parsed.get("color") or "Unknown")
    price_text = str(parsed.get("price") or "")
    type_text = str(parsed.get("type") or "")
    return "\n".join(
        [
            str(parsed.get("name") or ""),
            type_text,
            f"Color: {color_text}",
            f"Price: {price_text}",
            f"Available sizes: {sizes_text}",
        ]
    )


def _extract_catalog_cards_from_snippets(snippets: list[str]) -> list[str]:
    cards: list[str] = []
    seen: set[str] = set()
    for snippet in snippets:
        parsed = _parse_inventory_snippet(snippet)
        if not parsed:
            continue
        dedupe_key = str(parsed.get("name"))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        cards.append(_format_catalog_card(parsed))
    return cards


def _snippet_has_size(snippet: str, size_label: str) -> bool:
    parsed = _parse_inventory_snippet(snippet)
    if not parsed:
        return False
    return size_label in set(parsed.get("sizes") or set())


def _is_size_only_query(entities: object) -> bool:
    return bool(getattr(entities, "size", None)) and not bool(getattr(entities, "brand", None)) and not bool(getattr(entities, "model_hint", []))


def _is_brand_and_size_only_query(entities: object) -> bool:
    return bool(getattr(entities, "size", None)) and bool(getattr(entities, "brand", None)) and not bool(getattr(entities, "model_hint", []))


def _normalize_decimal_size_text(text: str) -> str:
    """Convert decimal comma size notation (e.g. 42,5) to 42.5."""
    return re.sub(r"(?<=\d)\s*,\s*(?=\d)", ".", text)


def _is_pure_size_followup(text: str) -> bool:
    """
    Return True when the entire user message is *only* a size expression
    with no brand or other context.

    Matches both single sizes ("42", "size 42", "42.5") and bare ranges
    ("41-42", "41 to 42", "41/42", "size 41-42") so that a range typed after
    a brand search also inherits the remembered brand.
    """
    lowered = text.lower().strip()
    _SN = r"(?:3[5-9]|4[0-9]|50)(?:\.(?:5|3))?"
    # Single bare size
    if re.fullmatch(rf"(?:size\s*)?{_SN}", lowered):
        return True
    # Bare size range
    if re.fullmatch(rf"(?:size\s*)?{_SN}\s*(?:-|to|/)\s*{_SN}", lowered):
        return True
    return False


def _score_structured_match(request: dict[str, str | None], candidate: dict[str, object]) -> int:
    brand = str(request.get("brand") or "")
    model = str(request.get("model") or "")
    size = str(request.get("size") or "")

    candidate_brand = str(candidate.get("brand") or "")
    candidate_model = str(candidate.get("model") or "")
    candidate_name = str(candidate.get("name") or "").lower()
    candidate_sizes = set(candidate.get("sizes") or set())

    score = 0

    if brand:
        if candidate_brand != brand:
            return -1
        score += 40

    if model:
        model_tokens = re.findall(r"[a-z0-9]+", model)
        if not all(token in candidate_model or token in candidate_name for token in model_tokens):
            return -1
        score += 50

    if size:
        if size not in candidate_sizes:
            return -1
        score += 30

    if score == 0:
        return -1
    return score


def _resolve_cart_item_from_snippets(user_text: str, snippets: list[str]) -> str | None:
    request = _extract_structured_request(user_text)

    if not request.get("brand") and not request.get("model") and not request.get("size"):
        lowered = user_text.lower()
        if any(token in lowered for token in ["it", "this one", "that one", "first one"]):
            for snippet in snippets:
                parsed = _parse_inventory_snippet(snippet)
                if parsed:
                    return str(parsed.get("label"))

    best_label: str | None = None
    best_score = -1
    for snippet in snippets:
        parsed = _parse_inventory_snippet(snippet)
        if not parsed:
            continue
        score = _score_structured_match(request, parsed)
        if score > best_score:
            best_score = score
            best_label = str(parsed.get("label"))

    if best_score < 0:
        return None
    return best_label


def _compute_cart_totals(cart_items: list[str]) -> tuple[float, float, float]:
    subtotal = 0.0
    for item in cart_items:
        match = re.search(r"\$(\d+(?:\.\d+)?)", item)
        if match:
            subtotal += float(match.group(1))
    delivery_fee = 4.0
    total = subtotal + delivery_fee
    return subtotal, delivery_fee, total


def _is_discovery_query(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in ["what do you have", "show me", "list", "catalog"])


def _format_choice_prompt(matches: list[str], intro: str) -> str:
    lines = [intro, ""]
    for idx, item in enumerate(matches, start=1):
        lines.append(f"{idx}. {item}")
    lines.append("")
    lines.append("Which one would you like to add to your cart?")
    return "\n".join(lines)


def _pick_candidate_from_choice(user_text: str, candidates: list[str]) -> str | None:
    lowered = user_text.lower().strip()
    index_match = re.search(r"\b(\d{1,2})\b", lowered)
    if index_match:
        idx = int(index_match.group(1))
        if 1 <= idx <= len(candidates):
            return candidates[idx - 1]

    query_tokens = {token for token in re.findall(r"[a-z0-9]+", lowered) if token not in {"i", "want", "the", "one", "add", "to", "cart"}}
    if not query_tokens:
        return None

    best_item: str | None = None
    best_overlap = 0
    for candidate in candidates:
        candidate_tokens = set(re.findall(r"[a-z0-9]+", candidate.lower()))
        overlap = len(query_tokens.intersection(candidate_tokens))
        if overlap > best_overlap:
            best_overlap = overlap
            best_item = candidate

    if best_overlap == 0:
        return None
    return best_item


def _is_checkout_intent(text: str) -> bool:
    lowered = text.lower()
    return any(
        token in lowered
        for token in [
            "i need them",
            "i need both",
            "add them to bag",
            "add to bag",
            "checkout",
            "i need to checkout",
            "need to checkout",
            "place order",
            "i want to order",
            "i need to order",
        ]
    )


def _is_add_to_bag_phrase(text: str) -> bool:
    lowered = text.lower()
    return any(
        token in lowered
        for token in [
            "please add",
            "add to bag",
            "add them to bag",
            "add to cart",
            "add them to cart",
            "please add to bag",
            "please add to cart",
        ]
    )


def _is_need_or_want_item_phrase(text: str) -> bool:
    lowered = text.lower().strip()
    return any(
        lowered.startswith(prefix)
        for prefix in [
            "i need this one",
            "i need it",
            "i need shoes",
            "i need ",
            "i need both",
            "i want this one",
            "i want it",
            "i want shoes",
            "i want ",
            "i want both",
        ]
    )


def _extract_readable_items_from_snippets(snippets: list[str]) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for snippet in snippets:
        parsed = _parse_inventory_snippet(snippet)
        if not parsed:
            continue
        label = str(parsed.get("label"))
        if label in seen:
            continue
        seen.add(label)
        items.append(label)
    return items


class TelegramBotRunner:
    def __init__(self, token: str, poll_timeout: int = 20) -> None:
        self.token = token
        self.poll_timeout = poll_timeout
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self.rag_service = RagService()
        self.chat_states: Dict[int, ChatState] = {}
        LOG_DIR.mkdir(parents=True, exist_ok=True)

    def _call(self, method: str, payload: Dict[str, object]) -> Dict[str, object]:
        data = urllib.parse.urlencode(payload).encode("utf-8")
        request = urllib.request.Request(
            url=f"{self.base_url}/{method}",
            data=data,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read().decode("utf-8")
        decoded = json.loads(body)
        if not decoded.get("ok"):
            raise RuntimeError(f"Telegram API error on {method}: {decoded}")
        return decoded

    def get_updates(self, offset: Optional[int] = None) -> list[dict]:
        payload: Dict[str, object] = {"timeout": self.poll_timeout}
        if offset is not None:
            payload["offset"] = offset
        result = self._call("getUpdates", payload)
        return result.get("result", [])

    def send_message(self, chat_id: int, text: str) -> None:
        self._call("sendMessage", {"chat_id": chat_id, "text": text})

    def _append_chat_log(self, chat_id: int, role: str, text: str) -> None:
        log_path = LOG_DIR / f"{chat_id}.jsonl"
        entry = {
            "timestamp": int(time.time()),
            "chat_id": chat_id,
            "role": role,
            "text": text,
        }
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _get_chat_state(self, chat_id: int) -> ChatState:
        state = self.chat_states.get(chat_id)
        if state is None:
            state = ChatState()
            self.chat_states[chat_id] = state
        else:
            now = int(time.time())
            if now - state.last_seen_at > 7 * 24 * 60 * 60:
                state = ChatState()
                self.chat_states[chat_id] = state
                # Fresh session: clear volatile context but keep saved location profile.
                reset_session_preserve_profile(str(chat_id))
        state.last_seen_at = int(time.time())
        return state

    def _retrieve_strict_candidates(self, user_text: str, chat_state: ChatState, top_k: int = 20) -> list[str]:
        color_hint = extract_color(user_text)
        gender_hint = extract_gender(user_text)
        return self.rag_service.retriever.retrieve_inventory_snippets(
            user_message=user_text,
            intent="search_product",
            top_k=top_k,
            color_hint=color_hint,
            gender_hint=gender_hint,
        )

    def _parse_cart_items_for_db(self, bag: list[str]) -> list[dict[str, object]]:
        """
        Parse cart items into structured format for order_items table.
        Each bag item is a formatted label like "Adidas Run Falcon 5 (Running Shoes) $70".

        Returns list with entries: {product_id, product_name, quantity, size, price}
        """
        items_for_db: list[dict[str, object]] = []
        for item_label in bag:
            # Parse the item label to extract name, size, and price
            # Format: "Brand Model Type $price"
            price_match = re.search(r"\$(\d+(?:\.\d+)?)", item_label)
            price = float(price_match.group(1)) if price_match else 0.0

            # Remove price from label to get product name
            product_name = re.sub(r"\s*\$\d+(?:\.\d+)?", "", item_label).strip()

            # Extract size if present (looking for patterns like "size 42" or just numbers)
            size = ""
            size_match = re.search(r"\bsize\s*(3[5-9]|4[0-9]|50)(?:\.(5|3))?\b", item_label, re.IGNORECASE)
            if size_match:
                size = size_match.group(1)
                if size_match.group(2):
                    size = f"{size}.{size_match.group(2)}"

            items_for_db.append(
                {
                    "product_id": None,  # Will be NULL in DB if not available
                    "product_name": product_name,
                    "quantity": 1,
                    "size": size,
                    "price": price,
                }
            )

        return items_for_db

    def _start_order_collection(self, chat_id: int, state: ChatState, item_request: str) -> str:
        state.conversation_state = STATE_CHECKOUT
        state.order_stage = _ORDER_STAGE_SEQUENCE[0][0]
        # Checkout should operate on current cart contents when available.
        if state.bag:
            item_request = "\n".join(f"• {item}" for item in state.bag)

        state.order_data = {
            "items": item_request,
        }

        # Pre-fill previously saved delivery location if the user ordered before.
        saved = load_delivery_profile(str(chat_id))
        for key in ["governorate", "district", "village", "address_details"]:
            if key in saved:
                state.order_data[key] = saved[key]

        return _ORDER_STAGE_SEQUENCE[0][1]

    def _get_next_stage_prompt(self, current_stage: str) -> tuple[str | None, str | None]:
        for idx, (stage_name, _) in enumerate(_ORDER_STAGE_SEQUENCE):
            if stage_name != current_stage:
                continue
            if idx + 1 >= len(_ORDER_STAGE_SEQUENCE):
                return None, None
            next_stage, prompt = _ORDER_STAGE_SEQUENCE[idx + 1]
            return next_stage, prompt
        return None, None

    def _format_admin_order_message(self, chat_id: int, order_data: Dict[str, str]) -> str:
        return (
            "NEW ORDER - FLEX FITS\n"
            f"Chat ID: {chat_id}\n"
            f"Customer Name: {order_data.get('name', '')}\n"
            f"Phone: {order_data.get('phone', '')}\n"
            f"Email: {order_data.get('email', '')}\n"
            f"Order Request: {order_data.get('items', '')}\n"
            f"Governorate: {order_data.get('governorate', '')}\n"
            f"District: {order_data.get('district', '')}\n"
            f"Village: {order_data.get('village', '')}\n"
            f"Address: {order_data.get('address_details', '')}"
        )

    def _send_order_to_admin(self, chat_id: int, order_data: Dict[str, str]) -> None:
        admin_chat_id = int(os.getenv("FLEX_ADMIN_TELEGRAM_CHAT_ID", "5665846174").strip())
        message = self._format_admin_order_message(chat_id, order_data)
        self._call("sendMessage", {"chat_id": admin_chat_id, "text": message})

    def _format_order_summary(self, state: ChatState) -> str:
        subtotal, delivery_fee, total = _compute_cart_totals(state.bag)
        lines = ["Order Summary", "", "Items:"]
        for idx, item in enumerate(state.bag, 1):
            lines.append(f"{idx}. {item}")
        lines.extend(
            [
                "",
                f"Subtotal: ${subtotal:.2f}",
                f"Delivery: ${delivery_fee:.2f}",
                f"Total: ${total:.2f}",
                "",
                "Confirm order? (yes/no)",
            ]
        )
        return "\n".join(lines)

    def _handle_order_collection(self, chat_id: int, state: ChatState, user_text: str) -> str | None:
        if not state.order_stage:
            return None

        stage = state.order_stage
        value = user_text.strip()

        if stage == "bag_confirm":
            _yes = {"yes", "y", "ok", "sure", "please", "yep", "yeah", "add it", "do it"}
            _no = {
                "no", "n", "cancel", "nope", "no thanks", "nah",
                "i don't need it", "i dont need it",
                "i don't want it", "i dont want it",
                "remove it", "not this one",
            }
            val_lower = value.lower()
            if val_lower in _yes or val_lower.startswith("yes"):
                item_text = state.order_data.get("items", "")
                if item_text and item_text not in state.bag:
                    state.bag.append(item_text)
                state.order_stage = None
                state.order_data = {}
                state.conversation_state = STATE_CART
                return f"Done. I added to your cart: {item_text}. Say 'finish my order' when you want checkout."
            if val_lower in _no or val_lower.startswith("no "):
                state.order_stage = None
                state.order_data = {}
                state.conversation_state = STATE_BROWSING
                return "Okay, I did not add it to your bag."
            return "Please reply yes or no."

        if stage == "confirm_order":
            val = value.lower()
            if val in {"yes", "y", "confirm", "ok", "okay", "sure"}:
                # Idempotency guard: if order has already been created, return existing reference
                if "_confirmed_order_id" in state.order_data:
                    existing_order_id = state.order_data["_confirmed_order_id"]
                    state.bag = []
                    state.order_stage = None
                    state.order_data = {}
                    state.pending_cart_candidates = []
                    state.last_results = []
                    state.conversation_state = STATE_IDLE
                    reset_session_preserve_profile(str(chat_id))
                    return f"Your order #{existing_order_id} was already confirmed. Delivery will arrive within 2-3 days."

                # Compute totals before persisting
                subtotal, delivery_fee, total = _compute_cart_totals(state.bag)

                # Prepare order data for Supabase
                order_date = time.strftime("%Y-%m-%d")
                customer_name = state.order_data.get("name", "").strip()
                customer_email = state.order_data.get("email", "").strip()
                customer_phone = state.order_data.get("phone", "").strip()
                governorate = state.order_data.get("governorate", "").strip()
                district = state.order_data.get("district", "").strip()
                village = state.order_data.get("village", "").strip()
                address_details = state.order_data.get("address_details", "").strip()

                # Try to persist to Supabase
                supabase_client = get_supabase_client()
                order_id = None
                supabase_success = False

                if supabase_client:
                    order_result = supabase_client.create_order(
                        customer_name=customer_name,
                        customer_email=customer_email,
                        customer_phone=customer_phone,
                        governorate=governorate,
                        district=district,
                        village=village,
                        address_details=address_details,
                        total=total,
                        order_date=order_date,
                    )

                    if order_result.success and order_result.order_id:
                        order_id = order_result.order_id
                        # Store idempotency token to prevent duplicate confirms
                        state.order_data["_confirmed_order_id"] = str(order_id)

                        # Create order items
                        items_for_db = self._parse_cart_items_for_db(state.bag)
                        items_result = supabase_client.create_order_items(
                            order_id=order_id,
                            items=items_for_db,
                        )
                        supabase_success = items_result.success
                    else:
                        # DB write failed; keep state and ask user to retry
                        self._append_chat_log(chat_id, "user", user_text)
                        reply = f"I had trouble saving your order to our database. Let's try again.\nReply yes to confirm: {order_result.error_message}"
                        self._append_chat_log(chat_id, "assistant", reply)
                        self.send_message(chat_id, reply)
                        return

                # Log the order to Telegram admin as secondary notification
                try:
                    self._send_order_to_admin(chat_id, state.order_data)
                except Exception as e:
                    # Log but don't fail if admin notification fails
                    print(f"Warning: Failed to send admin notification: {e}")

                # Persist location profile for future sessions
                save_delivery_profile(
                    str(chat_id),
                    {
                        "governorate": governorate,
                        "district": district,
                        "village": village,
                        "address_details": address_details,
                    },
                )

                # Order is finalized: clear cart and volatile conversation state
                state.bag = []
                state.order_stage = None
                state.order_data = {}
                state.pending_cart_candidates = []
                state.last_results = []
                state.conversation_state = STATE_IDLE
                reset_session_preserve_profile(str(chat_id))

                order_ref = f"#{order_id}" if order_id else "(reference pending)"
                return f"Your order {order_ref} has been confirmed. Delivery will arrive within 2-3 days."

            if val in {"no", "n", "cancel", "not now"}:
                state.order_stage = None
                state.order_data = {}
                state.conversation_state = STATE_CART if state.bag else STATE_IDLE
                return "No problem. I cancelled checkout and kept your cart unchanged."

            return "Please reply yes or no to confirm your order."

        state.order_data[stage] = value
        next_stage, prompt = self._get_next_stage_prompt(stage)

        if next_stage is None:
            # Collected all checkout fields: show receipt and ask explicit confirmation.
            state.order_stage = "confirm_order"
            state.conversation_state = STATE_ORDER_CONFIRMATION
            return self._format_order_summary(state)

        state.order_stage = next_stage
        return prompt

    def process_update(self, update: dict) -> None:
        message = update.get("message") or update.get("edited_message")
        if not message:
            return

        chat = message.get("chat", {})
        chat_id = chat.get("id")
        text = message.get("text")

        if not isinstance(chat_id, int) or not isinstance(text, str) or not text.strip():
            return

        user_text = text.strip()
        user_text = _normalize_decimal_size_text(user_text)
        intent = detect_intent(user_text)

        chat_state = self._get_chat_state(chat_id)
        memory_key = str(chat_id)

        # If we asked user to choose from multiple matches, consume selection first.
        if chat_state.pending_cart_candidates:
            chosen = _pick_candidate_from_choice(user_text, chat_state.pending_cart_candidates)
            if chosen is None:
                chat_state.conversation_state = STATE_PRODUCT_SELECTION
                reply = _format_choice_prompt(
                    chat_state.pending_cart_candidates,
                    f"I found {len(chat_state.pending_cart_candidates)} matching products:",
                )
            else:
                chat_state.pending_cart_candidates = []
                chat_state.order_stage = "bag_confirm"
                chat_state.order_data = {"items": chosen}
                chat_state.conversation_state = STATE_PRODUCT_SELECTION
                reply = f"Do you want me to add this to your cart?\n- {chosen}\nReply yes or no."

            self._append_chat_log(chat_id, "user", user_text)
            self._append_chat_log(chat_id, "assistant", reply)
            self.send_message(chat_id, reply)
            return

        # "I need them all" means add all currently shown list items to bag.
        if user_text.lower().strip() in {"i need them all", "them all", "all of them"}:
            if has_stored_search(memory_key):
                ctx = get_stored_context(memory_key)
                restore_intent = str(ctx.get("last_intent") or "search_product")
                restore_msg = str(ctx.get("last_message") or user_text)
                result = self.rag_service.generate_support_reply(
                    user_message=restore_msg,
                    intent=restore_intent,
                    state_entities=chat_state.last_entities,
                    color_hint=ctx.get("last_color"),
                    gender_hint=ctx.get("last_gender"),
                    top_k=10,
                )
                items = _extract_readable_items_from_snippets(result.get("context_chunks", []))
                if items:
                    for item in items:
                        if item not in chat_state.bag:
                            chat_state.bag.append(item)
                    chat_state.conversation_state = STATE_CART
                    reply = "Done. I added all shown items to your bag."
                else:
                    reply = "I could not find items from your previous list."
            else:
                reply = "Please ask for products first, then I can add them all to your bag."

            self._append_chat_log(chat_id, "user", user_text)
            self._append_chat_log(chat_id, "assistant", reply)
            self.send_message(chat_id, reply)
            return

        # ── Layer 1: Entity Extraction ──────────────────────────────────────────
        # Parse brand / size / gender / color / model_hint from the raw text.
        # query_mode tells the Decision Engine how to respond:
        #   "add_to_cart" → user explicitly wants to add something to the cart
        #   "specific"    → has size or specific model; trigger confirm/choose flow
        #   "browse"      → wants to see a catalog; show the full list, no cart prompt
        entities = extract_entities(user_text)
        search_text = user_text

        # Conversation memory: if user sends only a size (e.g., "42.5") or a size
        # range ("41-42") with no brand context, reuse the last brand from memory.
        if entities.size and not entities.brand and not entities.model_hint and _is_pure_size_followup(user_text):
            ctx = get_stored_context(memory_key)
            remembered_brand = ctx.get("last_brand")
            if isinstance(remembered_brand, str) and remembered_brand.strip():
                entities.brand = remembered_brand.strip().lower()
                # Build an augmented search text so the retriever picks up the
                # brand AND the correct size expression (single or range).
                if entities.size_min and entities.size_max and entities.size_min != entities.size_max:
                    search_text = f"{entities.brand} size {entities.size_min}-{entities.size_max}"
                else:
                    search_text = f"{entities.brand} size {entities.size}"

        # ── Layer 4 (ADD TO CART): explicit add-to-cart language ─────────────
        # Also catches the existing _is_add_to_bag_phrase patterns.
        if (
            (_is_add_to_bag_phrase(user_text) or entities.query_mode == "add_to_cart" or intent == "create_order")
            and not _is_checkout_intent(user_text)
            and intent not in {"intent_to_order", "finish_order"}
        ):
            # Prefer last shown results for pronoun requests like "I need it" / "add it to cart".
            strict_items: list[str] = []
            if chat_state.last_results and any(
                token in user_text.lower()
                for token in ["it", "this", "that", "one", "first", "second", "third", "add"]
            ):
                strict_items = list(chat_state.last_results)
            else:
                strict_snippets = self._retrieve_strict_candidates(search_text, chat_state, top_k=20)
                strict_items = _extract_readable_items_from_snippets(strict_snippets)

            if not strict_items:
                reply = "I could not find a matching product. Please include brand, model, size, or color."
            elif len(strict_items) == 1:
                chat_state.order_stage = "bag_confirm"
                chat_state.order_data = {"items": strict_items[0]}
                chat_state.conversation_state = STATE_PRODUCT_SELECTION
                reply = f"Do you want me to add {strict_items[0]} to your cart? Reply yes or no."
            else:
                chat_state.pending_cart_candidates = strict_items
                chat_state.conversation_state = STATE_PRODUCT_SELECTION
                reply = _format_choice_prompt(
                    strict_items,
                    f"I found {len(strict_items)} matching products:",
                )

            self._append_chat_log(chat_id, "user", user_text)
            self._append_chat_log(chat_id, "assistant", reply)
            self.send_message(chat_id, reply)
            return

        # Active order FSM flow has top priority once started.
        order_reply = self._handle_order_collection(chat_id, chat_state, user_text)
        if order_reply is not None:
            self._append_chat_log(chat_id, "user", user_text)
            self._append_chat_log(chat_id, "assistant", order_reply)
            self.send_message(chat_id, order_reply)
            return

        # ── Handle cancel confirmation flow ─────────────────────────────────
        if chat_state.cancel_confirmation_pending:
            is_confirm = any(
                token in user_text.lower()
                for token in ["yes", "sure", "confirm", "i'm sure", "im sure", "okay", "ok", "go ahead"]
            )
            is_reject = any(
                token in user_text.lower()
                for token in ["no", "keep", "don't", "dont", "don't cancel", "nevermind", "cancel that"]
            )

            if is_confirm:
                chat_state.bag = []
                chat_state.cancel_confirmation_pending = False
                chat_state.conversation_state = STATE_IDLE
                reply = "Your order has been cancelled. How can I help you today?"
            elif is_reject:
                chat_state.cancel_confirmation_pending = False
                chat_state.conversation_state = STATE_CART if chat_state.bag else STATE_IDLE
                bag_str = "\n".join(f"• {item}" for item in chat_state.bag)
                reply = f"Good! Your cart still has these items:\n{bag_str}\nSay 'finish my order' when you're ready to proceed."
            else:
                reply = "I didn't understand. Do you want to cancel? Just say 'yes' or 'no'."

            self._append_chat_log(chat_id, "user", user_text)
            self._append_chat_log(chat_id, "assistant", reply)
            self.send_message(chat_id, reply)
            return

        # ── Handle cancel_order intent ──────────────────────────────────────
        if intent == "cancel_order":
            if not chat_state.bag:
                reply = "Your cart is already empty. How can I help?"
            else:
                chat_state.cancel_confirmation_pending = True
                reply = "Are you sure you want to cancel your order? (yes/no)"

            self._append_chat_log(chat_id, "user", user_text)
            self._append_chat_log(chat_id, "assistant", reply)
            self.send_message(chat_id, reply)
            return

        # ── Handle intent_to_order: enter CHECKOUT state explicitly ─────────
        if intent == "intent_to_order":
            # If user says "i need to order 2" after seeing a list, treat the
            # number as selection from last shown results and add that item.
            if not chat_state.bag and chat_state.last_results:
                index_match = re.search(r"\b(\d{1,2})\b", user_text.lower())
                if index_match:
                    idx = int(index_match.group(1))
                    if 1 <= idx <= len(chat_state.last_results):
                        selected_item = chat_state.last_results[idx - 1]
                        if selected_item not in chat_state.bag:
                            chat_state.bag.append(selected_item)

            chat_state.conversation_state = STATE_CHECKOUT
            reply = self._start_order_collection(chat_id, chat_state, "")
            self._append_chat_log(chat_id, "user", user_text)
            self._append_chat_log(chat_id, "assistant", reply)
            self.send_message(chat_id, reply)
            return

        # ── Handle finish_order intent ──────────────────────────────────────
        if intent == "finish_order":
            if not chat_state.bag:
                reply = "Your cart is empty. Let's fill it with your favorite sneakers first."
            else:
                chat_state.conversation_state = STATE_CHECKOUT
                reply = self._start_order_collection(chat_id, chat_state, "")

            self._append_chat_log(chat_id, "user", user_text)
            self._append_chat_log(chat_id, "assistant", reply)
            self.send_message(chat_id, reply)
            return

        # ── Handle show_cart intent ─────────────────────────────────────────
        # Rule 5: "show me my cart" → list ONLY the items in the current cart.
        if intent == "show_cart":
            if not chat_state.bag:
                chat_state.conversation_state = STATE_IDLE
                reply = "Your cart is empty. Let's fill it with your favorite original sneakers."
            else:
                lines = ["Your cart currently contains:"]
                for idx, item in enumerate(chat_state.bag, 1):
                    lines.append(f"{idx}. {item}")
                subtotal, _, _ = _compute_cart_totals(chat_state.bag)
                lines.append("")
                lines.append(f"Subtotal: ${subtotal:.2f}")
                chat_state.conversation_state = STATE_CART
                reply = "\n".join(lines)

            self._append_chat_log(chat_id, "user", user_text)
            self._append_chat_log(chat_id, "assistant", reply)
            self.send_message(chat_id, reply)
            return

        # ── Handle remove_last intent ──────────────────────────────────────
        # Rule 4: "No I don't need it" → remove ONLY the last added item.
        # This NEVER clears the whole cart; use cancel_order for that.
        if intent == "remove_last":
            if not chat_state.bag:
                chat_state.conversation_state = STATE_IDLE
                reply = "Your cart is already empty, nothing to remove."
            else:
                removed = chat_state.bag.pop()  # removes the last item only
                if chat_state.bag:
                    lines = [f"Removed '{removed}' from your cart.", "", "Your cart now contains:"]
                    for idx, item in enumerate(chat_state.bag, 1):
                        lines.append(f"{idx}. {item}")
                    chat_state.conversation_state = STATE_CART
                    reply = "\n".join(lines)
                else:
                    chat_state.conversation_state = STATE_IDLE
                    reply = f"Removed '{removed}' from your cart. Your cart is now empty."

            self._append_chat_log(chat_id, "user", user_text)
            self._append_chat_log(chat_id, "assistant", reply)
            self.send_message(chat_id, reply)
            return

        # Trigger checkout collection only from explicit checkout language.
        if _is_checkout_intent(user_text):
            chat_state.conversation_state = STATE_CHECKOUT
            reply = self._start_order_collection(chat_id, chat_state, user_text)
            self._append_chat_log(chat_id, "user", user_text)
            self._append_chat_log(chat_id, "assistant", reply)
            self.send_message(chat_id, reply)
            return

        # ── Layer 3: Decision Engine — BROWSE ───────────────────────────────
        # When query_mode == "browse" AND the user mentioned a brand (or gender/
        # color with no size), show the full matching catalog as a numbered list.
        # This satisfies Rule 1 (show ALL results) and Rule 3 (browsing never
        # auto-adds to cart).
        if intent == "search_product" and entities.query_mode == "browse" and (
            entities.brand or entities.gender or entities.color
        ):
            chat_state.conversation_state = STATE_BROWSING
            browse_snippets = self.rag_service.retriever.retrieve_inventory_snippets(
                user_message=search_text,
                intent="search_product",
                top_k=50,          # effectively no cap — show every matching item
                color_hint=entities.color,
                gender_hint=entities.gender,
                return_all=True,   # bypass score cutoff for catalog display
            )
            browse_items = _extract_readable_items_from_snippets(browse_snippets)
            browse_cards = _extract_catalog_cards_from_snippets(browse_snippets)

            brand_label = entities.brand.title() if entities.brand else ""
            color_label = f" {entities.color}" if entities.color else ""
            gender_label = f" for {entities.gender}" if entities.gender else ""
            catalog_title = f"{brand_label}{color_label}{gender_label}".strip()

            if not browse_items:
                reply = (
                    f"I couldn't find any {catalog_title} shoes in stock right now. "
                    "Check back soon or ask about another brand."
                )
            else:
                lines = [f"Here's what we have in {catalog_title}:" if catalog_title else "Here's what we have:", ""]
                for idx, item in enumerate(browse_cards, 1):
                    card_lines = item.splitlines()
                    if not card_lines:
                        continue
                    lines.append(f"{idx}. {card_lines[0]}")
                    lines.extend(card_lines[1:])
                    lines.append("")
                lines.append("Tell me which one you'd like and I can add it to your cart.")
                reply = "\n".join(lines)

            chat_state.last_results = browse_items

            # Save search context so "show all" / "add them all" works later
            if browse_snippets:
                update_search_memory(
                    chat_id=memory_key,
                    intent="search_product",
                    results=browse_snippets,
                    color=entities.color,
                    gender=entities.gender,
                    brand=entities.brand,
                    original_message=user_text,
                )

            self._append_chat_log(chat_id, "user", user_text)
            self._append_chat_log(chat_id, "assistant", reply)
            self.send_message(chat_id, reply)
            return

        # ── Layer 3: Decision Engine — SPECIFIC ─────────────────────────────
        # query_mode == "specific" means constrained search (size/color/model).
        # In this mode we SHOW filtered results (no auto-add) and wait for an
        # explicit add-to-cart request.
        if intent == "search_product" and entities.query_mode == "specific":
            chat_state.conversation_state = STATE_BROWSING
            strict_snippets = self._retrieve_strict_candidates(search_text, chat_state, top_k=20)
            strict_items = _extract_readable_items_from_snippets(strict_snippets)
            strict_cards = _extract_catalog_cards_from_snippets(strict_snippets)

            # Determine whether the user's requested size (or range) is directly
            # satisfied by the returned snippets.
            # • Range query → "exact" means at least one snippet contains a size
            #   that falls within [size_min, size_max].
            # • Single-size query → "exact" means the specific size label appears
            #   in at least one snippet.
            is_range_query = bool(
                entities.size_min
                and entities.size_max
                and entities.size_min != entities.size_max
            )
            if is_range_query:
                try:
                    _rmin = float(entities.size_min)  # type: ignore[arg-type]
                    _rmax = float(entities.size_max)  # type: ignore[arg-type]
                    has_exact_size = bool(strict_snippets) and any(
                        any(
                            _rmin <= float(s) <= _rmax
                            for s in re.findall(r"\d+(?:\.\d+)?", snip.split("sizes:")[-1])
                            if 35.0 <= float(s) <= 50.0
                        )
                        for snip in strict_snippets
                    )
                except (ValueError, TypeError):
                    has_exact_size = bool(strict_snippets)
            else:
                has_exact_size = bool(entities.size) and any(
                    _snippet_has_size(snippet, str(entities.size))
                    for snippet in strict_snippets
                )

            # Build a human-readable size label used in OOS / close-size messages.
            if is_range_query:
                size_label = f"sizes {entities.size_min}\u2013{entities.size_max}"
            elif entities.size:
                size_label = f"size {entities.size}"
            else:
                size_label = "that size"

            if entities.size and not has_exact_size and _is_size_only_query(entities):
                chat_state.last_results = []
                reply = f"Sorry, we currently don't have sneakers available in {size_label}."
                self._append_chat_log(chat_id, "user", user_text)
                self._append_chat_log(chat_id, "assistant", reply)
                self.send_message(chat_id, reply)
                return

            if not strict_items:
                chat_state.last_results = []
                if entities.size and entities.brand and _is_brand_and_size_only_query(entities):
                    reply = f"Sorry, {entities.brand.title()} {size_label} is currently out of stock."
                elif entities.size and _is_size_only_query(entities):
                    reply = f"Sorry, we currently don't have sneakers available in {size_label}."
                else:
                    reply = "I could not find matching shoes. Try adding brand, model, size, or color."
            else:
                brand_name = entities.brand.title() if entities.brand else ""
                if entities.size and not has_exact_size:
                    if brand_name and _is_brand_and_size_only_query(entities):
                        intro = f"{brand_name} {size_label} is currently out of stock."
                    elif brand_name:
                        intro = f"Hmm... I couldn't find {brand_name} exactly in {size_label}."
                    else:
                        intro = f"Hmm... I couldn't find an exact match in {size_label}."
                    lines = [intro, "", "But I found these close sizes available:", ""]
                else:
                    intro = f"I found {len(strict_items)} {brand_name} matches:".strip() if brand_name else f"I found {len(strict_items)} matching products:"
                    lines = [intro, ""]

                for idx, item in enumerate(strict_cards, 1):
                    card_lines = item.splitlines()
                    if not card_lines:
                        continue
                    lines.append(f"{idx}. {card_lines[0]}")
                    lines.extend(card_lines[1:])
                    lines.append("")
                lines.append("If you want one, say 'add it to cart' or tell me the number.")
                reply = "\n".join(lines)
                chat_state.last_results = strict_items

            if strict_snippets:
                update_search_memory(
                    chat_id=memory_key,
                    intent="search_product",
                    results=strict_snippets,
                    color=entities.color,
                    gender=entities.gender,
                    brand=entities.brand,
                    original_message=user_text,
                )

            self._append_chat_log(chat_id, "user", user_text)
            self._append_chat_log(chat_id, "assistant", reply)
            self.send_message(chat_id, reply)
            return

        # ── show_all: replay last search with expanded top_k ──────────────────
        if intent == "show_all":
            if not has_stored_search(memory_key):
                self._append_chat_log(chat_id, "user", user_text)
                self._append_chat_log(chat_id, "assistant", "Please tell me what you're looking for first.")
                self.send_message(chat_id, "Please tell me what you're looking for first.")
                return
            ctx = get_stored_context(memory_key)
            restore_intent = str(ctx.get("last_intent") or "search_product")
            restore_msg = str(ctx.get("last_message") or user_text)
            result = self.rag_service.generate_support_reply(
                user_message=restore_msg,
                intent=restore_intent,
                state_entities=chat_state.last_entities,
                color_hint=ctx.get("last_color"),
                gender_hint=ctx.get("last_gender"),
                top_k=10,
            )
            base_reply = str(result.get("reply", "")) or "I can help with shoes, sizing, and delivery in Lebanon."
            intro_only = base_reply.splitlines()[0] if base_reply else "Sure. Here are all the options I found."
            reply = generate_support_response(
                intro_only,
                intent="show_all",
                highlights=result.get("context_chunks", []),
                show_all=True,
            )
            chat_state.last_results = _extract_readable_items_from_snippets(result.get("context_chunks", []))
            self._append_chat_log(chat_id, "user", user_text)
            self._append_chat_log(chat_id, "assistant", reply)
            self.send_message(chat_id, reply)
            return

        # Fast XML-backed responses for common conversational intents.
        if intent in {
            "thank_you",
            "goodbye",
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
            "cancel_order",
            "intent_to_order",
            "finish_order",
        }:
            xml_reply = get_intent_loader().get_response(intent).strip()
            if xml_reply:
                self._append_chat_log(chat_id, "user", user_text)
                self._append_chat_log(chat_id, "assistant", xml_reply)
                self.send_message(chat_id, xml_reply)
                return

        # ── regular flow: extract filters, inherit prior gender if absent ──────
        color_hint = extract_color(user_text)
        gender_hint = extract_gender(user_text)

        if gender_hint is None and intent in _SEARCH_INTENTS:
            stored = get_stored_context(memory_key)
            gender_hint = stored.get("last_gender")

        result = self.rag_service.generate_support_reply(
            user_message=user_text,
            intent=intent,
            state_entities=chat_state.last_entities,
            color_hint=color_hint,
            gender_hint=gender_hint,
        )
        reply = str(result.get("reply", "")) or "I can help with shoes, sizing, and delivery in Lebanon."

        if intent in _SEARCH_INTENTS:
            chunks = result.get("context_chunks", [])
            update_search_memory(
                chat_id=memory_key,
                intent=intent,
                results=chunks,
                color=color_hint,
                gender=gender_hint,
                brand=entities.brand,
                original_message=user_text,
            )

        self._append_chat_log(chat_id, "user", user_text)
        self._append_chat_log(chat_id, "assistant", reply)
        self.send_message(chat_id, reply)

    def run_forever(self) -> None:
        offset: Optional[int] = None
        while True:
            try:
                updates = self.get_updates(offset=offset)
                for update in updates:
                    update_id = update.get("update_id")
                    if isinstance(update_id, int):
                        offset = update_id + 1
                    self.process_update(update)
            except KeyboardInterrupt:
                print("Telegram bot stopped by user.")
                break
            except (TimeoutError, socket.timeout, urllib.error.URLError) as error:
                # Transient network/DNS issues are expected in long polling. Retry.
                print(f"Network issue: {error}. Retrying in 3s...")
                time.sleep(3)
            except Exception as error:  # noqa: BLE001
                print(f"Unexpected error: {error}. Retrying in 3s...")
                time.sleep(3)


def load_bot_token() -> str:
    token = os.getenv("FLEX_TELEGRAM_BOT_TOKEN", "").strip()
    if token:
        return token
    raise RuntimeError("Missing FLEX_TELEGRAM_BOT_TOKEN environment variable.")


def load_poll_timeout() -> int:
    raw = os.getenv("FLEX_TELEGRAM_POLL_TIMEOUT", "20").strip()
    try:
        value = int(raw)
        if value < 1:
            return 20
        return value
    except ValueError:
        return 20


def main() -> None:
    token = load_bot_token()
    timeout = load_poll_timeout()
    runner = TelegramBotRunner(token=token, poll_timeout=timeout)
    print("FLEX Fits V2 Telegram bot is running (long polling). Press Ctrl+C to stop.")
    runner.run_forever()


if __name__ == "__main__":
    main()
