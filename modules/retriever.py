from __future__ import annotations

import json
import re
from difflib import get_close_matches
from pathlib import Path
from typing import Dict, List


BASE_DIR = Path(__file__).resolve().parents[1]
FAQ_PATH = BASE_DIR / "knowledge" / "faq.md"
INVENTORY_PATH = BASE_DIR / "data" / "inventory.json"


FAQ_TOPIC_BY_INTENT: Dict[str, set[str]] = {
    "delivery_info": {"delivery"},
    "return_policy": {"returns", "exchanges"},
    "human_handoff": {"human", "handoff", "support"},
    "sale_query": {"sale"},
    "search_product": {"availability", "sizing"},
    "check_availability": {"availability", "sizing"},
    "check_category_availability": {"availability", "sizing"},
    "recommend": {"availability", "sizing", "sale"},
    "recommend_fallback_keywords": {"availability", "sizing", "sale"},
}

_BRAND_ALIASES: Dict[str, set[str]] = {
    "adidas": {"adidas"},
    "asics": {"asics"},
    "champion": {"champion"},
    "new balance": {"new balance", "newbalance", "nb"},
    "rocktail": {"rocktail"},
    "puma": {"puma"},
    "nike": {"nike"},
    "hoka": {"hoka"},
    "oc": {"oc"},
    "ua": {"ua"},
    "fila": {"fila"},
    "sacony": {"sacony", "saucony"},
}

_COLOR_WORDS = {
    "black", "white", "grey", "gray", "blue", "red", "green", "yellow",
    "beige", "orange", "mint", "navy", "brown", "pink", "purple",
}

_ARABIC_COLOR_ALIASES: Dict[str, str] = {
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


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9\.]+", text.lower())


def _canonical_type_token(token: str) -> str | None:
    lowered = token.lower().strip()
    if lowered in {"running", "run"}:
        return "running"
    if lowered in {"hiking", "hike"}:
        return "hiking"
    if lowered in {"casual"}:
        return "casual"
    return None


def _extract_requested_types(user_message: str) -> set[str]:
    found: set[str] = set()
    for raw in re.findall(
        r"\b(running|run|runningshoes|running_shoes|hiking|hike|hikingshoes|hiking_shoes|casual|casualshoes|casual_shoes)\b",
        user_message.lower(),
    ):
        token = raw.replace("_shoes", "").replace("shoes", "")
        canonical = _canonical_type_token(token)
        if canonical:
            found.add(canonical)
    return found


def _extract_exact_size(user_message: str) -> str | None:
    normalized = re.sub(r"(?<=\d)\s*,\s*(?=\d)", ".", user_message.lower())
    match = re.search(r"\bsize\s*(3[5-9]|4[0-9]|50)(?:\.(5|3))?\b", normalized)
    if not match:
        return None
    whole = match.group(1)
    frac = match.group(2)
    if not frac:
        return str(int(whole))
    return f"{int(whole)}.{frac}"


# Matches a single EU shoe size like "41", "42.5", "43.3" (no capturing groups).
_RETRIEVER_SIZE_NUM = r"(?:3[5-9]|4[0-9]|50)(?:\.(?:5|3))?"


def _extract_size_range_from_message(user_message: str) -> tuple[float, float] | None:
    """
    Detect a size range expression ("41-42", "41 to 42", "41/42",
    "size 41-42", "42,5-43") and return (min_float, max_float), or None.

    Decimal commas are normalised before scanning.  A strict lookbehind /
    lookahead prevents interpreting embedded multi-digit numbers (e.g. "141")
    as the start of a range.
    """
    normalized = re.sub(r"(?<=\d)\s*,\s*(?=\d)", ".", user_message.lower())
    pattern = (
        rf"(?<!\d)(?:size\s*)?({_RETRIEVER_SIZE_NUM})"
        rf"\s*(?:-|to|/)\s*"
        rf"({_RETRIEVER_SIZE_NUM})(?!\d)"
    )
    match = re.search(pattern, normalized)
    if not match:
        return None
    try:
        a, b = float(match.group(1)), float(match.group(2))
        return (min(a, b), max(a, b))
    except (ValueError, TypeError):
        return None


