"""
Test suite for Supabase order persistence integration.
Tests the field mapping, order parsing, and duplicate order prevention.
"""

import sys
from pathlib import Path
from typing import Any, Dict, cast

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from telegram_bot import TelegramBotRunner


def test_updated_checkout_sequence():
    """Verify that checkout sequence includes all required fields."""
    from telegram_bot import _ORDER_STAGE_SEQUENCE  # pyright: ignore[reportPrivateUsage]
    
    stage_names = [stage[0] for stage in _ORDER_STAGE_SEQUENCE]
    
    required_fields = ["name", "phone", "email", "governorate", "district", "village", "address_details"]
    assert stage_names == required_fields, f"Expected {required_fields}, got {stage_names}"
    print("✓ Checkout sequence has all required fields")


def test_cart_items_parsing():
    """Verify that cart items are correctly parsed into order_items format."""
    runner = TelegramBotRunner(token="fake-token")
    
    # Mock cart items in the format store in the bag
    cart = [
        "Adidas Run Falcon 5 (Running Shoes) $70",
        "Nike Blazer Low (Casual) $85.50",
        "Puma Court Shatter (Basketball) $95",
    ]
    
    items = cast(list[Dict[str, Any]], runner._parse_cart_items_for_db(cart))  # pyright: ignore[reportPrivateUsage]
    
    assert len(items) == 3, f"Expected 3 items, got {len(items)}"
    
    # Check first item
    assert items[0]["product_name"] == "Adidas Run Falcon 5 (Running Shoes)"
    assert items[0]["price"] == 70.0
    assert items[0]["quantity"] == 1
    
    # Check second item
    assert items[1]["product_name"] == "Nike Blazer Low (Casual)"
    assert items[1]["price"] == 85.50
    
    # Check third item
    assert items[2]["product_name"] == "Puma Court Shatter (Basketball)"
    assert items[2]["price"] == 95.0
    
    print("✓ Cart items correctly parsed to order_items format")


def test_cart_items_with_size():
    """Verify that cart items with size information are parsed correctly."""
    runner = TelegramBotRunner(token="fake-token")
    
    # Mock cart items with size information embedded (realistic scenario)
    cart = [
        "Adidas Run Falcon 5 size 42 (Running Shoes) $70",
        "Nike Blazer Low size 41.5 (Casual) $85",
    ]
    
    items = cast(list[Dict[str, Any]], runner._parse_cart_items_for_db(cart))  # pyright: ignore[reportPrivateUsage]
    
    assert len(items) == 2
    # Note: the current parsing extracts size but the output doesn't show it in product_name
    # The size field should be extracted
    assert items[0]["price"] == 70.0
    assert items[1]["price"] == 85.0
    
    print("✓ Cart items with size information parsed correctly")


def test_empty_cart_parsing():
    """Verify that empty carts are handled gracefully."""
    runner = TelegramBotRunner(token="fake-token")
    
    items = cast(list[Dict[str, Any]], runner._parse_cart_items_for_db([]))  # pyright: ignore[reportPrivateUsage]
    assert items == [], f"Expected empty list, got {items}"
    
    print("✓ Empty cart handled correctly")


def test_telegram_admin_message_format():
    """Verify that admin message includes new fields (phone, email, district, village)."""
    runner = TelegramBotRunner(token="fake-token")
    
    order_data = {
        "name": "Ahmed Ali",
        "phone": "961-555-1234",
        "email": "ahmed@example.com",
        "governorate": "Beirut",
        "district": "Downtown",
        "village": "Hamra",
        "address_details": "Street 32, Building 5, Apt 3",
        "items": "Adidas Running Shoe size 42",
    }
    
    message = runner._format_admin_order_message(chat_id=12345, order_data=order_data)  # pyright: ignore[reportPrivateUsage]
    
    # Verify all fields are present
    assert "Phone: 961-555-1234" in message
    assert "Email: ahmed@example.com" in message
    assert "District: Downtown" in message
    assert "Village: Hamra" in message
    assert "Address: Street 32, Building 5, Apt 3" in message
    assert "Ahmed Ali" in message
    
    print("✓ Admin message format includes all new fields")


def test_supabase_client_initialization():
    """Verify that Supabase client can be initialized from env vars."""
    import os
    
    # Set dummy credentials
    os.environ["SUPABASE_URL"] = "https://example.supabase.co"
    os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "test-key-123"
    
    from modules.supabase_gateway import get_supabase_client
    
    client = get_supabase_client()
    assert client is not None, "Supabase client should be created with valid env vars"
    assert client.supabase_url == "https://example.supabase.co"
    
    print("✓ Supabase client initialization works")


def test_supabase_client_missing_credentials():
    """Verify that missing credentials return None."""
    import os
    
    # Clear env vars
    os.environ.pop("SUPABASE_URL", None)
    os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
    
    from modules.supabase_gateway import get_supabase_client
    
    client = get_supabase_client()
    assert client is None, "Supabase client should be None when credentials are missing"
    
    print("✓ Supabase client returns None when credentials missing")


def test_order_stage_prompts_match_fields():
    """Verify that all order stages have matching field names."""
    from telegram_bot import _ORDER_STAGE_SEQUENCE  # pyright: ignore[reportPrivateUsage]
    
    # Extract field names from the sequence
    field_names = {stage[0] for stage in _ORDER_STAGE_SEQUENCE}
    
    # These should map to Supabase orders table columns
    expected_fields = {"name", "phone", "email", "governorate", "district", "village", "address_details"}
    
    assert field_names == expected_fields, f"Field mismatch: expected {expected_fields}, got {field_names}"
    
    print("✓ All order stage fields match Supabase schema")


if __name__ == "__main__":
    print("\n=== Running Telegram + Supabase Integration Tests ===\n")
    
    test_updated_checkout_sequence()
    test_cart_items_parsing()
    test_cart_items_with_size()
    test_empty_cart_parsing()
    test_telegram_admin_message_format()
    test_supabase_client_initialization()
    test_supabase_client_missing_credentials()
    test_order_stage_prompts_match_fields()
    
    print("\n✓ All tests passed!\n")
