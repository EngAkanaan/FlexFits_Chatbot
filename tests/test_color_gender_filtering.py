"""
Tests for Phase 1–3 features:
  - Color pre-filtering in retriever
  - Gender pre-filtering in retriever
  - create_order and show_all intent detection
  - Category keyword → search_product intent
  - Exact model phrase boost (Galaxy 6 ranked first vs. Run Falcon 5)
  - extract_color / extract_gender helpers
"""
from __future__ import annotations

import re

from modules.intent_router import detect_intent, extract_color, extract_gender
from modules.retriever import Retriever


# ── Helpers ──────────────────────────────────────────────────────────────────

def _brands_in(snippets: list[str]) -> list[str]:
    """Extract the 'Brand Model' segment from each inventory snippet."""
    results = []
    for s in snippets:
        parts = [p.strip() for p in s.split("|")]
        if len(parts) >= 2:
            results.append(parts[1])
    return results


def _colors_in(snippets: list[str]) -> list[str]:
    """Return raw snippet strings — used to verify color field is not exposed by retriever."""
    return snippets


# ── Intent detection tests ────────────────────────────────────────────────────

def test_create_order_intent():
    # Explicit item-selection and add-to-cart phrases → create_order
    assert detect_intent("i need this one") == "create_order"
    assert detect_intent("add to cart") == "create_order"
    # "I want/need [brand]" without specific add-to-cart language →
    # search_product (Decision Engine decides browse vs. confirm via query_mode)
    assert detect_intent("i need adidas shoes") == "search_product"
    # General checkout intent (no specific item)
    assert detect_intent("i need to order") == "intent_to_order"
    assert detect_intent("i want to order") == "intent_to_order"


def test_arabic_lebanese_intents():
    assert detect_intent("السلام عليكم") == "greeting"
    assert detect_intent("mar7aba") == "greeting"
    assert detect_intent("كيف الحال") == "small_talk"
    assert detect_intent("شو السعر") == "check_price"
    assert detect_intent("shu l se3er") == "check_price"
    assert detect_intent("اريد هدا الحداء") == "create_order"
    assert detect_intent("badde he l shoes") == "create_order"
    assert detect_intent("بدي اطلب") == "intent_to_order"


def test_cancel_order_intent():
    assert detect_intent("cancel") == "cancel_order"
    assert detect_intent("cancel my order") == "cancel_order"
    assert detect_intent("cancel order") == "cancel_order"


def test_finish_order_intent():
    assert detect_intent("i need to finish my order") == "finish_order"
    assert detect_intent("finish my order") == "finish_order"
    assert detect_intent("finalize my order") == "finish_order"


def test_sale_query_stays_sale_with_categories():
    assert detect_intent("what do you have on sale casual hiking runningshoes") == "sale_query"


def test_show_all_intent():
    assert detect_intent("show me all of them") == "show_all"
    assert detect_intent("i need them all") == "show_all"
    assert detect_intent("all of them") == "show_all"
    assert detect_intent("show all") == "show_all"
    assert detect_intent("all") == "show_all"


def test_category_keyword_triggers_search_product():
    assert detect_intent("show me casual") == "search_product"
    assert detect_intent("i want running shoes") == "search_product"
    assert detect_intent("hiking shoes available?") == "search_product"
    assert detect_intent("show me runningshoes") == "search_product"
    assert detect_intent("show me casualshoes") == "search_product"
    assert detect_intent("show me hikingshoes") == "search_product"


def test_gender_keyword_triggers_search_product():
    assert detect_intent("shoes for woman") == "search_product"
    assert detect_intent("men shoes size 44") == "search_product"
    assert detect_intent("ladies sneakers") == "search_product"


def test_color_keyword_triggers_search_product():
    assert detect_intent("i need black shoes") == "search_product"
    assert detect_intent("show me white ones") == "search_product"
    assert detect_intent("بدي حذاء اسود") == "search_product"
    assert detect_intent("شو في أبيض؟") == "search_product"


# ── extract_color helper ──────────────────────────────────────────────────────

