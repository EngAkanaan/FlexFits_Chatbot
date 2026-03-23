from __future__ import annotations

from telegram_bot import (
    STATE_BROWSING,
    STATE_CART,
    STATE_CHECKOUT,
    STATE_ORDER_CONFIRMATION,
    TelegramBotRunner,
    _extract_structured_request,
    _resolve_cart_item_from_snippets,
)


def test_extract_structured_request_new_balance_574_size_42() -> None:
    data = _extract_structured_request("new balance 574 size 42")
    assert data["brand"] == "new balance"
    assert data["model"] == "574"
    assert data["size"] == "42"


def test_add_it_to_cart_uses_previous_top_result() -> None:
    snippets = [
        "newbalance-574 | New Balance 574M | type: Casual | price: $39 | sizes: [42, 42.5] | status: in_stock",
        "newbalance-997h | New Balance 997H | type: Casual | price: $39 | sizes: [40] | status: in_stock",
    ]

    selected = _resolve_cart_item_from_snippets("add it to my cart", snippets)
    assert selected == "New Balance 574M (Casual) $39"


def test_finish_order_shows_summary() -> None:
    runner = TelegramBotRunner(token="fake-token")
    sent_messages: list[str] = []

    # Avoid real Telegram/network side effects in tests.
    runner.send_message = lambda chat_id, text: sent_messages.append(text)  # type: ignore[method-assign]
    runner._append_chat_log = lambda chat_id, role, text: None  # type: ignore[method-assign]

    chat_id = 123
    state = runner._get_chat_state(chat_id)
    state.bag = ["New Balance 574M (Casual) $39"]

    update = {
        "message": {
            "chat": {"id": chat_id},
            "text": "finish my order",
        }
    }
    runner.process_update(update)

    assert sent_messages, "Expected checkout prompt to be sent"
    assert "full name" in sent_messages[-1].lower()


def test_checkout_state_machine_collects_fields_then_asks_confirmation() -> None:
    runner = TelegramBotRunner(token="fake-token")
    sent_messages: list[str] = []

    runner.send_message = lambda chat_id, text: sent_messages.append(text)  # type: ignore[method-assign]
    runner._append_chat_log = lambda chat_id, role, text: None  # type: ignore[method-assign]

    chat_id = 555
    state = runner._get_chat_state(chat_id)
    state.bag = ["Adidas TERREX (Hiking Shoes) $80"]

    # Enter checkout
    runner.process_update({"message": {"chat": {"id": chat_id}, "text": "checkout"}})
    assert state.conversation_state == STATE_CHECKOUT
    assert state.order_stage == "name"

    # Fill 5 required fields
    runner.process_update({"message": {"chat": {"id": chat_id}, "text": "John Doe"}})
    runner.process_update({"message": {"chat": {"id": chat_id}, "text": "Mount Lebanon"}})
    runner.process_update({"message": {"chat": {"id": chat_id}, "text": "Jounieh"}})
    runner.process_update({"message": {"chat": {"id": chat_id}, "text": "Sahel Alma"}})
    runner.process_update({"message": {"chat": {"id": chat_id}, "text": "Main street, building 2"}})

    assert sent_messages
    summary = sent_messages[-1]
    assert "Order Summary" in summary
    assert "Subtotal: $80.00" in summary
    assert "Delivery: $4.00" in summary
    assert "Total: $84.00" in summary
    assert "Confirm order? (yes/no)" in summary
    assert state.order_stage == "confirm_order"
    assert state.conversation_state == STATE_ORDER_CONFIRMATION


def test_confirm_order_yes_clears_cart() -> None:
    runner = TelegramBotRunner(token="fake-token")
    sent_messages: list[str] = []

    runner.send_message = lambda chat_id, text: sent_messages.append(text)  # type: ignore[method-assign]
    runner._append_chat_log = lambda chat_id, role, text: None  # type: ignore[method-assign]
    runner._send_order_to_admin = lambda chat_id, order_data: None  # type: ignore[method-assign]

    chat_id = 556
    state = runner._get_chat_state(chat_id)
    state.bag = ["Adidas TERREX (Hiking Shoes) $80"]

    # Enter and complete checkout collection quickly.
    for text in [
        "checkout",
        "John Doe",
        "Mount Lebanon",
        "Jounieh",
        "Sahel Alma",
        "Main street, building 2",
        "yes",
    ]:
        runner.process_update({"message": {"chat": {"id": chat_id}, "text": text}})

    assert sent_messages
    assert "order has been confirmed" in sent_messages[-1].lower()
    assert state.bag == []
    assert state.order_stage is None


