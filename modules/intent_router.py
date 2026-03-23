from __future__ import annotations

import re


_BRAND_PATTERN = re.compile(
    r"\b(adidas|adida|adiads|adiddas|asics|champion|new\s*balance|newbalance|nb|rocktail|puma|nike|hoka|oc|ua|fila|sacony|saucony)\b"
)
_SIZE_PATTERN = re.compile(r"\b(3[5-9]|4[0-9]|50)(?:\.5|\.3)?\b")
_COLOR_PATTERN = re.compile(
    r"\b(black|white|grey|gray|blue|red|green|yellow|beige|orange|mint|navy|brown|pink|purple)\b"
    r"|اسود|أسود|ابيض|أبيض|احمر|أحمر|ازرق|أزرق|اخضر|أخضر|اصفر|أصفر|رمادي|رصاصي|بيج|برتقالي|بني|زهري|وردي|بنفسجي|كحلي|نعناعي"
)
_GENDER_PATTERN = re.compile(
    r"\b(woman|women|ladies|lady|girl|girls|female|man|men|male|guy|guys|gents)\b"
)
_CATEGORY_PATTERN = re.compile(
    r"\b(casual|running|hiking|runningshoes|hikingshoes|casualshoes|sport|sports|sneakers?|trainer|trainers)\b"
)
_SHOW_ALL_PATTERN = re.compile(
    r"\ball\s+of\s+them\b|\bthem\s+all\b|\bi\s+need\s+them\s+all\b"
    r"|\bshow\s+me\s+all\b|\bsee\s+them\s+all\b|\blist\s+all\b|\ball\s+of\s+it\b"
    r"|\bshow\s+all\b|\bsee\s+all\b|^\s*all\s*$"
)
_GREETING_PATTERN = re.compile(
    r"\b(hello|hi|hey|marhaba|mar7aba|salam|salam\s+alaykom|salam\s+alaykum)\b"
    r"|مرحبا|السلام\s+عليكم|وعليكم\s+السلام|اهلا|أهلا|هلا|صباح\s+الخير|مسا\s+الخير"
)

_ARABIC_COLOR_ALIASES: dict[str, str] = {
    "اسود": "black",
    "أسود": "black",
    "ابيض": "white",
    "أبيض": "white",
    "احمر": "red",
    "أحمر": "red",
    "ازرق": "blue",
    "أزرق": "blue",
    "اخضر": "green",
    "أخضر": "green",
    "اصفر": "yellow",
    "أصفر": "yellow",
    "رمادي": "grey",
    "رصاصي": "grey",
    "بيج": "beige",
    "برتقالي": "orange",
    "بني": "brown",
    "زهري": "pink",
    "وردي": "pink",
    "بنفسجي": "purple",
    "كحلي": "navy",
    "نعناعي": "mint",
}


def extract_color(text: str) -> str | None:
    """Return the first color word found in text, normalised to lowercase. 'gray' maps to 'grey'."""
    lowered = text.lower()
    for alias, canonical in _ARABIC_COLOR_ALIASES.items():
        if alias in lowered:
            return canonical
    match = _COLOR_PATTERN.search(lowered)
    if not match:
        return None
    color = match.group(1)
    return "grey" if color == "gray" else color


def extract_gender(text: str) -> str | None:
    """Return 'women' or 'men' if a gender signal is found in text, else None."""
    match = _GENDER_PATTERN.search(text.lower())
    if not match:
        return None
    token = match.group(1)
    if token in {"woman", "women", "ladies", "lady", "girl", "girls", "female"}:
        return "women"
    return "men"