def test_extract_color_returns_color():
    assert extract_color("i need black shoes") == "black"
    assert extract_color("show me white sneakers") == "white"
    assert extract_color("gray running shoes") == "grey"   # normalised
    assert extract_color("بدي اسود") == "black"
    assert extract_color("بدي أبيض") == "white"
    assert extract_color("عايز ازرق") == "blue"


def test_extract_color_returns_none_when_absent():
    assert extract_color("i need shoes size 42") is None
    assert extract_color("show me adidas") is None


# ── extract_gender helper ─────────────────────────────────────────────────────

def test_extract_gender_women():
    assert extract_gender("shoes for woman") == "women"
    assert extract_gender("ladies casual") == "women"
    assert extract_gender("girls shoes") == "women"


def test_extract_gender_men():
    assert extract_gender("men shoes size 44") == "men"
    assert extract_gender("guys sneakers") == "men"


def test_extract_gender_none():
    assert extract_gender("i need running shoes") is None
    assert extract_gender("adidas size 42") is None


# ── Retriever: color pre-filtering ───────────────────────────────────────────

def test_color_filter_returns_only_black():
    r = Retriever()
    snippets = r.retrieve_inventory_snippets(
        user_message="i need shoes",
        intent="search_product",
        top_k=20,
        color_hint="black",
    )
    # Every returned snippet must correspond to a product whose color includes "black".
    # The snippet itself doesn't expose color, so we verify via the retriever's own
    # logic: if color filtering works, NO non-black product should appear.
    # We can reload inventory and cross-reference IDs.
    import json
    from pathlib import Path
    inv = json.loads((Path(__file__).resolve().parents[1] / "data" / "inventory.json").read_text())
    id_to_color = {
        str(row.get("id") or row.get("Product_ID") or ""): str(row.get("color") or row.get("Color") or "").lower()
        for row in inv
    }

    for snippet in snippets:
        product_id = snippet.split("|")[0].strip()
        color_field = id_to_color.get(product_id, "")
        assert "black" in color_field, (
            f"Non-black product in results: {product_id!r} has color {color_field!r}"
        )


def test_color_filter_white_excludes_black_only():
    r = Retriever()
    snippets = r.retrieve_inventory_snippets(
        user_message="white shoes",
        intent="search_product",
        top_k=20,
        color_hint="white",
    )
    import json
    from pathlib import Path
    inv = json.loads((Path(__file__).resolve().parents[1] / "data" / "inventory.json").read_text())
    id_to_color = {
        str(row.get("id") or row.get("Product_ID") or ""): str(row.get("color") or row.get("Color") or "").lower()
        for row in inv
    }

    for snippet in snippets:
        product_id = snippet.split("|")[0].strip()
        color_field = id_to_color.get(product_id, "")
        assert "white" in color_field, (
            f"Non-white product in results: {product_id!r} has color {color_field!r}"
        )


# ── Retriever: gender pre-filtering ──────────────────────────────────────────

def test_gender_filter_women_excludes_men_products():
    r = Retriever()
    snippets = r.retrieve_inventory_snippets(
        user_message="shoes",
        intent="search_product",
        top_k=20,
        gender_hint="women",
    )
    import json
    from pathlib import Path
    inv = json.loads((Path(__file__).resolve().parents[1] / "data" / "inventory.json").read_text())
    id_to_gender = {
        str(row.get("id") or row.get("Product_ID") or ""): str(row.get("gender") or row.get("Gender") or "unisex").lower()
        for row in inv
    }

    for snippet in snippets:
        product_id = snippet.split("|")[0].strip()
        gender = id_to_gender.get(product_id, "unisex")
        assert gender in {"women", "unisex"}, (
            f"Men-only product returned for women query: {product_id!r} gender={gender!r}"
        )


