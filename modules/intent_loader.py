from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parents[1]
INTENTS_FILE = BASE_DIR / "data" / "intents.xml"


class IntentLoader:
    """Load and cache intent definitions from XML."""

    def __init__(self, xml_path: Path | None = None) -> None:
        self.xml_path = xml_path or INTENTS_FILE
        self._cache: Dict[str, Dict[str, object]] = {}
        self._triggers_flat: List[tuple[str, List[str]]] = []  # (intent_name, [patterns])
        self.load()

    def load(self) -> None:
        """Load and cache intents from XML."""
        if not self.xml_path.exists():
            return

        try:
            tree = ET.parse(self.xml_path)
            root = tree.getroot()
        except ET.ParseError:
            return

        for intent_elem in root.findall("intent"):
            name = intent_elem.get("name", "").strip()
            if not name:
                continue

            display = intent_elem.get("display_name", name)
            response_elem = intent_elem.find("response")
            response = response_elem.text.strip() if response_elem is not None and response_elem.text else ""

            triggers_elem = intent_elem.find("triggers")
            patterns = []
            if triggers_elem is not None:
                for pattern_elem in triggers_elem.findall("pattern"):
                    if pattern_elem.text:
                        patterns.append(pattern_elem.text.strip().lower())

            self._cache[name] = {
                "name": name,
                "display_name": display,
                "patterns": patterns,
                "response": response,
            }
            self._triggers_flat.append((name, patterns))

    def get_intent(self, name: str) -> Dict[str, object] | None:
        """Return intent metadata by name, or None."""
        return self._cache.get(name.strip())

    def get_response(self, name: str) -> str:
        """Return fallback response for an intent, or empty string."""
        intent = self.get_intent(name)
        if intent:
            return str(intent.get("response", ""))
        return ""

    def has_intent(self, name: str) -> bool:
        """Return True if intent exists."""
        return name.strip() in self._cache

    def all_intents(self) -> List[str]:
        """Return list of all loaded intent names."""
        return list(self._cache.keys())

    def match_pattern(self, text: str) -> Optional[str]:
        """Try to match text against all intent patterns and pick the most specific match."""
        lowered = text.strip().lower()
        best_intent: Optional[str] = None
        best_len = -1
        for intent_name, patterns in self._triggers_flat:
            for pattern in patterns:
                if pattern in lowered:
                    pattern_len = len(pattern)
                    if pattern_len > best_len:
                        best_len = pattern_len
                        best_intent = intent_name
        return best_intent


# Singleton instance
_loader: IntentLoader | None = None


def get_intent_loader() -> IntentLoader:
    """Get or create the global intent loader."""
    global _loader
    if _loader is None:
        _loader = IntentLoader()
    return _loader
