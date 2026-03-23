from __future__ import annotations

import re
from typing import List


def _is_inventory_snippet(chunk: str) -> bool:
    return "| type:" in chunk and "| price:" in chunk


def _clean_product_name(name: str) -> str:
    tokens = [token for token in name.split() if token]
    if len(tokens) >= 2 and tokens[0].lower() == tokens[1].lower():
        return " ".join([tokens[0]] + tokens[2:]).strip()
    return name


def _format_inventory_line(chunk: str) -> str:
    # Expected shape: id | Brand Model | type: X | color: Y | price: $X | sizes: [...] | status: in_stock
    parts = [part.strip() for part in chunk.split("|")]
    if len(parts) < 6:
        return chunk

    name = _clean_product_name(parts[1])
    type_part = ""
    color_part = ""
    price_part = ""
    sizes_part = ""
    status_part = ""
    for part in parts[2:]:
        if part.startswith("type:"):
            type_part = part.replace("type:", "").strip()
        elif part.startswith("color:"):
            color_part = part.replace("color:", "").strip()
        elif part.startswith("price:"):
            price_part = part.replace("price:", "").strip()
        elif part.startswith("sizes:"):
            sizes_part = part.replace("sizes:", "").strip()
        elif part.startswith("status:"):
            status_part = part.replace("status:", "").strip()

    size_display = re.sub(r"\s+", " ", sizes_part)
    color_display = f", color {color_part}" if color_part else ""
    return f"- {name} ({type_part}) {price_part}{color_display}, sizes {size_display}, {status_part}."


def _format_category_lines(chunks: List[str]) -> str:
    categories = set()
    for chunk in chunks:
        if not _is_inventory_snippet(chunk):
            continue
        parts = [part.strip() for part in chunk.split("|")]
        if len(parts) >= 3:
            category = parts[2].replace("type:", "").strip()
            if category:
                categories.add(category)

    if not categories:
        return ""
    sorted_categories = ", ".join(sorted(categories))
    return f"We currently have: {sorted_categories}."


def _format_inventory_results(chunks: List[str], limit: int | None = 3) -> str:
    lines: List[str] = []
    for chunk in chunks:
        if not _is_inventory_snippet(chunk):
            continue
        lines.append(_format_inventory_line(chunk))
        if limit is not None and len(lines) >= limit:
            break
    return "\n".join(lines)


def generate_support_response(base_reply: str, intent: str, highlights: List[str] | None = None, show_all: bool = False) -> str:
    highlights = highlights or []

    # Conversational and action intents should never dump retrieval chunks.
    conversational = {
        "greeting", "small_talk", "thank_you", "goodbye", "help", "delivery_info", 
        "return_policy", "about_authenticity", "about_location", "about_showroom",
        "about_exchange", "about_refund", "payment_methods", "delivery_timeline",
        "ask_color", "ask_size", "ask_brand", "ask_type", "ask_price",
        "human_handoff", "fallback", "create_order",
    }
    if intent in conversational:
        return base_reply.strip()

    if intent == "list_categories":
        category_reply = _format_category_lines(highlights)
        if category_reply:
            return category_reply
        return base_reply.strip()

    # For show_all, display ALL results without limit
    limit = None if (intent == "show_all" or show_all) else 3
    inventory_block = _format_inventory_results(highlights, limit=limit)
    if inventory_block:
        return f"{base_reply}\n{inventory_block}".strip()

    # If no inventory context exists for a product intent, ask a precise follow-up.
    product_intents = {"search_product", "check_availability", "check_category_availability", "recommend", "sale_query", "check_price"}
    if intent in product_intents:
        return "I can help. Tell me a brand, type, or size and I will check available options for you."

    return base_reply.strip()