def test_gender_filter_men_excludes_women_products():
    r = Retriever()
    snippets = r.retrieve_inventory_snippets(
        user_message="shoes",
        intent="search_product",
        top_k=20,
        gender_hint="men",
    )
    import json
    from pathlib import Path
    inv = json.loads((Path(__file__).resolve().parents[1] / "data" / "inventory.json").read_text())
    id_to_gender = {
        str(row.get("id") or row.get("Product_ID") or ""): str(row.get("gender") or row.get("Gender") or "unisex").lower()
        for row in inv
    }

    for snippet in snippets:
        product_id = snippet.split("|")[0].strip()
        gender = id_to_gender.get(product_id, "unisex")
        assert gender in {"men", "unisex"}, (
            f"Women-only product returned for men query: {product_id!r} gender={gender!r}"
        )


# ── Retriever: exact model phrase boost ──────────────────────────────────────

def test_exact_model_phrase_boost_galaxy_6_ranks_first():
    """'adidas galaxy 6' should rank Galaxy 6 above Run Falcon 5."""
    r = Retriever()
    snippets = r.retrieve_inventory_snippets(
        user_message="i need adidas galaxy 6",
        intent="search_product",
        top_k=5,
    )
    assert snippets, "Expected at least one result for 'adidas galaxy 6'"
    top_result = snippets[0]
    assert "Galaxy 6" in top_result, (
        f"Expected Galaxy 6 ranked first, got: {top_result!r}"
    )


def test_hiking_request_returns_only_hiking_types():
    r = Retriever()
    snippets = r.retrieve_inventory_snippets(
        user_message="show me hiking shoes",
        intent="search_product",
        top_k=20,
    )

    assert snippets
    for snippet in snippets:
        assert "type: Hiking" in snippet or "type: hiking" in snippet


def test_brand_filter_returns_only_requested_brand():
    r = Retriever()
    snippets = r.retrieve_inventory_snippets(
        user_message="what do you have puma",
        intent="search_product",
        top_k=20,
    )

    assert snippets
    for snippet in snippets:
        assert "| Puma " in snippet


def test_brand_and_size_filter_returns_only_exact_matches():
    r = Retriever()
    snippets = r.retrieve_inventory_snippets(
        user_message="puma size 40",
        intent="search_product",
        top_k=20,
    )

    assert snippets
    for snippet in snippets:
        assert "| Puma " in snippet
        assert "sizes:" in snippet
        assert re.search(r"\b40\b", snippet)


def test_running_request_returns_only_running_types():
    r = Retriever()
    snippets = r.retrieve_inventory_snippets(
        user_message="show me running shoes",
        intent="search_product",
        top_k=20,
    )

    assert snippets
    for snippet in snippets:
        assert "type: Running" in snippet or "type: running" in snippet


def test_casual_request_returns_only_casual_types():
    r = Retriever()
    snippets = r.retrieve_inventory_snippets(
        user_message="show me casual shoes",
        intent="search_product",
        top_k=20,
    )

    assert snippets
    for snippet in snippets:
        assert "type: Casual" in snippet or "type: casual" in snippet


def test_exact_size_query_uses_size_window():
    r = Retriever()
    snippets = r.retrieve_inventory_snippets(
        user_message="adidas size 42",
        intent="search_product",
        top_k=20,
    )

    assert snippets
    flattened = "\n".join(snippets)
    assert "41" in flattened or "41.3" in flattened
    assert "42.5" in flattened or "43" in flattened


def test_size_window_prioritizes_exact_match_first():
    r = Retriever()
    snippets = r.retrieve_inventory_snippets(
        user_message="asics size 42",
        intent="search_product",
        top_k=10,
    )

    assert snippets
    first_sizes = [p.strip() for p in snippets[0].split("|") if "sizes:" in p]
    assert first_sizes
    assert "42" in first_sizes[0]


def test_snippets_only_expose_in_stock_sizes():
    r = Retriever()
    snippets = r.retrieve_inventory_snippets(
        user_message="adidas",
        intent="search_product",
        top_k=20,
        return_all=True,
    )

    target = next((snippet for snippet in snippets if "Adidas Run Falcon 5" in snippet), "")
    assert target
    assert "40" in target and "41" in target and "42.5" in target
    assert "44" not in target and "46" not in target