def _size_label_to_float(size_label: str | None) -> float | None:
    if not size_label:
        return None
    try:
        return float(size_label)
    except (TypeError, ValueError):
        return None


def _extract_brand(user_message: str) -> str | None:
    # Reuse fuzzy brand parser so typos like adida/adiads/adiddas still map.
    try:
        from .entity_extractor import extract_brand as _extract_brand_fuzzy

        brand = _extract_brand_fuzzy(user_message)
        if brand:
            return brand
    except Exception:
        pass

    lowered = user_message.lower()
    for canonical, aliases in _BRAND_ALIASES.items():
        for alias in aliases:
            if re.search(rf"\b{re.escape(alias)}\b", lowered):
                return canonical
    return None


def _model_tokens_match(model_tokens: set[str], haystack_tokens: set[str]) -> bool:
    """Allow small typos in model tokens while keeping deterministic filtering."""
    if not model_tokens:
        return True
    for token in model_tokens:
        if token in haystack_tokens:
            continue
        # Fuzzy fallback for model-like tokens (e.g., "falcn" -> "falcon").
        close = get_close_matches(token, list(haystack_tokens), n=1, cutoff=0.84)
        if not close:
            return False
    return True


def _extract_color_hint(user_message: str) -> str | None:
    lowered = user_message.lower()
    for alias, canonical in _ARABIC_COLOR_ALIASES.items():
        if alias in lowered:
            return canonical
    for color in _COLOR_WORDS:
        if re.search(rf"\b{re.escape(color)}\b", lowered):
            return "grey" if color == "gray" else color
    return None


def _expand_tokens(tokens: set[str]) -> set[str]:
    expanded = set(tokens)
    for token in list(tokens):
        for piece in re.findall(r"[a-z]+|\d+", token):
            expanded.add(piece)
    return expanded


def _extract_model_tokens(user_message: str, brand_hint: str | None) -> set[str]:
    lowered = user_message.lower()
    cleaned = lowered
    if brand_hint:
        for alias in _BRAND_ALIASES.get(brand_hint, {brand_hint}):
            cleaned = re.sub(rf"\b{re.escape(alias)}\b", " ", cleaned)

    # Strip size range patterns first ("size 41-42", "41 to 42", "41/42")
    # so that the individual numbers don't survive as model tokens.
    _range_strip = (
        rf"(?<!\d)(?:size\s*)?{_RETRIEVER_SIZE_NUM}"
        rf"\s*(?:-|to|/)\s*{_RETRIEVER_SIZE_NUM}(?!\d)"
    )
    cleaned = re.sub(_range_strip, " ", cleaned)

    cleaned = re.sub(r"\bsize\s*(3[5-9]|4[0-9]|50)(?:\.(5|3))?\b", " ", cleaned)
    cleaned = re.sub(
        r"\b(what|do|you|have|show|me|add|to|cart|bag|i|need|want|my|the|this|that|it|please|order|on|for|in)\b",
        " ",
        cleaned,
    )
    cleaned = re.sub(r"\b(running|hiking|casual|shoe|shoes|sneakers?|trainers?)\b", " ", cleaned)
    cleaned = re.sub(r"\b(women|woman|ladies|men|man|collection|sale|discount|all|current|priced)\b", " ", cleaned)
    for color in _COLOR_WORDS:
        cleaned = re.sub(rf"\b{re.escape(color)}\b", " ", cleaned)

    tokens = {token for token in re.findall(r"[a-z0-9]+", cleaned) if token}
    if not tokens:
        return set()

    if brand_hint:
        return _expand_tokens(tokens)

    # Without explicit brand, only keep likely model-like tokens (e.g., numeric model IDs).
    model_like = {token for token in tokens if any(ch.isdigit() for ch in token)}
    return _expand_tokens(model_like)


