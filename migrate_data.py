"""One-off data copy: item/customer/order_header/order_details/
qra_header/qra_detail from the backend's `public` schema into this
service's `catalog` schema, run once during cutover (same physical
Postgres instance today - see the split plan).

Column lists deliberately don't include the columns already dropped from
order_header/order_details/qra_detail earlier this session (status,
created_at, unit_price, category, qra_detail_id, uom_buy, uom_get, id) -
the source public schema no longer has them either, this only exists to
document why the SELECT lists look this way rather than `SELECT *`.

Run with the venv active: python migrate_data.py
"""
from sqlalchemy import create_engine, text

SRC = "postgresql+psycopg://voiceorder:changeme@localhost/voiceorder" \
      "?options=-c%20search_path%3Dpublic"
DST = "postgresql+psycopg://voiceorder:changeme@localhost/voiceorder" \
      "?options=-c%20search_path%3Dcatalog"

TABLES = [
    ("item", ["item_number", "item_desc", "category"]),
    ("customer", ['"CustomerNumber"', '"CustomerName"', "email", "telephone",
                  '"City"', '"Address1"', '"Address2"']),
    ("order_header", ["order_nb", "order_type", "cust_nb"]),
    ("order_details", ["order_nb", "order_type", "line_nb", "item_nb",
                       "item_desc", "qty", "uom", "line_type", "is_free"]),
    ("qra_header", ["cust_nb", "from_date", "to_date", "status"]),
    ("qra_detail", ["cust_nb", "qra_type", "item_nb_buy", "item_nb_get",
                    "item_nb_price", "qty_buy", "qty_get", "qra_price"]),
]


def main():
    src_engine = create_engine(SRC)
    dst_engine = create_engine(DST)

    with src_engine.connect() as src, dst_engine.begin() as dst:
        for table, cols in TABLES:
            col_list = ", ".join(cols)
            rows = src.execute(text(f"SELECT {col_list} FROM {table}")).all()
            if not rows:
                print(f"{table}: 0 rows (nothing to copy)")
                continue
            placeholders = ", ".join(f":{i}" for i in range(len(cols)))
            stmt = text(f"INSERT INTO {table} ({col_list}) "
                       f"VALUES ({placeholders}) ON CONFLICT DO NOTHING")
            for row in rows:
                dst.execute(stmt, {str(i): v for i, v in enumerate(row)})
            print(f"{table}: {len(rows)} rows copied")

        # Advance the new order_nb_seq past whatever the backend's own
        # sequence already reached, so freshly-committed orders here never
        # collide with an order_nb already issued before the split.
        src_val = src.execute(text("SELECT last_value FROM order_nb_seq")).scalar()
        dst.execute(text("SELECT setval('order_nb_seq', :v)"), {"v": src_val})
        print(f"order_nb_seq: advanced to {src_val}")


if __name__ == "__main__":
    main()