def test_women_collection_uses_35_to_42_range():
    r = Retriever()
    snippets = r.retrieve_inventory_snippets(
        user_message="show me women collection",
        intent="search_product",
        top_k=30,
        gender_hint="women",
    )

    assert snippets
    for snippet in snippets:
        sizes_part = [p.strip() for p in snippet.split("|") if "sizes:" in p]
        assert sizes_part
        raw = sizes_part[0].split("sizes:")[-1]
        numeric = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", raw)]
        assert any(35.0 <= x <= 42.0 for x in numeric)


def test_men_collection_uses_40_to_50_range():
    r = Retriever()
    snippets = r.retrieve_inventory_snippets(
        user_message="show me men collection",
        intent="search_product",
        top_k=30,
        gender_hint="men",
    )

    assert snippets
    for snippet in snippets:
        sizes_part = [p.strip() for p in snippet.split("|") if "sizes:" in p]
        assert sizes_part
        raw = sizes_part[0].split("sizes:")[-1]
        numeric = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", raw)]
        # Men's range is 40+ (sizes 40–50)
        assert any(40.0 <= x <= 50.0 for x in numeric)


# ── New: intent routing ───────────────────────────────────────────────────────

def test_show_cart_intent():
    assert detect_intent("show my cart") == "show_cart"
    assert detect_intent("what's in my cart") == "show_cart"
    assert detect_intent("what is in my cart") == "show_cart"
    assert detect_intent("my cart") == "show_cart"


def test_remove_last_intent():
    assert detect_intent("no i don't need it") == "remove_last"
    assert detect_intent("no i dont need it") == "remove_last"
    assert detect_intent("remove it") == "remove_last"
    assert detect_intent("remove last") == "remove_last"
    assert detect_intent("i don't want it") == "remove_last"


def test_brand_query_intent_is_search_product_not_create_order():
    # "i need/want [brand]" must be search_product so the Decision Engine
    # can apply browse mode instead of jumping to the cart add flow.
    assert detect_intent("i need adidas shoes") == "search_product"
    assert detect_intent("i want puma") == "search_product"
    assert detect_intent("i want adidas for women") == "search_product"


# ── New: entity extractor ─────────────────────────────────────────────────────

def test_fuzzy_brand_matching_adidas_typos():
    from modules.entity_extractor import extract_brand
    assert extract_brand("adida shoes") == "adidas"
    assert extract_brand("adiads shoes") == "adidas"
    assert extract_brand("adiddas run falcon") == "adidas"


def test_typo_brand_query_routes_to_search_product():
    assert detect_intent("show me all adida") == "search_product"
    assert detect_intent("do you have adiads") == "search_product"


def test_fuzzy_brand_matching_nb_alias():
    from modules.entity_extractor import extract_brand
    assert extract_brand("do you have nb size 42") == "new balance"
    assert extract_brand("new balance 574") == "new balance"


def test_fuzzy_brand_matching_puma():
    from modules.entity_extractor import extract_brand
    assert extract_brand("puma white size 40") == "puma"
    assert extract_brand("i want puma shoes") == "puma"


def test_query_mode_browse_for_brand_only():
    from modules.entity_extractor import extract_entities
    e = extract_entities("i want adidas")
    assert e.brand == "adidas"
    assert e.query_mode == "browse"


def test_query_mode_specific_for_brand_and_color():
    from modules.entity_extractor import extract_entities
    # Spec ADD TO CART example: "I want Adidas black" → check matches → confirm/choose
    e = extract_entities("i want adidas black")
    assert e.brand == "adidas"
    assert e.color == "black"
    assert e.query_mode == "specific"


def test_query_mode_browse_for_brand_and_gender():
    from modules.entity_extractor import extract_entities
    e = extract_entities("i want adidas for women")
    assert e.brand == "adidas"
    assert e.gender == "women"
    assert e.query_mode == "browse"  # Rule 3: browsing must never auto-add to cart


def test_query_mode_specific_for_brand_and_size():
    from modules.entity_extractor import extract_entities
    e = extract_entities("adidas size 42")
    assert e.brand == "adidas"
    assert e.size == "42"
    assert e.query_mode == "specific"


def test_extract_size_without_size_keyword():
    from modules.entity_extractor import extract_entities
    e = extract_entities("adidas 42")
    assert e.brand == "adidas"
    assert e.size == "42"


