"""
entity_extractor.py — Layer 1: Entity Extraction
-------------------------------------------------
Single entry point: extract_entities(text) -> EntityResult

Extracts structured fields from raw user text:
  brand       – canonical brand name, or None
  size        – exact size string like "42" or "42.5", or None
  gender      – "women" | "men" | None
  color       – color word (lowercase), or None
  model_hint  – remaining tokens after stripping brand/color/size/stopwords
  query_mode  – "browse" | "specific" | "add_to_cart"

query_mode semantics (drives the Decision Engine in telegram_bot.py):
  "add_to_cart" – user explicitly wants to add a product to the cart
                  (e.g. "add to cart puma", "please add this to my bag")
  "specific"    – user is querying a particular item with enough detail
                  to trigger a confirm-or-choose flow
                  (e.g. "adidas size 42", "new balance 574 size 40")
  "browse"      – user wants to see options; show the full matching catalog
                  without jumping into the cart confirmation flow
                  (e.g. "i want adidas", "show me puma for women",
                   "do you have adidas black")

Fuzzy brand matching handles common misspellings:
  "adiads"  -> "adidas"
  "adiddas" -> "adidas"
  "nb"      -> "new balance"
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import get_close_matches
from typing import Optional


# ---------------------------------------------------------------------------
# Brand alias table
# Each key is the canonical brand name stored in inventory.json (lowercase).
# Values are all acceptable spellings including common misspellings.
# ---------------------------------------------------------------------------
_BRAND_VARIANTS: dict[str, list[str]] = {
    "adidas":      ["adidas", "adiads", "adiddas", "adidias", "addidas"],
    "nike":        ["nike", "nik", "nke"],
    "puma":        ["puma", "puma's", "pume"],
    "new balance": ["new balance", "newbalance", "nb", "new ballance"],
    "asics":       ["asics", "assics", "asic"],
    "champion":    ["champion", "chamion"],
    "hoka":        ["hoka"],
    "fila":        ["fila", "filla"],
    "rocktail":    ["rocktail"],
    "oc":          ["oc"],
    "ua":          ["ua"],
    "sacony":      ["sacony", "saucony"],
}

# Sorted by alias length descending so "new balance" is matched before "nb"
_SORTED_ALIASES: list[tuple[str, str]] = sorted(
    (
        (alias, canonical)
        for canonical, aliases in _BRAND_VARIANTS.items()
        for alias in aliases
    ),
    key=lambda x: -len(x[0]),
)

# Single-word aliases only (for fuzzy matching against individual tokens)
_SINGLE_WORD_ALIASES: list[str] = [
    alias for alias, _ in _SORTED_ALIASES if " " not in alias
]

_COLOR_WORDS = {
    "black", "white", "grey", "gray", "blue", "red", "green", "yellow",
    "beige", "orange", "mint", "navy", "brown", "pink", "purple",
}

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

# Maps user-facing gender words to canonical form
_GENDER_TOKENS: dict[str, str] = {
    "woman":  "women",
    "women":  "women",
    "ladies": "women",
    "lady":   "women",
    "girl":   "women",
    "girls":  "women",
    "female": "women",
    "man":    "men",
    "men":    "men",
    "male":   "men",
    "guy":    "men",
    "guys":   "men",
    "gents":  "men",
}

_TYPE_PATTERNS: dict[str, re.Pattern[str]] = {
    "running": re.compile(r"\b(running|run|runningshoes|running\s+shoes)\b", re.IGNORECASE),
    "hiking": re.compile(r"\b(hiking|hike|hikingshoes|hiking\s+shoes)\b", re.IGNORECASE),
    "casual": re.compile(r"\b(casual|casualshoes|casual\s+shoes)\b", re.IGNORECASE),
}

# Phrases that signal the user is browsing, not committing to buy
_BROWSE_PHRASE_PAT = re.compile(
    r"\b(show\s+me|what\s+do\s+you\s+have|do\s+you\s+have|"
    r"what.{0,10}have|catalog|browse|see\s+all|list\s+all|"
    r"show\s+all|see\s+what)\b",
    re.IGNORECASE,
)

# Phrases that mean "put this in my cart right now"
_ADD_CART_PAT = re.compile(
    r"\b(please\s+)?add\b.{0,30}\b(bag|cart)\b"
    r"|\badd\s+to\s+(bag|cart)\b"
    r"|\bput\s+(it\s+)?in\s+(my\s+)?(bag|cart)\b"
    r"|\bi['']ll\s+take\b"
    r"|\btake\s+this\s+one\b"
    r"|\bi['']d\s+like\s+to\s+add\b",
    re.IGNORECASE,
)

# Stopwords that carry no product-specific meaning after entity stripping
_MODEL_STOPWORDS_PAT = re.compile(
    r"\b(what|do|you|have|show|me|add|to|cart|bag|i|need|want|my|the|"
    r"this|that|it|please|order|on|for|in|a|an|is|are|any|some|all|"
    r"let|see|got|shoe|shoes|sneakers?|trainer|trainers|running|casual|"
    r"hiking|sport|sports|size|both|pair|pairs|one|also|and|or)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# EntityResult dataclass
# ---------------------------------------------------------------------------

@dataclass
class EntityResult:
    brand: Optional[str] = None         # canonical brand ("adidas", "new balance", …)
    size: Optional[str] = None          # primary size: first/lower bound for range queries
    size_min: Optional[str] = None      # lower bound when a range is requested ("41" in "41-42")
    size_max: Optional[str] = None      # upper bound when a range is requested ("42" in "41-42")
    gender: Optional[str] = None        # "women" | "men"
    color: Optional[str] = None         # "black", "white", …
    product_type: Optional[str] = None  # "running" | "hiking" | "casual"
    model_hint: list[str] = field(default_factory=list)  # leftover model tokens
    query_mode: str = "browse"           # "browse" | "specific" | "add_to_cart"


# ---------------------------------------------------------------------------
# Individual field extractors (also exported for direct use in other modules)
# ---------------------------------------------------------------------------

def extract_brand(text: str) -> Optional[str]:
    """
    Return the canonical brand name found in text, or None.

    Strategy:
    1. Exact multi-word match  (handles "new balance", "nb", etc.)
    2. Fuzzy single-word match (handles "adiads" → "adidas", cutoff 0.82)

    The 0.82 cutoff was chosen empirically:
      - low enough to catch 1-char typos ("adiads" ≈ 0.857 vs "adidas")
      - high enough to avoid false positives ("men" shouldn't match "nike")
    """
    lowered = text.lower()

    # Pass 1 – exact alias (handles multi-word brands first)
    for alias, canonical in _SORTED_ALIASES:
        if re.search(rf"\b{re.escape(alias)}\b", lowered):
            return canonical

    # Pass 2 – fuzzy single-word (handles typos)
    for word in re.findall(r"[a-z]{3,}", lowered):
        close = get_close_matches(word, _SINGLE_WORD_ALIASES, n=1, cutoff=0.82)
        if close:
            matched_alias = close[0]
            for alias, canonical in _SORTED_ALIASES:
                if alias == matched_alias:
                    return canonical

    return None


# Matches a single EU shoe size number such as "41", "42.5", "43.3".
# Used by both extract_size() and extract_size_range().
_SIZE_NUM_PAT: str = r"(?:3[5-9]|4[0-9]|50)(?:\.(?:5|3))?"


def extract_size(text: str) -> Optional[str]:
    """
    Return first shoe size found in text (e.g. "size 42" or "adidas 42" → "42").
    Handles half-sizes like 42.5 and 42.3.  Decimal commas are normalised first.
    """
    normalized = re.sub(r"(?<=\d)\s*,\s*(?=\d)", ".", text.lower())
    match = re.search(r"\b(?:size\s*)?(3[5-9]|4[0-9]|50)(?:\.(5|3))?\b", normalized)
    if not match:
        return None
    whole = match.group(1)
    frac = match.group(2)
    return f"{whole}.{frac}" if frac else whole


def extract_size_range(text: str) -> Optional[tuple[str, str]]:
    """
    Detect a size range and return (min_str, max_str), or None.

    Supported formats (with optional leading 'size' keyword):
      41-42        →  ("41", "42")
      41 to 42     →  ("41", "42")
      41/42        →  ("41", "42")
      size 41-42   →  ("41", "42")
      42,5-43      →  ("42.5", "43")  (decimal comma normalised)

    The returned tuple is always ordered (smaller, larger).
    Returns None when no range pattern is found in text.
    """
    # Normalise European decimal comma before scanning (42,5 → 42.5).
    normalized = re.sub(r"(?<=\d)\s*,\s*(?=\d)", ".", text.lower())
    # Guard with (?<!\d) / (?!\d) so we don't mistake embedded digits
    # (e.g. "141") as the start/end of a shoe-size range.
    pattern = (
        rf"(?<!\d)(?:size\s*)?({_SIZE_NUM_PAT})"
        rf"\s*(?:-|to|/)\s*"
        rf"({_SIZE_NUM_PAT})(?!\d)"
    )
    match = re.search(pattern, normalized)
    if not match:
        return None
    try:
        a_str, b_str = match.group(1), match.group(2)
        a_f, b_f = float(a_str), float(b_str)
        return (a_str, b_str) if a_f <= b_f else (b_str, a_str)
    except (ValueError, TypeError):
        return None


def extract_gender(text: str) -> Optional[str]:
    """Return 'women' or 'men' if a gender word exists in text, else None."""
    lowered = text.lower()
    for token, gender in _GENDER_TOKENS.items():
        if re.search(rf"\b{re.escape(token)}\b", lowered):
            return gender
    return None


def extract_color(text: str) -> Optional[str]:
    """Return first color word found; 'gray' is normalised to 'grey'."""
    lowered = text.lower()
    for alias, canonical in _ARABIC_COLOR_ALIASES.items():
        if alias in lowered:
            return canonical
    for color in _COLOR_WORDS:
        if re.search(rf"\b{re.escape(color)}\b", lowered):
            return "grey" if color == "gray" else color
    return None


def extract_product_type(text: str) -> Optional[str]:
    """Return canonical product type keyword if present."""
    for canonical, pattern in _TYPE_PATTERNS.items():
        if pattern.search(text):
            return canonical
    return None


def _extract_model_hint(text: str, brand: Optional[str]) -> list[str]:
    """
    Return tokens that likely describe the model after stripping:
      - brand aliases
      - size expression ("size 42")
      - color words
      - gender words
      - common stopwords

    These leftover tokens (e.g. ["574", "run", "falcon"]) are used by the
    Decision Engine to determine whether the query is specific enough to
    trigger a confirm-or-choose flow.
    """
    lowered = text.lower()

    # Strip all aliases for the detected brand
    if brand:
        for alias, canonical in _SORTED_ALIASES:
            if canonical == brand:
                lowered = re.sub(rf"\b{re.escape(alias)}\b", " ", lowered)

    # Strip size range patterns first (e.g. "size 41-42", "41 to 42") so the
    # individual numbers don't survive into the single-size strip below.
    _range_strip = (
        rf"(?<!\d)(?:size\s*)?{_SIZE_NUM_PAT}"
        rf"\s*(?:-|to|/)\s*{_SIZE_NUM_PAT}(?!\d)"
    )
    lowered = re.sub(_range_strip, " ", lowered)

    # Strip remaining single-size patterns with and without the "size" keyword.
    lowered = re.sub(r"\b(?:size\s*)?(3[5-9]|4[0-9]|50)(?:\.(5|3))?\b", " ", lowered)

    # Strip color and gender words
    for color in _COLOR_WORDS:
        lowered = re.sub(rf"\b{re.escape(color)}\b", " ", lowered)
    for token in _GENDER_TOKENS:
        lowered = re.sub(rf"\b{re.escape(token)}\b", " ", lowered)

    # Strip stopwords
    lowered = _MODEL_STOPWORDS_PAT.sub(" ", lowered)

    # Return only alphanumeric tokens longer than 1 char
    return [t for t in re.findall(r"[a-z0-9]{2,}", lowered)]


def _determine_query_mode(
    text: str,
    brand: Optional[str],
    model_hint: list[str],
    size: Optional[str],
    color: Optional[str] = None,
) -> str:
    """
    Classify user intent for the Decision Engine:

    "add_to_cart" – explicit phrasing requesting a cart action
                    ("add to cart puma", "please add this to my bag", "i'll take it")

    "specific"    – the query is constrained enough that a small candidate set
                    can be confirmed or chosen. Triggers when:
                      • size is present ("adidas size 42")
                      • color is present ("I want Adidas black" → spec ADD TO CART example)
                      • model_hint + size both present
                    Also applies to "i want/need" prefix when color or size given.

    "browse"      – user wants to see the full matching catalog without being
                    pushed toward a cart action:
                      • brand-only ("i want adidas", "show me adidas", "do you have adidas")
                      • brand + gender filter ("i want adidas for women")
                      • catalog / collection queries

    Design principle: default to browse when unsure.
    """
    lowered = text.lower()

    # ------------------------------------------------------------------
    # 1. Explicit add-to-cart language always wins
    # ------------------------------------------------------------------
    if _ADD_CART_PAT.search(lowered):
        return "add_to_cart"

    # ------------------------------------------------------------------
    # 2. Explicit browse phrasing ("show me", "catalog", "do you have")
    #    → browse UNLESS both model AND size are also specified
    # ------------------------------------------------------------------
    if _BROWSE_PHRASE_PAT.search(lowered):
        if size or color or (model_hint and size):
            return "specific"
        return "browse"

    # ------------------------------------------------------------------
    # 3. "I want/need" prefix — check qualifying filters:
    #    • color present  ("I want Adidas black")        → specific
    #    • size present   ("I want Adidas size 42")      → specific
    #    • model + size   ("I want Adidas Run Falcon 5 size 42") → specific
    #    • brand/gender only ("I want Adidas for women") → browse
    # ------------------------------------------------------------------
    if re.match(r"^i\s+(want|need)\s+", lowered):
        if color or size or (model_hint and size):
            return "specific"
        return "browse"

    # ------------------------------------------------------------------
    # 4. Size constraint present → specific
    # ------------------------------------------------------------------
    if size:
        return "specific"

    # ------------------------------------------------------------------
    # 5. Color constraint (no explicit add-to-cart and no "i want" prefix)
    #    e.g. "puma black", "adidas black size 42" → specific
    # ------------------------------------------------------------------
    if color:
        return "specific"

    # ------------------------------------------------------------------
    # 6. Default: browse
    # ------------------------------------------------------------------
    return "browse"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def extract_entities(text: str) -> EntityResult:
    """
    Parse a raw user message into structured EntityResult.

    Example:
        >>> e = extract_entities("i want adidas run falcon 5 size 42")
        >>> e.brand       # "adidas"
        >>> e.size        # "42"
        >>> e.model_hint  # ["run", "falcon", "5"]
        >>> e.query_mode  # "specific"
    """
    brand = extract_brand(text)

    # Try a range first so that "41-42" yields size_min="41", size_max="42".
    # Fall back to a single size if no range pattern is found.
    size_range = extract_size_range(text)
    if size_range:
        size_min, size_max = size_range
        size = size_min          # "size" is the lower bound; used for memory / labels
    else:
        size = extract_size(text)
        size_min = size          # single value: min == max == the size itself
        size_max = size

    gender = extract_gender(text)
    color = extract_color(text)
    product_type = extract_product_type(text)
    model_hint = _extract_model_hint(text, brand)
    query_mode = _determine_query_mode(text, brand, model_hint, size, color)

    return EntityResult(
        brand=brand,
        size=size,
        size_min=size_min,
        size_max=size_max,
        gender=gender,
        color=color,
        product_type=product_type,
        model_hint=model_hint,
        query_mode=query_mode,
    )
