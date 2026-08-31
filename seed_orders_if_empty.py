"""Operational tool: check whether order_header has any rows, and if not,
add 50 valid test orders built strictly from the customers/items that
already exist. No-op (and makes no changes) if orders already exist.
Moved here from the backend with order_header/order_details.

    .venv/Scripts/python seed_orders_if_empty.py
"""
from app.db import session_scope
from app.services.order_seed import ensure_test_orders

with session_scope() as s:
    created = ensure_test_orders(s, minimum=50)

if created:
    print(f"order_header was empty - seeded {len(created)} test orders: "
         f"{created[0]}..{created[-1]}")
else:
    print("orders already exist - no seeding performed")