def test_extract_size_normalizes_decimal_comma():
    from modules.entity_extractor import extract_entities
    e = extract_entities("adidas 42,5")
    assert e.brand == "adidas"
    assert e.size == "42.5"


def test_extract_entities_includes_product_type():
    from modules.entity_extractor import extract_entities
    e = extract_entities("adidas running shoes size 38")
    assert e.brand == "adidas"
    assert e.product_type == "running"
    assert e.size == "38"


def test_query_mode_add_to_cart_for_explicit_phrase():
    from modules.entity_extractor import extract_entities
    e = extract_entities("add to cart puma white")
    assert e.query_mode == "add_to_cart"
    e2 = extract_entities("please add this to my bag")
    assert e2.query_mode == "add_to_cart"


# ── New: show_cart and remove_last bot behavior ───────────────────────────────

def test_show_cart_lists_items():
    from telegram_bot import TelegramBotRunner
    runner = TelegramBotRunner(token="fake-token")
    sent: list[str] = []
    runner.send_message = lambda chat_id, text: sent.append(text)  # type: ignore[method-assign]
    runner._append_chat_log = lambda chat_id, role, text: None      # type: ignore[method-assign]

    state = runner._get_chat_state(42)
    state.bag = ["Adidas Run Falcon 5 (Running Shoes) $70", "Puma Court Shatter Low (Casual) $65"]

    runner.process_update({"message": {"chat": {"id": 42}, "text": "show my cart"}})
    assert sent
    assert "1. Adidas Run Falcon 5" in sent[-1]
    assert "2. Puma Court Shatter Low" in sent[-1]


def test_show_cart_empty_gives_friendly_message():
    from telegram_bot import TelegramBotRunner
    runner = TelegramBotRunner(token="fake-token")
    sent: list[str] = []
    runner.send_message = lambda chat_id, text: sent.append(text)  # type: ignore[method-assign]
    runner._append_chat_log = lambda chat_id, role, text: None      # type: ignore[method-assign]

    runner.process_update({"message": {"chat": {"id": 43}, "text": "show my cart"}})
    assert sent
    assert "empty" in sent[-1].lower()


def test_remove_last_removes_only_last_item():
    from telegram_bot import TelegramBotRunner
    runner = TelegramBotRunner(token="fake-token")
    sent: list[str] = []
    runner.send_message = lambda chat_id, text: sent.append(text)  # type: ignore[method-assign]
    runner._append_chat_log = lambda chat_id, role, text: None      # type: ignore[method-assign]

    state = runner._get_chat_state(44)
    state.bag = ["Adidas Run Falcon 5 (Running Shoes) $70", "Puma Court Shatter Low (Casual) $65"]

    runner.process_update({"message": {"chat": {"id": 44}, "text": "no i don't need it"}})
    assert sent
    # Only the last item is removed; first item must still be in bag
    assert len(state.bag) == 1
    assert state.bag[0] == "Adidas Run Falcon 5 (Running Shoes) $70"
    assert "Puma Court Shatter Low" in sent[-1]  # confirmation mentions removed item


def test_remove_last_on_empty_cart_gives_feedback():
    from telegram_bot import TelegramBotRunner
    runner = TelegramBotRunner(token="fake-token")
    sent: list[str] = []
    runner.send_message = lambda chat_id, text: sent.append(text)  # type: ignore[method-assign]
    runner._append_chat_log = lambda chat_id, role, text: None      # type: ignore[method-assign]

    runner.process_update({"message": {"chat": {"id": 45}, "text": "remove it"}})
    assert sent
    assert "empty" in sent[-1].lower()