def test_search_single_match_shows_filtered_results() -> None:
    runner = TelegramBotRunner(token="fake-token")
    sent_messages: list[str] = []

    runner.send_message = lambda chat_id, text: sent_messages.append(text)  # type: ignore[method-assign]
    runner._append_chat_log = lambda chat_id, role, text: None  # type: ignore[method-assign]
    runner._retrieve_strict_candidates = lambda user_text, chat_state, top_k=20: [  # type: ignore[method-assign]
        "adidas-campus | Adidas Campus Gray | type: Casual | price: $75 | sizes: [42] | status: in_stock"
    ]

    update = {"message": {"chat": {"id": 1}, "text": "adidas size 42"}}
    runner.process_update(update)

    assert sent_messages
    assert "I found" in sent_messages[-1]
    assert "Adidas Campus Gray" in sent_messages[-1]
    assert "add it to cart" in sent_messages[-1].lower()


def test_search_multiple_matches_shows_numbered_results() -> None:
    runner = TelegramBotRunner(token="fake-token")
    sent_messages: list[str] = []

    runner.send_message = lambda chat_id, text: sent_messages.append(text)  # type: ignore[method-assign]
    runner._append_chat_log = lambda chat_id, role, text: None  # type: ignore[method-assign]
    runner._retrieve_strict_candidates = lambda user_text, chat_state, top_k=20: [  # type: ignore[method-assign]
        "adidas-campus | Adidas Campus Gray | type: Casual | price: $75 | sizes: [42] | status: in_stock",
        "adidas-gazelle | Adidas Gazelle Brown | type: Casual | price: $80 | sizes: [42] | status: in_stock",
    ]

    update = {"message": {"chat": {"id": 1}, "text": "adidas size 42"}}
    runner.process_update(update)

    assert sent_messages
    assert "I found" in sent_messages[-1]
    assert "1. Adidas Campus Gray" in sent_messages[-1]
    assert "2. Adidas Gazelle Brown" in sent_messages[-1]


def test_specific_size_query_uses_close_size_window_wording() -> None:
    runner = TelegramBotRunner(token="fake-token")
    sent_messages: list[str] = []

    runner.send_message = lambda chat_id, text: sent_messages.append(text)  # type: ignore[method-assign]
    runner._append_chat_log = lambda chat_id, role, text: None  # type: ignore[method-assign]
    runner._retrieve_strict_candidates = lambda user_text, chat_state, top_k=20: [  # type: ignore[method-assign]
        "adidas-runfalcon5 | Adidas Run Falcon 5 | type: Running Shoes | color: Black | price: $70 | sizes: ['41', '42.5'] | status: in_stock",
        "adidas-vlcourt | Adidas VL Court Base Black | type: Casual | color: Black | price: $75 | sizes: ['43.3'] | status: in_stock",
    ]

    runner.process_update({"message": {"chat": {"id": 1}, "text": "adidas size 42"}})

    assert sent_messages
    assert "Adidas size 42 is currently out of stock" in sent_messages[-1]
    assert "close sizes available" in sent_messages[-1]
    assert "Adidas Run Falcon 5" in sent_messages[-1]


def test_size_only_query_no_stock_returns_generic_message() -> None:
    runner = TelegramBotRunner(token="fake-token")
    sent_messages: list[str] = []

    runner.send_message = lambda chat_id, text: sent_messages.append(text)  # type: ignore[method-assign]
    runner._append_chat_log = lambda chat_id, role, text: None  # type: ignore[method-assign]
    runner._retrieve_strict_candidates = lambda user_text, chat_state, top_k=20: []  # type: ignore[method-assign]

    runner.process_update({"message": {"chat": {"id": 1}, "text": "do you have size 47?"}})

    assert sent_messages
    assert "don't have sneakers available in size 47" in sent_messages[-1]


