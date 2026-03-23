from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Dict, List

BASE_DIR = Path(__file__).resolve().parents[1]
MEMORY_DIR = BASE_DIR / "logs" / "chat_memory"
DEFAULT_MEMORY_TTL_DAYS = 7

# Pronouns that mean "the same thing I just searched for".
_PRONOUN_PATTERN = re.compile(
    r"\ball\s+of\s+them\b|\bthem\s+all\b|\bi\s+need\s+them\s+all\b"
    r"|\bshow\s+me\s+all\b|\bsee\s+them\s+all\b|\blist\s+all\b"
    r"|\ball\s+of\s+it\b|\bshow\s+all\b|\bsee\s+all\b|^\s*all\s*$"
    r"|\bthose\b|\bthem\b"
)


def _memory_path(chat_id: str) -> Path:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    return MEMORY_DIR / f"{chat_id}.json"


def _load_memory_raw(chat_id: str) -> Dict[str, object]:
    """Load raw memory without applying expiration logic."""
    path = _memory_path(chat_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def load_memory(chat_id: str) -> Dict[str, object]:
    """Load memory and auto-clear stale sessions older than DEFAULT_MEMORY_TTL_DAYS."""
    if is_session_expired(chat_id, DEFAULT_MEMORY_TTL_DAYS):
        clear_session_memory(chat_id)
        return {}
    return _load_memory_raw(chat_id)


def save_memory(chat_id: str, data: Dict[str, object]) -> None:
    """Persist memory dict for this chat as a JSON file."""
    path = _memory_path(chat_id)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def update_search_memory(
    chat_id: str,
    intent: str,
    results: List[str],
    color: str | None = None,
    gender: str | None = None,
    brand: str | None = None,
    category: str | None = None,
    original_message: str = "",
) -> None:
    """
    Save the last search context so subsequent pronoun queries ("show me all of them")
    can replay it with an expanded top_k.
    Only called after product-search intents that returned results.
    """
    data = load_memory(chat_id)
    data["last_intent"] = intent
    data["last_results"] = results
    data["last_color"] = color
    data["last_gender"] = gender
    data["last_brand"] = brand
    data["last_category"] = category
    data["last_message"] = original_message
    data["updated_at"] = int(time.time())
    save_memory(chat_id, data)


def is_pronoun_query(text: str) -> bool:
    """Return True if the message is a pronoun reference like 'show me all of them'."""
    return bool(_PRONOUN_PATTERN.search(text.lower().strip()))


def has_stored_search(chat_id: str) -> bool:
    """Return True if there is at least one saved search for this chat."""
    mem = load_memory(chat_id)
    return bool(mem.get("last_intent")) and bool(mem.get("last_results"))


def get_stored_context(chat_id: str) -> Dict[str, object]:
    """
    Return the stored search context dict with keys:
      last_intent, last_message, last_color, last_gender, last_brand, last_category
    Returns empty dict if no stored search.
    """
    mem = load_memory(chat_id)
    return {
        "last_intent": mem.get("last_intent"),
        "last_message": mem.get("last_message", ""),
        "last_color": mem.get("last_color"),
        "last_gender": mem.get("last_gender"),
        "last_brand": mem.get("last_brand"),
        "last_category": mem.get("last_category"),
    }


def is_session_expired(chat_id: str, days_threshold: int = 7) -> bool:
    """
    Return True if the session memory is older than days_threshold days.
    This helps clear stale conversation context when users return after a long break.
    """
    mem = _load_memory_raw(chat_id)
    if not mem or "updated_at" not in mem:
        return False
    
    last_update = mem.get("updated_at", 0)
    current_time = int(time.time())
    seconds_elapsed = current_time - last_update
    seconds_threshold = days_threshold * 24 * 60 * 60
    
    return seconds_elapsed > seconds_threshold


def clear_session_memory(chat_id: str) -> None:
    """
    Clear all conversation memory for a chat, starting fresh.
    Used when a session expires after one week without interaction.
    """
    save_memory(chat_id, {})


def save_delivery_profile(chat_id: str, profile: Dict[str, str]) -> None:
    """Persist delivery location details that survive session resets."""
    data = load_memory(chat_id)
    # Keep only non-empty string fields.
    cleaned = {
        str(key): str(value).strip()
        for key, value in profile.items()
        if str(value).strip()
    }
    data["saved_delivery_profile"] = cleaned
    data["updated_at"] = int(time.time())
    save_memory(chat_id, data)


def load_delivery_profile(chat_id: str) -> Dict[str, str]:
    """Load saved delivery location details, if any."""
    data = load_memory(chat_id)
    raw = data.get("saved_delivery_profile", {})
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in raw.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def reset_session_preserve_profile(chat_id: str) -> None:
    """Clear volatile session state while keeping saved delivery location."""
    profile = load_delivery_profile(chat_id)
    payload: Dict[str, object] = {}
    if profile:
        payload["saved_delivery_profile"] = profile
        payload["updated_at"] = int(time.time())
    save_memory(chat_id, payload)


def validate_and_load_memory(chat_id: str, days_threshold: int = 7) -> Dict[str, object]:
    """
    Load memory for a chat, automatically clearing it if it's older than days_threshold.
    This ensures users start fresh after a long break, but remembers recent conversations.
    """
    if is_session_expired(chat_id, days_threshold):
        clear_session_memory(chat_id)
        return {}
    return load_memory(chat_id)
