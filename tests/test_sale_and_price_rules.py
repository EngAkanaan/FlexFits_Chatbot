from __future__ import annotations

from pathlib import Path

from modules.retriever import Retriever


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_sale_query_returns_only_price_39_and_in_stock(tmp_path: Path) -> None:
    faq = tmp_path / "faq.md"
    inventory = tmp_path / "inventory.json"

    _write_text(faq, "# FAQ\n\n## Sale Items Policy\n- Sale item price is 39.\n")
    _write_text(
        inventory,
        """[
  {
    "id": "sale-in-stock",
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
    "id": "sale-out-stock",
    "brand": "Hoka",
    "model": "Arhi 6",
    "type": "Running Shoes",
    "color": "Black",
    "sizes": [42],
    "price": 39,
    "stock_by_size": {"42": 0},
    "items_sold": 2,
    "status": "out_of_stock"
  },
  {
    "id": "nonsale-in-stock",
    "brand": "Nike",
    "model": "Court Vision Low FW",
    "type": "Casual",
    "color": "White",
    "sizes": [42],
    "price": 80,
    "stock_by_size": {"42": 1},
    "items_sold": 3,
    "status": "in_stock"
  }
]
""",
    )

    retriever = Retriever(faq_path=faq, inventory_path=inventory)
    snippets = retriever.retrieve_inventory_snippets(
        user_message="show me sale shoes",
        intent="sale_query",
        top_k=5,
    )

    assert len(snippets) == 1
    assert "price: $39" in snippets[0]
    assert "status: in_stock" in snippets[0] or "status: low_stock" in snippets[0]
    assert "nonsale-in-stock" not in snippets[0]


def test_non_sale_query_can_return_non_sale_in_stock_items(tmp_path: Path) -> None:
    faq = tmp_path / "faq.md"
    inventory = tmp_path / "inventory.json"

    _write_text(faq, "# FAQ\n\n## Availability And Sizing\n- In-stock only.\n")
    _write_text(
        inventory,
        """[
  {
    "id": "adidas-runfalcon5",
    "brand": "Adidas",
    "model": "Run Falcon 5",
    "type": "Running Shoes",
    "color": "Black",
    "sizes": [42.5],
    "price": 70,
    "stock_by_size": {"42.5": 1},
    "items_sold": 8,
    "status": "in_stock"
  }
]
""",
    )

    retriever = Retriever(faq_path=faq, inventory_path=inventory)
    snippets = retriever.retrieve_inventory_snippets(
        user_message="adidas running shoes",
        intent="search_product",
        top_k=3,
    )

    assert len(snippets) == 1
    assert "adidas-runfalcon5" in snippets[0]
    assert "price: $70" in snippets[0]


def test_sale_show_all_returns_only_price_39(tmp_path: Path) -> None:
    faq = tmp_path / "faq.md"
    inventory = tmp_path / "inventory.json"

    _write_text(faq, "# FAQ\n\n## Sale Items Policy\n- Sale item price is 39.\n")
    _write_text(
        inventory,
        """[
  {
    "id": "sale-running",
    "brand": "Asics",
    "model": "R1",
    "type": "Running Shoes",
    "color": "Black",
    "sizes": [42],
    "price": 39,
    "stock_by_size": {"42": 1},
    "items_sold": 4,
    "status": "in_stock"
  },
  {
    "id": "sale-casual",
    "brand": "Puma",
    "model": "C1",
    "type": "Casual",
    "color": "White",
    "sizes": [42],
    "price": 39,
    "stock_by_size": {"42": 1},
    "items_sold": 2,
    "status": "in_stock"
  },
  {
    "id": "not-sale",
    "brand": "Nike",
    "model": "N1",
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
    snippets = retriever.retrieve_inventory_snippets(
        user_message="show all current shoes priced 39 on sale",
        intent="sale_query",
        top_k=30,
    )

    assert snippets
    assert all("price: $39" in item for item in snippets)
    assert not any("not-sale" in item for item in snippets)
