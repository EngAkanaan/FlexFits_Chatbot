from __future__ import annotations

from pathlib import Path

from modules.rag_service import RagService
from modules.retriever import Retriever


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_rag_service_returns_policy_metadata_and_context(tmp_path: Path) -> None:
    faq = tmp_path / "faq.md"
    inventory = tmp_path / "inventory.json"

    _write_text(
        faq,
        """# FAQ\n\n## Sale Items Policy\n- Sale item price is 39.\n\n## Delivery In Lebanon\n- Delivery add-on is $4.\n""",
    )
    _write_text(
        inventory,
        """[
  {
    "id": "asics-gelnimbus23",
    "brand": "Asics",
    "model": "GEL-Nimbus 23",
    "type": "Running Shoes",
    "color": "Black",
    "sizes": [42],
    "price": 39,
    "stock_by_size": {"42": 1},
    "items_sold": 4,
    "status": "in_stock"
  },
  {
    "id": "nike-courtvisionlow-fw",
    "brand": "Nike",
    "model": "Court Vision Low FW",
    "type": "Casual",
    "color": "White",
    "sizes": [42],
    "price": 80,
    "stock_by_size": {"42": 1},
    "items_sold": 2,
    "status": "in_stock"
  }
]
""",
    )

    retriever = Retriever(faq_path=faq, inventory_path=inventory)
    service = RagService(retriever=retriever)

    result = service.generate_support_reply(
        user_message="show me sale shoes",
        intent="sale_query",
        state_entities={"brand": "Asics"},
    )

    assert result["metadata"]["intent"] == "sale_query"
    assert result["metadata"]["policy_file"] == "sale_policy.xml"
    assert result["metadata"]["inventory_chunk_count"] >= 1
    assert len(result["context_chunks"]) >= 1
    assert "sale" in result["reply"].lower()


def test_rag_service_blocks_inventory_for_delivery_intent(tmp_path: Path) -> None:
    faq = tmp_path / "faq.md"
    inventory = tmp_path / "inventory.json"

    _write_text(
        faq,
        """# FAQ\n\n## Delivery In Lebanon\n- Delivery add-on is $4.\n""",
    )
    _write_text(
        inventory,
        """[
  {
    "id": "adidas-galaxy-6",
    "brand": "Adidas",
    "model": "Galaxy 6",
    "type": "Running Shoes",
    "color": "Black",
    "sizes": [38],
    "price": 60,
    "stock_by_size": {"38": 1},
    "items_sold": 1,
    "status": "in_stock"
  }
]
""",
    )

    retriever = Retriever(faq_path=faq, inventory_path=inventory)
    service = RagService(retriever=retriever)

    result = service.generate_support_reply(
        user_message="how much is delivery",
        intent="delivery_info",
        state_entities={},
    )

    assert result["metadata"]["intent"] == "delivery_info"
    assert result["metadata"]["inventory_chunk_count"] == 0
    assert "delivery" in result["reply"].lower()