def test_intent_to_order_with_number_adds_selected_last_result():
    from telegram_bot import TelegramBotRunner

    runner = TelegramBotRunner(token="fake-token")
    sent: list[str] = []
    runner.send_message = lambda chat_id, text: sent.append(text)  # type: ignore[method-assign]
    runner._append_chat_log = lambda chat_id, role, text: None      # type: ignore[method-assign]

    state = runner._get_chat_state(99)
    state.last_results = [
        "Asics GEL-Nimbus 23 (Running Shoes) $39",
        "Asics GEL-Kayano 28 (Running Shoes) $39",
    ]

    runner.process_update({"message": {"chat": {"id": 99}, "text": "i need to order 2"}})

    assert state.bag == ["Asics GEL-Kayano 28 (Running Shoes) $39"]
    assert state.order_stage == "name"
    assert sent
    assert "full name" in sent[-1].lower()


# ── New: size range parsing ───────────────────────────────────────────────────

def test_extract_size_range_dash():
    """'adidas size 41-42' → size_min='41', size_max='42'."""
    from modules.entity_extractor import extract_entities
    e = extract_entities("adidas size 41-42")
    assert e.brand == "adidas"
    assert e.size_min == "41"
    assert e.size_max == "42"
    assert e.size == "41"          # lower bound is the primary size


def test_extract_size_range_to_keyword():
    """'adidas size 41 to 42' → min=41, max=42."""
    from modules.entity_extractor import extract_entities
    e = extract_entities("adidas size 41 to 42")
    assert e.size_min == "41"
    assert e.size_max == "42"


def test_extract_size_range_slash():
    """'adidas size 41/42' → min=41, max=42."""
    from modules.entity_extractor import extract_entities
    e = extract_entities("adidas size 41/42")
    assert e.size_min == "41"
    assert e.size_max == "42"


def test_extract_size_range_bare_no_keyword():
    """'adidas 41-42' (no 'size' keyword) → range detected."""
    from modules.entity_extractor import extract_entities
    e = extract_entities("adidas 41-42")
    assert e.size_min == "41"
    assert e.size_max == "42"


def test_extract_size_range_decimal_comma():
    """'adidas size 42,5-43' → min='42.5', max='43'."""
    from modules.entity_extractor import extract_entities
    e = extract_entities("adidas size 42,5-43")
    assert e.size_min == "42.5"
    assert e.size_max == "43"


def test_extract_size_range_query_mode_is_specific():
    """A range query must be classified as 'specific' (not 'browse')."""
    from modules.entity_extractor import extract_entities
    e = extract_entities("adidas size 41-42")
    assert e.query_mode == "specific"


def test_single_size_has_equal_min_max():
    """For a plain single-size query, size_min and size_max equal size."""
    from modules.entity_extractor import extract_entities
    e = extract_entities("adidas size 42")
    assert e.size == "42"
    assert e.size_min == "42"
    assert e.size_max == "42"


def test_retriever_range_query_window():
    """
    'adidas size 41-42' should expand to window [40, 43] and return products
    that have in-stock sizes anywhere in that range.
    """
    r = Retriever()
    snippets = r.retrieve_inventory_snippets(
        user_message="adidas size 41-42",
        intent="search_product",
        top_k=20,
    )
    assert snippets, "Expected results for adidas size range 41-42"
    # Every snippet must be adidas
    for snippet in snippets:
        assert "| Adidas " in snippet, f"Non-adidas product in results: {snippet!r}"
    # The combined sizes across all snippets must cover the requested window (40-43)
    all_sizes_text = "\n".join(snippets)
    size_values = [
        float(s)
        for s in re.findall(r"\d+(?:\.\d+)?", all_sizes_text.split("sizes:")[-1])
        if 35.0 <= float(s) <= 50.0
    ]
    assert any(40.0 <= v <= 43.0 for v in size_values), (
        "Expected at least one size in window [40, 43]"
    )


def test_retriever_range_prioritises_exact_matches_first():
    """Products whose sizes fall inside the requested range rank above fringe-only products."""
    r = Retriever()
    snippets = r.retrieve_inventory_snippets(
        user_message="adidas size 41-42",
        intent="search_product",
        top_k=10,
    )
    assert snippets
    # The first result's sizes part should contain 41 or 42 directly.
    first_sizes_part = snippets[0].split("sizes:")[-1]
    assert re.search(r"\b4[12]\b", first_sizes_part), (
        f"Expected 41 or 42 in top result's sizes, got: {first_sizes_part!r}"
    )
