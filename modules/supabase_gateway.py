from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, cast


@dataclass
class OrderInsertResult:
    """Result of an order insert operation."""
    success: bool
    order_id: Optional[int] = None
    error_message: str = ""


@dataclass
class OrderItemInsertResult:
    """Result of order items insert operation."""
    success: bool
    item_count: int = 0
    error_message: str = ""


class SupabaseClient:
    """Direct HTTP client for Supabase REST API with service-role authentication."""

    def __init__(self, supabase_url: str, service_role_key: str) -> None:
        """
        Initialize Supabase client.

        Args:
            supabase_url: Your Supabase project URL (e.g., https://xxx.supabase.co)
            service_role_key: Supabase service role key (for server-side operations)
        """
        self.supabase_url = supabase_url.rstrip("/")
        self.service_role_key = service_role_key
        self.base_url = f"{self.supabase_url}/rest/v1"
        self.timeout = 10

    def _make_request(
        self,
        method: str,
        endpoint: str,
        payload: Optional[Dict[str, Any] | list[Dict[str, Any]]] = None,
        query_params: Optional[Dict[str, str]] = None,
    ) -> Any:
        """
        Make an authenticated HTTP request to Supabase REST API.

        Args:
            method: HTTP method (GET, POST, PATCH, DELETE)
            endpoint: Table name or path (e.g., "orders", "order_items")
            payload: Request body as dict or list (auto-JSON encoded)
            query_params: Query string parameters

        Returns:
            Parsed JSON response or empty dict on error

        Raises:
            RuntimeError: On HTTP or network errors
        """
        url = f"{self.base_url}/{endpoint}"

        if query_params:
            query_string = urllib.parse.urlencode(query_params)
            url = f"{url}?{query_string}"

        headers = {
            "Authorization": f"Bearer {self.service_role_key}",
            "Content-Type": "application/json",
            "apikey": self.service_role_key,
            "Prefer": "return=representation",
        }

        body = None
        if payload:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        request = urllib.request.Request(
            url=url,
            data=body,
            headers=headers,
            method=method,
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw_body = response.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8")
            raise RuntimeError(f"Supabase HTTP {e.code}: {error_body}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise RuntimeError(f"Supabase network error: {e}") from e

        try:
            return json.loads(raw_body) if raw_body else {}
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Invalid JSON response from Supabase: {raw_body}") from e

    def create_order(
        self,
        customer_name: str,
        customer_email: str,
        customer_phone: str,
        governorate: str,
        district: str,
        village: str,
        address_details: str,
        total: float,
        order_date: str,
    ) -> OrderInsertResult:
        """
        Insert a new order into the orders table.

        Args:
            customer_name: Customer's full name
            customer_email: Customer's email
            customer_phone: Customer's phone
            governorate: Governorate
            district: District
            village: Village
            address_details: Address details/street/building
            total: Order total (subtotal + delivery)
            order_date: ISO date string (e.g., "2026-03-18")

        Returns:
            OrderInsertResult with order_id if successful
        """
        payload: Dict[str, Any] = {
            "customer_name": customer_name,
            "customer_email": customer_email,
            "customer_phone": customer_phone,
            "governorate": governorate,
            "district": district,
            "village": village,
            "address_details": address_details,
            "total": total,
            "status": "pending",
            "date": order_date,
            "created_at": f"{order_date}T{time.strftime('%H:%M:%S')}Z",
        }

        try:
            response = self._make_request("POST", "orders", payload=payload)

            # Supabase returns an array with the inserted row(s)
            if isinstance(response, list) and response:
                order_row = cast(Dict[str, Any], response[0])
                order_id_raw = order_row.get("id")
                if order_id_raw is None:
                    order_id = None
                else:
                    try:
                        order_id = int(order_id_raw)
                    except (TypeError, ValueError):
                        order_id = None
                if order_id:
                    return OrderInsertResult(success=True, order_id=order_id)

            return OrderInsertResult(
                success=False,
                error_message="No order ID in response",
            )
        except Exception as e:
            return OrderInsertResult(
                success=False,
                error_message=f"Failed to create order: {str(e)}",
            )

    def create_order_items(
        self,
        order_id: int,
        items: List[Dict[str, Any]],
    ) -> OrderItemInsertResult:
        """
        Insert order items into the order_items table.

        Args:
            order_id: The order ID (foreign key to orders.id)
            items: List of items, each with keys: product_id, product_name, quantity, size, price

        Returns:
            OrderItemInsertResult with count of inserted items
        """
        if not items:
            return OrderItemInsertResult(success=True, item_count=0)

        payload_list: list[Dict[str, Any]] = []
        for item in items:
            payload_list.append(
                {
                    "order_id": order_id,
                    "product_id": item.get("product_id"),
                    "product_name": item.get("product_name", ""),
                    "quantity": item.get("quantity", 1),
                    "size": item.get("size", ""),
                    "price": item.get("price", 0.0),
                    "created_at": f"{time.strftime('%Y-%m-%dT%H:%M:%SZ')}",
                }
            )

        try:
            response = self._make_request("POST", "order_items", payload=payload_list)

            # Supabase returns array of inserted rows
            if isinstance(response, list):
                inserted_rows = cast(List[Dict[str, Any]], response)
                return OrderItemInsertResult(success=True, item_count=len(inserted_rows))

            return OrderItemInsertResult(
                success=False,
                error_message="Invalid response format for order_items",
            )
        except Exception as e:
            return OrderItemInsertResult(
                success=False,
                error_message=f"Failed to create order items: {str(e)}",
            )

    def update_order_status(self, order_id: int, status: str) -> bool:
        """
        Update the status of an existing order.

        Args:
            order_id: The order ID
            status: New status string (e.g., "confirmed", "shipped", "cancelled")

        Returns:
            True if successful, False otherwise
        """
        payload = {"status": status, "updated_at": f"{time.strftime('%Y-%m-%dT%H:%M:%SZ')}"}

        try:
            self._make_request(
                "PATCH",
                "orders",
                payload=payload,
                query_params={"id": f"eq.{order_id}"},
            )
            return True
        except Exception:
            return False


def get_supabase_client() -> Optional[SupabaseClient]:
    """
    Get a configured Supabase client from environment variables.
    Returns None if credentials are missing.
    """
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

    if not url or not key:
        return None

    return SupabaseClient(supabase_url=url, service_role_key=key)