def test_size_followup_uses_remembered_brand_from_memory() -> None:
    runner = TelegramBotRunner(token="fake-token")
    sent_messages: list[str] = []
    seen_queries: list[str] = []

    runner.send_message = lambda chat_id, text: sent_messages.append(text)  # type: ignore[method-assign]
    runner._append_chat_log = lambda chat_id, role, text: None  # type: ignore[method-assign]

    def _fake_retrieve(user_text: str, chat_state, top_k=20):  # type: ignore[no-untyped-def]
        seen_queries.append(user_text)
        if "adidas size 42" in user_text:
            return [
                "adidas-runfalcon5 | Adidas Run Falcon 5 | type: Running Shoes | color: Black | price: $70 | sizes: ['41', '42.5'] | status: in_stock",
            ]
        if "adidas size 42.5" in user_text:
            return [
                "adidas-runfalcon5 | Adidas Run Falcon 5 | type: Running Shoes | color: Black | price: $70 | sizes: ['42.5'] | status: in_stock",
            ]
        return []

    runner._retrieve_strict_candidates = _fake_retrieve  # type: ignore[method-assign]

    runner.process_update({"message": {"chat": {"id": 1}, "text": "adidas size 42"}})
    runner.process_update({"message": {"chat": {"id": 1}, "text": "42,5"}})

    assert any("adidas size 42.5" in query for query in seen_queries)


def test_add_to_cart_confirmation_moves_state_to_cart() -> None:
    runner = TelegramBotRunner(token="fake-token")
    sent_messages: list[str] = []

    runner.send_message = lambda chat_id, text: sent_messages.append(text)  # type: ignore[method-assign]
    runner._append_chat_log = lambda chat_id, role, text: None  # type: ignore[method-assign]
    runner._retrieve_strict_candidates = lambda user_text, chat_state, top_k=20: [  # type: ignore[method-assign]
        "puma-court-shatter-low | Puma Court Shatter Low | type: Casual | color: White | price: $65 | sizes: ['42'] | status: low_stock"
    ]

    chat_id = 2
    runner.process_update({"message": {"chat": {"id": chat_id}, "text": "add to cart puma"}})
    runner.process_update({"message": {"chat": {"id": chat_id}, "text": "yes"}})

    state = runner._get_chat_state(chat_id)
    assert state.conversation_state == STATE_CART


def test_add_to_cart_single_match_asks_confirmation() -> None:
    runner = TelegramBotRunner(token="fake-token")
    sent_messages: list[str] = []

    runner.send_message = lambda chat_id, text: sent_messages.append(text)  # type: ignore[method-assign]
    runner._append_chat_log = lambda chat_id, role, text: None  # type: ignore[method-assign]
    runner._retrieve_strict_candidates = lambda user_text, chat_state, top_k=20: [  # type: ignore[method-assign]
        "puma-court-shatter-low | Puma Court Shatter Low | type: Casual | price: $65 | sizes: [43] | status: low_stock"
    ]

    update = {"message": {"chat": {"id": 1}, "text": "add to cart puma white"}}
    runner.process_update(update)

    assert sent_messages
    assert "Do you want me to add" in sent_messages[-1]
    assert "Puma Court Shatter Low" in sent_messages[-1]


def test_add_to_cart_multiple_matches_asks_choice() -> None:
    runner = TelegramBotRunner(token="fake-token")
    sent_messages: list[str] = []

    runner.send_message = lambda chat_id, text: sent_messages.append(text)  # type: ignore[method-assign]
    runner._append_chat_log = lambda chat_id, role, text: None  # type: ignore[method-assign]
    runner._retrieve_strict_candidates = lambda user_text, chat_state, top_k=20: [  # type: ignore[method-assign]
        "puma-court-shatter-low | Puma Court Shatter Low | type: Casual | price: $65 | sizes: [43] | status: low_stock",
        "puma-black | Puma Black | type: Running Shoes | price: $39 | sizes: [40] | status: in_stock",
    ]

    update = {"message": {"chat": {"id": 1}, "text": "add to cart puma"}}
    runner.process_update(update)

    assert sent_messages
    assert "Which one would you like to add to your cart?" in sent_messages[-1]
    assert "1. Puma Court Shatter Low" in sent_messages[-1]
    assert "2. Puma Black" in sent_messages[-1]
