from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Dict

from modules.intent_router import detect_intent, extract_color, extract_gender
from modules.entity_extractor import extract_entities
from modules.rag_service import RagService
from modules.response_generator import generate_support_response
from modules.conversation_memory import (
    update_search_memory,
    has_stored_search,
    get_stored_context,
    load_memory,
)

# Fixed key used to namespace local-chat memory on disk.
_LOCAL_CHAT_ID = "local"

# Intents that perform a product search and should save context to memory.
_SEARCH_INTENTS = {
    "search_product", "sale_query", "recommend", "check_availability",
    "check_category_availability", "recommend_fallback_keywords",
}


@dataclass
class LocalState:
    last_entities: Dict[str, object] = field(default_factory=dict)
    bag: list[dict[str, object]] = field(default_factory=list)
    order_mode: bool = False
    cancel_confirmation_pending: bool = False
    pending_add_item: dict[str, object] | None = None


def _is_add_to_cart_phrase(text: str) -> bool:
    lowered = text.lower()
    return any(
        token in lowered
        for token in [
            "add to cart",
            "add to bag",
            "add them to cart",
            "add them to bag",
            "please add",
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


def _clean_product_name(name: str) -> str:
    tokens = [token for token in name.split() if token]
    if len(tokens) >= 2 and tokens[0].lower() == tokens[1].lower():
        return " ".join([tokens[0]] + tokens[2:]).strip()
    return name


def _parse_product_from_snippet(snippet: str) -> dict[str, Any] | None:
    parts = [p.strip() for p in snippet.split("|")]
    if len(parts) < 2:
        return None

    product_id = parts[0]
    raw_name = parts[1]
    type_value = ""
    price_value = 0.0
    size_values: list[str] = []

    for part in parts[2:]:
        if part.startswith("type:"):
            type_value = part.replace("type:", "").strip()
        elif part.startswith("price:"):
            raw_price = part.replace("price:", "").replace("$", "").strip()
            try:
                price_value = float(raw_price)
            except ValueError:
                price_value = 0.0
        elif part.startswith("sizes:"):
            raw_sizes = part.replace("sizes:", "").strip()
            size_values = re.findall(r"\d+(?:\.\d+)?", raw_sizes)

    lowered_name = raw_name.lower()
    brand_value: str | None = None
    model_value = raw_name
    for brand in ["adidas", "asics", "nike", "puma", "new balance", "champion", "rocktail", "hoka", "fila", "oc", "ua", "sacony"]:
        if lowered_name.startswith(f"{brand} "):
            brand_value = brand.title()
            model_value = raw_name[len(brand):].strip()
            break
        if lowered_name == brand:
            brand_value = brand.title()
            model_value = ""
            break

    if brand_value and model_value and model_value.lower() == brand_value.lower():
        model_value = ""

    if not brand_value:
        cleaned = _clean_product_name(raw_name)
        split = cleaned.split(" ", 1)
        brand_value = split[0] if split else cleaned
        model_value = split[1] if len(split) > 1 else ""

    display_name = f"{brand_value} {model_value}".strip() if model_value else brand_value

    return {
        "id": product_id,
        "brand": brand_value,
        "model": model_value,
        "type": type_value,
        "price": price_value,
        "sizes": size_values,
        "display_name": display_name,
    }


def _extract_products_from_last_results() -> list[dict[str, Any]]:
    memory = load_memory(_LOCAL_CHAT_ID)
    products: list[dict[str, Any]] = []
    for snippet in memory.get("last_results", []) or []:
        if not isinstance(snippet, str):
            continue
        product = _parse_product_from_snippet(snippet)
        if not product:
            continue
        products.append(product)
    return products


def _pick_product_from_last_results(user_message: str, products: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not products:
        return None

    lowered = user_message.lower()
    if any(token in lowered for token in ["this one", "it", "first one", "the first"]):
        return products[0]

    # Pick by token overlap with item label.
    query_tokens = set(re.findall(r"[a-z0-9]+", lowered))
    stopwords = {"add", "to", "cart", "bag", "the", "please", "i", "need", "want", "it", "this", "one", "shoes", "shoe"}
    query_tokens = {token for token in query_tokens if token not in stopwords}

    def _expanded_tokens(tokens: set[str]) -> set[str]:
        expanded = set(tokens)
        for token in list(tokens):
            for part in re.findall(r"[a-z]+|\d+", token):
                expanded.add(part)
        return expanded

    query_tokens = _expanded_tokens(query_tokens)

    best_product: dict[str, Any] | None = None
    best_overlap = 0
    for product in products:
        label = f"{product.get('display_name', '')} {product.get('type', '')}"
        label_tokens = set(re.findall(r"[a-z0-9]+", label.lower()))
        label_tokens = _expanded_tokens(label_tokens)
        overlap = len(query_tokens.intersection(label_tokens))
        if overlap > best_overlap:
            best_overlap = overlap
            best_product = product

    if best_overlap > 0:
        return best_product
    return None


def _build_cart_item(product: dict[str, Any], preferred_size: str | None) -> dict[str, object]:
    available_sizes = [str(s) for s in product.get("sizes", [])]
    selected_size = preferred_size if preferred_size and preferred_size in available_sizes else (available_sizes[0] if available_sizes else "")
    return {
        "id": str(product.get("id", "")),
        "brand": str(product.get("brand", "")),
        "model": str(product.get("model", "")),
        "size": selected_size,
        "price": float(product.get("price", 0.0) or 0.0),
    }


def _format_cart_item(item: dict[str, object]) -> str:
    brand = str(item.get("brand", "")).strip()
    model = str(item.get("model", "")).strip()
    size = str(item.get("size", "")).strip()
    price = float(item.get("price", 0.0) or 0.0)

    title = f"{brand} {model}".strip() if model else brand
    if size:
        title = f"{title} size {size}"
    return f"{title} - ${price:.2f}"


def _format_cart_summary(bag: list[dict[str, object]]) -> tuple[float, str]:
    """
    Return (total_price, formatted_cart_str).
    For now, uses a simple mock price. In production, would look up actual prices.
    """
    if not bag:
        return 0.0, "Your cart is empty."
    
    subtotal = 0.0
    delivery_fee = 4.0

    cart_str = "Your cart:\n"
    for item in bag:
        price = float(item.get("price", 0.0) or 0.0)
        subtotal += price
        cart_str += f"  • {_format_cart_item(item)}\n"
    total = subtotal + delivery_fee
    cart_str += f"Subtotal: ${subtotal:.2f}\nDelivery: ${delivery_fee:.2f}\nTotal: ${total:.2f}"
    
    return total, cart_str


def _is_cancel_confirmation(text: str) -> bool:
    """Check if user is confirming cancellation (yes, sure, confirm, etc)."""
    lowered = text.lower().strip()
    return any(
        token in lowered
        for token in ["yes", "sure", "confirm", "i'm sure", "im sure", "okay", "ok", "go ahead"]
    )


def _is_cancel_rejection(text: str) -> bool:
    """Check if user is rejecting cancellation (no, keep, don't, etc)."""
    lowered = text.lower().strip()
    return any(
        token in lowered
        for token in ["no", "keep", "don't", "dont", "don't cancel", "nevermind", "cancel that"]
    )


def run_local_chat() -> None:
    service = RagService()
    state = LocalState()

    print("FLEX Fits V2 local mode is running.")
    print("Type your message. Type 'exit' to stop.")

    while True:
        user_message = input("You: ").strip()
        if not user_message:
            continue
        if user_message.lower() in {"exit", "quit"}:
            print("Session ended.")
            break

        # ── Handle cancel confirmation flow ──────────────────────────────────
        if state.cancel_confirmation_pending:
            if _is_cancel_confirmation(user_message):
                state.bag = []
                state.cancel_confirmation_pending = False
                print("Nelly: Your order has been cancelled. How can I help you today?")
                continue
            elif _is_cancel_rejection(user_message):
                state.cancel_confirmation_pending = False
                print("Nelly: Good! Your cart still has these items:")
                for item in state.bag:
                    print(f"  • {item}")
                print("Say 'finish my order' when you're ready to proceed.")
                continue
            else:
                # Unclear response to cancel confirmation
                print("Nelly: I didn't understand. Do you want to cancel? Just say 'yes' or 'no'.")
                continue

        # ── Handle add-to-cart confirmation flow ─────────────────────────────
        if state.pending_add_item:
            if _is_cancel_confirmation(user_message):
                exists = any(
                    str(existing.get("id")) == str(state.pending_add_item.get("id"))
                    and str(existing.get("size")) == str(state.pending_add_item.get("size"))
                    for existing in state.bag
                )
                if not exists:
                    state.bag.append(state.pending_add_item)
                print(f"Nelly: Done, I added to your cart: {_format_cart_item(state.pending_add_item)}.")
                state.pending_add_item = None
                continue
            if _is_cancel_rejection(user_message):
                state.pending_add_item = None
                print("Nelly: No problem. Tell me which shoe you want instead.")
                continue
            print("Nelly: Please reply yes or no. Do you want me to add it to your cart?")
            continue

        intent = detect_intent(user_message)
        entities = extract_entities(user_message)

        # ── show_all: replay last search with expanded top_k ─────────────────
        if intent == "show_all":
            if not has_stored_search(_LOCAL_CHAT_ID):
                print("Nelly: Please tell me what you're looking for first.")
                continue
            ctx = get_stored_context(_LOCAL_CHAT_ID)
            restore_intent = str(ctx.get("last_intent") or "search_product")
            restore_msg = str(ctx.get("last_message") or user_message)
            result = service.generate_support_reply(
                user_message=restore_msg,
                intent=restore_intent,
                state_entities=state.last_entities,
                color_hint=ctx.get("last_color"),
                gender_hint=ctx.get("last_gender"),
                top_k=50,
            )
            base_reply = str(result.get("reply", "")) or "I can help with shoes, sizing, and delivery in Lebanon."
            intro_only = base_reply.splitlines()[0] if base_reply else "Sure. Here are all the options I found."
            reply = generate_support_response(
                intro_only,
                intent="show_all",
                highlights=result.get("context_chunks", []),
                show_all=True,
            )
            print(f"Nelly: {reply}")
            continue

        # ── cancel_order: ask for confirmation before clearing cart ──────────
        if intent == "cancel_order":
            if not state.bag:
                print("Nelly: Your cart is already empty. How can I help?")
                continue
            state.cancel_confirmation_pending = True
            print("Nelly: Are you sure you want to cancel your order? (yes/no)")
            continue

        # ── intent_to_order: clarify what they want to order ─────────────────
        if intent == "intent_to_order":
            products = _extract_products_from_last_results()
            if products:
                state.pending_add_item = _build_cart_item(products[0], entities.size)
                print(f"Nelly: Do you want me to add {_format_cart_item(state.pending_add_item)} to the cart? (yes/no)")
            elif state.bag:
                print(f"Nelly: You already have {len(state.bag)} item(s) in your cart. Do you want to add more shoes or finish your order?")
            else:
                print("Nelly: Sure! What brand, size, or style would you like? I can show you options.")
            continue

        # ── finish_order: collect delivery details and confirm ───────────────
        if intent == "finish_order":
            if not state.bag:
                print("Nelly: Your cart is empty. Tell me what you'd like to order first.")
                continue
            
            # Show cart summary
            total, cart_str = _format_cart_summary(state.bag)
            print(f"Nelly: {cart_str}")
            
            # Collect delivery information one by one
            print("\nLet me get your delivery details:")
            
            name = input("Your name: ").strip()
            if not name:
                print("Nelly: Name is required.")
                continue
            
            governate = input("Governate (e.g., Mount Lebanon, Beirut): ").strip()
            if not governate:
                print("Nelly: Governate is required.")
                continue
            
            city = input("City: ").strip()
            if not city:
                print("Nelly: City is required.")
                continue
            
            area = input("Area/District: ").strip()
            if not area:
                print("Nelly: Area is required.")
                continue
            
            street = input("Street address: ").strip()
            if not street:
                print("Nelly: Street is required.")
                continue
            
            building = input("Building number: ").strip()
            if not building:
                print("Nelly: Building number is required.")
                continue
            
            phone = input("Phone number: ").strip()
            if not phone:
                print("Nelly: Phone is required.")
                continue
            
            email = input("Email (optional): ").strip() or "not_provided"
            
            # Confirm order
            print("\n=== ORDER CONFIRMATION ===")
            print(cart_str)
            print(f"\nDelivery to:\n{name}\n{area}, {city}, {governate}\n{street}, Building {building}\nPhone: {phone}\n")
            
            confirm = input("Confirm order? (yes/no): ").strip().lower()
            if confirm in {"yes", "y", "ok", "okay", "sure"}:
                print("Nelly: Perfect! Your order has been submitted. Our admin team will contact you soon to confirm.")
                print("Thank you for shopping with FLEX Fits!")
                
                state.bag = []
            else:
                print("Nelly: Order cancelled. No changes were made.")
            continue

        # Local order/cart flow: understand add-to-cart and "i need it" style commands.
        if _is_add_to_cart_phrase(user_message) or _is_need_or_want_item_phrase(user_message):
            products = _extract_products_from_last_results()
            chosen = _pick_product_from_last_results(user_message, products)
            if chosen is None:
                print("Nelly: Tell me the exact model you want, or ask me to show options first.")
                continue

            cart_item = _build_cart_item(chosen, entities.size)
            exists = any(
                str(existing.get("id")) == str(cart_item.get("id"))
                and str(existing.get("size")) == str(cart_item.get("size"))
                for existing in state.bag
            )
            if not exists:
                state.bag.append(cart_item)
            print(f"Nelly: Done, I added to your cart: {_format_cart_item(cart_item)}.")
            continue

        if intent == "create_order":
            state.order_mode = True
            print("Nelly: Sure. Which model and size do you want to order? I will walk you through it.")
            continue

        # ── regular flow: extract filters, inherit prior gender if absent ─────
        color_hint = extract_color(user_message)
        gender_hint = extract_gender(user_message)

        # Size-only and brand+size stock checks (across all brands or one brand).
        if intent in _SEARCH_INTENTS and entities.size:
            strict_snippets = service.retriever.retrieve_inventory_snippets(
                user_message=user_message,
                intent="search_product",
                top_k=30,
                color_hint=color_hint,
                gender_hint=gender_hint,
            )
            has_exact = any(
                entities.size in re.findall(r"\d+(?:\.\d+)?", part.replace("sizes:", "").strip())
                for snippet in strict_snippets
                for part in snippet.split("|")
                if "sizes:" in part
            )

            if entities.brand and not entities.model_hint and not has_exact:
                print(f"Nelly: Sorry, {entities.brand.title()} size {entities.size} is currently out of stock.")
                continue

            if not entities.brand and not entities.model_hint and not has_exact:
                print(f"Nelly: Sorry, we currently don't have sneakers available in size {entities.size}.")
                continue

        # Carry the gender filter forward so "show me casual" keeps the women filter
        # from a prior "shoes for woman" query without the user repeating themselves.
        if gender_hint is None and intent in _SEARCH_INTENTS:
            stored = get_stored_context(_LOCAL_CHAT_ID)
            gender_hint = stored.get("last_gender")

        result = service.generate_support_reply(
            user_message=user_message,
            intent=intent,
            state_entities=state.last_entities,
            color_hint=color_hint,
            gender_hint=gender_hint,
        )
        reply = str(result.get("reply", "")) or "I can help with shoes, sizing, and delivery in Lebanon."

        if state.order_mode and intent in _SEARCH_INTENTS:
            reply = f"{reply}\n\nIf you want one of these, say: add to cart the model name."

        print(f"Nelly: {reply}")

        # Persist search context for follow-up pronoun queries.
        if intent in _SEARCH_INTENTS:
            chunks = result.get("context_chunks", [])
            update_search_memory(
                chat_id=_LOCAL_CHAT_ID,
                intent=intent,
                results=chunks,
                color=color_hint,
                gender=gender_hint,
                original_message=user_message,
            )


if __name__ == "__main__":
    run_local_chat()