class Retriever:
    def __init__(self, faq_path: Path | None = None, inventory_path: Path | None = None) -> None:
        self.faq_path = faq_path or FAQ_PATH
        self.inventory_path = inventory_path or INVENTORY_PATH

    def _load_faq_chunks(self) -> List[Dict[str, str]]:
        if not self.faq_path.exists():
            return []

        content = self.faq_path.read_text(encoding="utf-8").strip()
        if not content:
            return []

        chunks: List[Dict[str, str]] = []
        sections = re.split(r"\n##\s+", content)
        for index, section in enumerate(sections):
            cleaned = section.strip()
            if not cleaned:
                continue

            if index == 0 and cleaned.startswith("#"):
                continue

            lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
            if not lines:
                continue

            heading = lines[0]
            body = "\n".join(lines[1:]).strip()
            combined = f"{heading}\n{body}".strip()
            chunks.append({"heading": heading, "body": body, "text": combined})
        return chunks

    def _load_inventory_rows(self) -> List[Dict[str, object]]:
        if not self.inventory_path.exists():
            return []

        raw = self.inventory_path.read_text(encoding="utf-8").strip()
        if not raw:
            return []

        data = json.loads(raw)
        if isinstance(data, list):
            normalized: List[Dict[str, object]] = []
            for row in data:
                if not isinstance(row, dict):
                    continue
                normalized.append(self._normalize_inventory_row(row))
            return normalized
        return []

    def _normalize_inventory_row(self, row: Dict[str, object]) -> Dict[str, object]:
        """Map Supabase-style inventory columns into the internal schema expected by retrieval."""
        # Prefer existing internal keys when present, otherwise derive from Supabase-like names.
        product_id = row.get("id") or row.get("Product_ID") or ""
        brand = row.get("brand") or row.get("Name_of_Brand") or ""
        model = row.get("model") or row.get("Name_of_Product") or ""
        product_type = row.get("type") or row.get("Type") or row.get("Category") or ""
        color = row.get("color") or row.get("Color") or "Unknown"
        gender = row.get("gender") or row.get("Gender") or "unisex"

        price_raw = row.get("price", row.get("Price", 0))
        try:
            price = float(price_raw)
        except (TypeError, ValueError):
            price = 0.0

        # Keep stock_by_size if already provided; otherwise derive from SIZE + Stock fields.
        stock_by_size = row.get("stock_by_size")
        if not isinstance(stock_by_size, dict):
            stock_by_size = {}
            size_value = row.get("SIZE")
            stock_value = row.get("Stock", row.get("Items_LEFT_in_stock", 0))
            try:
                parsed_stock = int(stock_value)
            except (TypeError, ValueError):
                parsed_stock = 0
            if isinstance(size_value, list):
                # Distribute aggregate stock across ordered sizes deterministically.
                remaining = max(0, parsed_stock)
                for size in size_value:
                    if remaining <= 0:
                        stock_by_size[str(size)] = 0
                        continue
                    stock_by_size[str(size)] = 1
                    remaining -= 1
                if remaining > 0 and size_value:
                    first_key = str(size_value[0])
                    stock_by_size[first_key] = int(stock_by_size.get(first_key, 0) or 0) + remaining
            elif isinstance(size_value, str):
                # Accept comma/space separated size strings from external datasets.
                raw_sizes = [part.strip() for part in re.split(r"[,/\\s]+", size_value) if part.strip()]
                remaining = max(0, parsed_stock)
                for size in raw_sizes:
                    if remaining <= 0:
                        stock_by_size[str(size)] = 0
                        continue
                    stock_by_size[str(size)] = 1
                    remaining -= 1
                if remaining > 0 and raw_sizes:
                    first_key = str(raw_sizes[0])
                    stock_by_size[first_key] = int(stock_by_size.get(first_key, 0) or 0) + remaining
            elif size_value is not None:
                stock_by_size[str(size_value)] = parsed_stock

        # Build sizes list from stock map when missing.
        sizes = row.get("sizes")
        if not isinstance(sizes, list):
            sizes = []
            for raw_size in stock_by_size.keys():
                try:
                    size_num = float(str(raw_size))
                    if size_num.is_integer():
                        sizes.append(int(size_num))
                    else:
                        sizes.append(size_num)
                except (TypeError, ValueError):
                    continue

        items_sold_raw = row.get("items_sold", row.get("Items_Sold", 0))
        try:
            items_sold = int(items_sold_raw)
        except (TypeError, ValueError):
            items_sold = 0

        status_raw = row.get("status") or row.get("Status")
        status = str(status_raw).strip().lower() if status_raw is not None else ""
        if status not in {"in_stock", "low_stock", "out_of_stock"}:
            # Derive status when external sources use different labels.
            total_stock = 0
            for value in stock_by_size.values():
                try:
                    total_stock += int(value)
                except (TypeError, ValueError):
                    continue
            if total_stock <= 0:
                status = "out_of_stock"
            elif total_stock <= 2:
                status = "low_stock"
            else:
                status = "in_stock"

        return {
            "id": str(product_id),
            "brand": str(brand),
            "model": str(model),
            "type": str(product_type),
            "color": str(color),
            "gender": str(gender),
            "sizes": sizes,
            "price": price,
            "stock_by_size": stock_by_size,
            "items_sold": items_sold,
            "status": str(status),
        }

    def _inventory_status(self, row: Dict[str, object]) -> str:
        stock_by_size = row.get("stock_by_size", {})
        total_stock = 0
        if isinstance(stock_by_size, dict):
            for value in stock_by_size.values():
                try:
                    total_stock += int(value)
                except (TypeError, ValueError):
                    continue

        if total_stock <= 0:
            return "out_of_stock"
        if total_stock <= 2:
            return "low_stock"
        return "in_stock"

    def _allow_inventory_for_intent(self, intent: str | None) -> bool:
        blocked = {"delivery_info", "return_policy", "human_handoff"}
        return (intent or "") not in blocked

    def _allow_sale_row(self, row: Dict[str, object], intent: str | None) -> bool:
        if intent != "sale_query":
            return True
        try:
            return float(row.get("price", 0)) == 39.0
        except (TypeError, ValueError):
            return False

    def _color_matches(self, color_hint: str, row_color: str) -> bool:
        """Return True if color_hint appears anywhere in the row's color field (case-insensitive)."""
        return color_hint.lower() in row_color.lower()

    def _gender_allowed(self, row_gender: str, user_gender: str | None) -> bool:
        """
        Hard gender label filter — Layer 2: Inventory Filter Engine.

        Rule:
          user asks 'women' → return only products labeled 'women' or 'unisex'
          user asks 'men'   → return only products labeled 'men'   or 'unisex'
          no gender signal  → return everything

        'unisex' is always allowed for any gender query because unisex shoes
        cover the full size range and are appropriate for all shoppers.
        """
        if user_gender is None:
            return True
        row_g = row_gender.lower().strip()
        if row_g == "unisex":
            return True
        if user_gender == "women":
            return row_g == "women"
        if user_gender == "men":
            return row_g == "men"
        return True

    def _row_has_size_in_range(self, row: Dict[str, object], min_size: float, max_size: float) -> bool:
        stock_by_size = row.get("stock_by_size", {})
        if not isinstance(stock_by_size, dict):
            return False

        for size_key, qty in stock_by_size.items():
            try:
                if int(qty) <= 0:
                    continue
                size_value = float(str(size_key))
            except (TypeError, ValueError):
                continue
            if min_size <= size_value <= max_size:
                return True
        return False

    def _gender_range_allowed(self, row: Dict[str, object], user_gender: str | None) -> bool:
        """
        Secondary size-range guard after gender label filtering.

        Even after filtering by inventory gender label, we apply a size-range
        check so that unisex items only appear in the correct section:
          women → sizes 35–41  (per store policy; women's EU sizing)
          men   → sizes 40–50  (per store policy; men's EU sizing)

        Why both layers?
        The label + size combo handles:
          1. Mislabeled inventory rows (size-range catches them)
          2. Unisex shoes sold in both ranges (both genders see them where appropriate)
        """
        if user_gender == "women":
            return self._row_has_size_in_range(row, 35.0, 41.0)  # women: 35-41
        if user_gender == "men":
            return self._row_has_size_in_range(row, 40.0, 50.0)  # men: 40+
        return True

    def _row_matches_exact_size(self, row: Dict[str, object], size_label: str | None) -> bool:
        if not size_label:
            return True
        stock_by_size = row.get("stock_by_size", {})
        if not isinstance(stock_by_size, dict):
            return False
        return int(stock_by_size.get(size_label, 0) or 0) > 0

    def _available_size_pairs(self, row: Dict[str, object]) -> list[tuple[str, float]]:
        stock_by_size = row.get("stock_by_size", {})
        if not isinstance(stock_by_size, dict):
            return []

        pairs: list[tuple[str, float]] = []
        for raw_size, qty in stock_by_size.items():
            try:
                if int(qty) <= 0:
                    continue
                size_value = float(str(raw_size))
            except (TypeError, ValueError):
                continue
            size_label = str(raw_size)
            pairs.append((size_label, size_value))
        pairs.sort(key=lambda item: item[1])
        return pairs

    def _row_matches_size_window(self, row: Dict[str, object], requested_size: float | None) -> bool:
        if requested_size is None:
            return True

        min_size = requested_size - 1.0
        max_size = requested_size + 1.0
        for _, size_value in self._available_size_pairs(row):
            if min_size <= size_value <= max_size:
                return True
        return False

    def _window_sizes_for_row(self, row: Dict[str, object], requested_size: float | None) -> list[str]:
        pairs = self._available_size_pairs(row)
        if requested_size is None:
            return [label for label, _ in pairs]

        min_size = requested_size - 1.0
        max_size = requested_size + 1.0
        return [label for label, value in pairs if min_size <= value <= max_size]

    def _window_sizes_for_range(
        self, row: Dict[str, object], window_min: float, window_max: float
    ) -> list[str]:
        """
        Return the labels of all in-stock sizes that fall within
        [window_min, window_max] (already expanded by \xb11 by the caller).
        """
        return [
            label
            for label, value in self._available_size_pairs(row)
            if window_min <= value <= window_max
        ]

    def _size_range_score(
        self, row: Dict[str, object], range_min: float, range_max: float
    ) -> tuple[int, float]:
        """
        Scoring for range queries (analogous to _size_window_score for single-size).

        Returns (exact_boost, min_distance) where:
          exact_boost = 1  if any in-stock size sits within [range_min, range_max]
                         0  if only close-by sizes (outside the range) are present
          min_distance = smallest float distance from any in-stock size to the
                         nearest bound of [range_min, range_max].  Used for
                         tiebreaking: products closer to the requested range rank
                         higher than products that only match via the \xb11 expansion.
        """
        candidate_values = [
            v for _, v in self._available_size_pairs(row)
            if range_min - 1.0 <= v <= range_max + 1.0
        ]
        if not candidate_values:
            return (0, 99.0)

        # A size is "exact" when it sits within the user-requested range.
        exact_values = [v for v in candidate_values if range_min <= v <= range_max]
        exact_boost = 1 if exact_values else 0

        # Distance: 0 for exact hits; distance to nearest bound for fringe hits.
        if exact_boost:
            min_dist = 0.0
        else:
            min_dist = min(
                min(abs(v - range_min), abs(v - range_max))
                for v in candidate_values
            )
        return (exact_boost, min_dist)

    def _size_window_score(self, row: Dict[str, object], requested_size: float | None) -> tuple[int, float]:
        if requested_size is None:
            return (0, 99.0)

        candidate_values = [
            value
            for _, value in self._available_size_pairs(row)
            if requested_size - 1.0 <= value <= requested_size + 1.0
        ]
        if not candidate_values:
            return (0, 99.0)

        min_diff = min(abs(value - requested_size) for value in candidate_values)
        return (1 if min_diff == 0 else 0, min_diff)

    def _row_matches_types(self, row: Dict[str, object], requested_types: set[str]) -> bool:
        if not requested_types:
            return True
        row_type = str(row.get("type", "")).lower()
        if "running" in requested_types and "running" in row_type:
            return True
        if "hiking" in requested_types and "hiking" in row_type:
            return True
        if "casual" in requested_types and "casual" in row_type:
            return True
        return False

    def _faq_allowed_by_intent(self, heading: str, intent: str | None) -> bool:
        if not intent:
            return True

        allowed_topics = FAQ_TOPIC_BY_INTENT.get(intent)
        if not allowed_topics:
            return True

        heading_tokens = set(_tokenize(heading))
        return any(topic in heading_tokens for topic in allowed_topics)

    def retrieve_faq(self, user_message: str, intent: str | None = None, top_k: int = 3) -> List[str]:
        chunks = self._load_faq_chunks()
        if not chunks:
            return []

        query_tokens = set(_tokenize(user_message))

        scored: List[tuple[int, str]] = []
        for chunk in chunks:
            heading = chunk.get("heading", "")
            text = chunk.get("text", "")
            if not self._faq_allowed_by_intent(heading, intent):
                continue

            overlap_score = len(query_tokens.intersection(_tokenize(text)))
            heading_boost = 2 if query_tokens.intersection(_tokenize(heading)) else 0
            score = overlap_score + heading_boost
            scored.append((score, text))

        scored.sort(key=lambda item: item[0], reverse=True)
        picked = [chunk for score, chunk in scored if score > 0][:top_k]

        # Deterministic fallback: return top topical chunk if query had no overlap.
        if not picked and scored:
            return [scored[0][1]]
        return picked

    def retrieve_inventory_snippets(
        self,
        user_message: str,
        intent: str | None = None,
        top_k: int = 3,
        color_hint: str | None = None,
        gender_hint: str | None = None,
        return_all: bool = False,
    ) -> List[str]:
        """
        Inventory Filter Engine — Layer 2 of the pipeline.

        Hard exclusion filters (applied before scoring):
          • out-of-stock rows are skipped
          • sale_query: only rows priced at $39 pass
          • gender_hint: only matching gender label + size range rows pass
          • brand_hint: only the requested brand passes (zero cross-brand leakage)
          • model_tokens: all extracted model tokens must appear in brand+model text
          • color: only rows whose color field contains the requested color pass

        When return_all=True the top_k cap is lifted so browsers see the
        complete matching catalog (used by the browse path in telegram_bot.py).
        """
        if not self._allow_inventory_for_intent(intent):
            return []

        rows = self._load_inventory_rows()
        if not rows:
            return []

        query_tokens = set(_tokenize(user_message))
        collection_query = "collection" in user_message.lower()
        requested_types = _extract_requested_types(user_message)

        # ── Size window computation ──────────────────────────────────────────
        # For a single-size query ("size 42") the window is [41, 43].
        # For a range query ("size 41-42") the window is [40, 43] — i.e. the
        # user-requested range expanded by \xb11 on each end.
        size_range = _extract_size_range_from_message(user_message)
        if size_range:
            range_min_f, range_max_f = size_range
            window_min: float | None = range_min_f - 1.0
            window_max: float | None = range_max_f + 1.0
            requested_size_value: float | None = None  # no single pivot for range
        else:
            exact_size = _extract_exact_size(user_message)
            requested_size_value = _size_label_to_float(exact_size)
            if requested_size_value is not None:
                window_min = requested_size_value - 1.0
                window_max = requested_size_value + 1.0
            else:
                window_min = window_max = None

        brand_hint = _extract_brand(user_message)
        model_tokens = _extract_model_tokens(user_message, brand_hint)
        if intent == "sale_query":
            model_tokens = set()
        inferred_color = _extract_color_hint(user_message)
        effective_color_hint = color_hint or inferred_color
        scored: List[tuple[int, int, float, str]] = []

        for row in rows:
            # --- Business rule filters (hard exclusions) ---
            if not self._allow_sale_row(row, intent):
                continue

            # --- Gender pre-filter: drop rows that don't match user's gender signal ---
            row_gender = str(row.get("gender", "unisex"))
            if not self._gender_allowed(row_gender, gender_hint):
                continue

            # Enforce women/men collection by size ranges regardless of data labeling quality.
            if not self._gender_range_allowed(row, gender_hint):
                continue

            # Strict category filter: if user requested hiking/casual/running, return only those types.
            if not self._row_matches_types(row, requested_types):
                continue

            # Size-window filter: match only rows that have an in-stock size
            # inside the computed window (single-size \xb11  OR  range \xb11 on each end).
            if window_min is not None and window_max is not None:
                if not self._row_has_size_in_range(row, window_min, window_max):
                    continue

            # Strict brand filter: if user requested a brand, never return other brands.
            brand = str(row.get("brand", "")).strip()
            row_brand = brand.lower()
            if brand_hint and row_brand != brand_hint:
                continue

            # Strict model token filter: all extracted model tokens must exist in brand+model text.
            model = str(row.get("model", "")).strip()
            if model_tokens:
                haystack_tokens = _expand_tokens(set(_tokenize(f"{brand} {model}")))
                if not _model_tokens_match(model_tokens, haystack_tokens):
                    continue

            # --- Color pre-filter: drop rows whose color doesn't include the requested color ---
            row_color = str(row.get("color", ""))
            if effective_color_hint and not self._color_matches(effective_color_hint, row_color):
                continue

            product_type = str(row.get("type", "")).strip()
            price = row.get("price", "")
            # Sizes shown in the snippet are those that fall within the computed window.
            if window_min is not None and window_max is not None:
                sizes = self._window_sizes_for_range(row, window_min, window_max)
            else:
                sizes = self._window_sizes_for_row(row, requested_size_value)
            status = self._inventory_status(row)

            if status == "out_of_stock":
                continue

            snippet = (
                f"{row.get('id', '')} | {brand} {model} | type: {product_type} "
                f"| color: {row_color} | price: ${price} | sizes: {sizes} | status: {status}"
            )
            snippet_tokens = set(_tokenize(snippet))
            overlap_score = len(query_tokens.intersection(snippet_tokens))

            brand_boost = 2 if brand and brand.lower() in user_message.lower() else 0
            # Exact model name phrase match: boosts specific models like "Galaxy 6", "Run Falcon 5".
            model_phrase_boost = 5 if model and model.lower() in user_message.lower() else 0
            model_token_boost = 2 if model and model.lower() in user_message.lower() else 0
            type_boost = 2 if requested_types and self._row_matches_types(row, requested_types) else 0

            # Size scoring: range queries and single-size queries use separate helpers.
            if size_range:
                exact_size_boost, size_distance = self._size_range_score(
                    row, range_min_f, range_max_f
                )
            else:
                exact_size_boost, size_distance = self._size_window_score(row, requested_size_value)

            size_boost = 0
            if window_min is not None and window_max is not None:
                size_boost = 6 if exact_size_boost else max(1, 4 - int(size_distance * 10))
            in_stock_boost = 1 if status == "in_stock" else 0

            score = overlap_score + brand_boost + model_phrase_boost + model_token_boost + type_boost + size_boost + in_stock_boost
            scored.append((score, exact_size_boost, size_distance, snippet))

        # Sort by: score desc, exact-size first, then smallest size distance.
        scored.sort(key=lambda item: (-item[0], -item[1], item[2]))

        # When return_all is set (browse catalog mode) return every item that
        # passed the hard filters, ignoring the top_k cap entirely.
        if return_all:
            return [snippet for _, _, _, snippet in scored]

        if collection_query:
            picked = [snippet for _, _, _, snippet in scored[:top_k]]
        else:
            picked = [snippet for score, _, _, snippet in scored if score > 0][:top_k]

        # Deterministic fallback for broad recommendation intents.
        if not picked and intent in {"recommend", "recommend_fallback_keywords", "search_product"}:
            return [snippet for _, _, _, snippet in scored[: min(top_k, len(scored))]]
        return picked