def detect_intent(text: str) -> str:
    """Detect intent using pattern matching. Falls back to XML patterns if no match."""
    message = text.lower().strip()

    if not message:
        return "fallback"

    # -- Priority patterns (fastest, regex-based) --
    if _GREETING_PATTERN.search(message):
        return "greeting"

    if any(
        token in message
        for token in [
            "how are you",
            "kifik",
            "كيفك",
            "akhbar",
            "كيف الحال",
            "شو الاخبار",
            "شو الأخبار",
            "kifak",
            "kifek",
            "kifkon",
            "shu l akhbar",
            "akhbarak",
        ]
    ):
        return "small_talk"

    if any(
        token in message
        for token in [
            "who are you",
            "your name",
            "who r u",
            "مين انت",
            "شو اسمك",
            "شو بتعمل",
            "شو فيك تساعد",
        ]
    ):
        return "help"

    if any(token in message for token in ["showroom", "physical store", "walk in store", "walk-in store"]):
        return "about_showroom"

    if any(token in message for token in ["how many days", "delivery timeline", "delivery time", "when will it arrive", "how long delivery"]):
        return "delivery_timeline"

    # Cancel intent: must be checked early
    if any(token in message for token in ["cancel", "cancel order", "cancel my order"]):
        return "cancel_order"

    # Finish order intent: complete/finalize the order with delivery details
    if any(
        token in message
        for token in [
            "i need to finish my order",
            "i need to finalize my order",
            "i need to complete my order",
            "finish my order",
            "finalize my order",
            "complete my order",
            "proceed to checkout",
            "finalize order",
            "finish order",
        ]
    ):
        return "finish_order"

    # Intent to order (general, no specific item): "i need to order" without item details
    if any(
        token in message
        for token in [
            "i need to order",
            "i want to order",
            "place order",
            "i'd like to order",
            "بدي اطلب",
            "بدّي اطلب",
            "اريد اطلب",
            "bade order",
            "badde order",
        ]
    ) and not any(
        token in message
        for token in ["adidas", "nike", "asics", "puma", "new balance", "hoka", "champion", "rocktail", "reebok", "fila", "saucony"]
    ):
        return "intent_to_order"

    # Specific item order intent phrases
    if any(
        token in message
        for token in [
            "i need this one",
            "i need it",
            "i need both",
            "i want this one",
            "i want it",
            "i want both",
            "add to bag",
            "add them to bag",
            "add them to cart",
            "add to cart",
            "please add",
            "i'll take",
            "اريد هذا الحذاء",
            "اريد هدا الحداء",
            "بدي هيدا الحذاء",
            "بدي هالحذاء",
            "badde he l shoes",
            "badde hayda",
            "khod hayda",
        ]
    ):
        return "create_order"

    # Sale/discount should stay sale_query even if category words are present.
    if any(token in message for token in ["sale", "discount", "on sale"]):
        return "sale_query"

    # Show cart: customer wants to see what's currently in their cart
    if any(
        token in message
        for token in [
            "show my cart", "show cart", "my cart", "view cart",
            "what's in my cart", "what is in my cart", "whats in my cart",
            "cart items", "items in cart", "what do i have in my cart",
        ]
    ):
        return "show_cart"

    # Remove last: customer declines or removes the last cart item
    # (does NOT clear the whole cart; use cancel_order for that)
    if any(
        token in message
        for token in [
            "no i don't need it", "no i dont need it",
            "i don't need it", "i dont need it",
            "i don't want it", "i dont want it",
            "remove it", "remove last", "remove the last",
            "take it out", "delete last", "remove from cart", "remove from bag",
        ]
    ):
        return "remove_last"

    # Entity-rich product queries (brand / size / color / gender / category)
    # go to search_product — the Decision Engine will decide browse vs. confirm.
    # NOTE: "i need/want [brand]" is intentionally NOT routed to create_order;
    # the entity extractor determines query_mode (browse vs. specific) instead.

    # Entity-rich product queries: brand / size / color / gender / category.
    # These always route to search_product so the Decision Engine can apply
    # the correct browse vs. confirm logic via entity_extractor.query_mode.
    if (
        _SIZE_PATTERN.search(message)
        or _BRAND_PATTERN.search(message)
        or _COLOR_PATTERN.search(message)
        or _GENDER_PATTERN.search(message)
        or _CATEGORY_PATTERN.search(message)
    ):
        return "search_product"

    if any(
        token in message
        for token in [
            "what do you have",
            "what do you sell",
            "catalog",
            "categories",
            "catalogue",
            "شو عندكم",
            "شو في",
            "شو بتبيعو",
            "شو بتبيعوا",
        ]
    ):
        return "list_categories"

    if _SHOW_ALL_PATTERN.search(message):
        return "show_all"

    if any(token in message for token in ["deliver", "shipping", "delivery", "delivery lebanon", "توصيل", "شحن"]):
        return "delivery_info"

    if any(token in message for token in ["return", "exchange", "refund", "ترجيع", "استبدال", "ارجاع"]):
        return "return_policy"

    if any(token in message for token in ["human", "agent", "representative", "موظف", "حدا من الفريق", "بدي احكي مع حدا"]):
        return "human_handoff"

    if any(token in message for token in ["recommend", "suggest", "anything"]):
        return "recommend"

    if any(token in message for token in ["price", "how much", "cost", "price?", "السعر", "شو السعر", "قديش السعر", "shu l se3er", "shu se3er", "se3er"]):
        return "check_price"

    # -- Fallback to XML patterns --
    try:
        from .intent_loader import get_intent_loader
        loader = get_intent_loader()
        xml_match = loader.match_pattern(text)
        if xml_match:
            return xml_match
    except Exception:
        pass  # If XML loader fails, continue to default fallback

    return "fallback"
