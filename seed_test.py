"""Seed the isolated catalog_test schema with the same baseline dummy
data the backend's tests have always relied on - moved here from the
backend's own seed_test.py now that these tables live here.

Run against the test schema only - never against the dev database:

    DATABASE_URL='postgresql+psycopg://voiceorder:changeme@localhost/voiceorder?options=-c%20search_path%3Dcatalog_test%2Cpublic' \
        .venv/Scripts/python seed_test.py

Deliberately includes a few edge cases so tests have real fixtures for them:
  - C003 has no email on file
  - order 990000003 has zero lines
  - order 990000004 belongs to C002, used to exercise the "wrong customer"
    mismatch path against C001
"""
from app.db import session_scope
from app.models import Customer, Item, OrderDetail, OrderHeader

ITEMS = [
    ("A100", "Blue Paint 5L", "Paint"),
    ("A101", "White Paint 5L", "Paint"),
    ("B200", "Paint Brush 2 inch", "Tools"),
    ("B201", "Paint Roller Large", "Tools"),
    ("C300", "Masking Tape 50m", "Consumables"),
    ("I999", "Discontinued Sample Item", "Misc"),
]

CUSTOMERS = [
    ("C001", "Test Trading", "orders+c001@testtrading.example",
     "03123456", "Beirut", "Hamra"),
    ("C002", "Beirut Hardware Co", "purchasing@beiruthardware.example",
     "01654321", "Beirut", "Verdun"),
    ("C003", "Zahle Paint Supply", None,
     "08765432", "Zahle", "Main Street"),
]

with session_scope() as s:
    for nb, name, email, tel, city, addr in CUSTOMERS:
        s.add(Customer(customer_number=nb, customer_name=name, email=email,
                       telephone=tel, city=city, address1=addr))

    for nb, desc, cat in ITEMS:
        s.add(Item(item_number=nb, item_desc=desc, category=cat))

    s.add(OrderHeader(order_nb="990000001", order_type="SO", cust_nb="C001"))
    s.add(OrderDetail(order_nb="990000001", order_type="SO", line_nb=1,
                      item_nb="A100", item_desc="Blue Paint 5L", qty=3,
                      uom="PCS"))
    s.add(OrderDetail(order_nb="990000001", order_type="SO", line_nb=2,
                      item_nb="B200", item_desc="Paint Brush 2 inch", qty=2,
                      uom="PCS"))

    s.add(OrderHeader(order_nb="990000002", order_type="SO", cust_nb="C001"))
    s.add(OrderDetail(order_nb="990000002", order_type="SO", line_nb=1,
                      item_nb="I999", item_desc="Discontinued Sample Item",
                      qty=1, uom="PCS"))

    s.add(OrderHeader(order_nb="990000003", order_type="SO", cust_nb="C001"))

    s.add(OrderHeader(order_nb="990000004", order_type="SO", cust_nb="C002"))
    s.add(OrderDetail(order_nb="990000004", order_type="SO", line_nb=1,
                      item_nb="C300", item_desc="Masking Tape 50m", qty=5,
                      uom="PCS"))

print("seeded catalog_test")
