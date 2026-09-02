"""One-off data cutover: copies item/customer/order_header/order_details/
qra_header/qra_detail/customer_ownership_history from the real Postgres
`catalog` schema (where this service's data lives today) into the new SQL
Server database's `catalog` schema, as part of the Postgres -> SQL Server
2025 migration.

Run ONCE, against a freshly-migrated (empty) SQL Server schema - i.e.
after `alembic upgrade head` has created the tables there, before the
service is ever pointed at SQL Server for real traffic. Not idempotent (no
ON CONFLICT/MERGE handling): re-running it against a schema that already
has rows will raise a primary-key violation rather than silently
duplicating or skipping - a fresh cutover should only ever run once, and
a failed run should be re-tried on a re-truncated destination, not resumed.

customer_ownership_history's own `id` (a surrogate autoincrement PK -
nothing references it via FK) is intentionally NOT copied - SQL Server's
IDENTITY assigns fresh ones. Every other table copies its real primary key
values (natural keys - item_number, CustomerNumber, order_nb+order_type
etc. - not surrogate autoincrement columns), so no IDENTITY_INSERT dance is
needed for those.

Requires psycopg installed (to read from Postgres) even though the
service's own requirements.txt no longer needs it -
`pip install psycopg[binary]` into the venv before running this once.

Run with the venv active, after editing SRC/DST below for the real
source/destination credentials: python migrate_data.py
"""
from sqlalchemy import create_engine, text

# The real Postgres instance being migrated OFF of - the `catalog` schema
# specifically, since that's where this service's live data actually lives
# today (not `public`, which is the backend's own schema).
SRC = "postgresql+psycopg://voiceorder:changeme@localhost/voiceorder" \
      "?options=-c%20search_path%3Dcatalog"
# The new SQL Server database/schema being migrated TO. Edit the driver
# name/TrustServerCertificate as needed for the real target server.
DST = ("mssql+pyodbc://voiceorder:changeme@localhost/voiceorder"
      "?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes")
DST_SCHEMA = "catalog"

TABLES = [
    ("item", ["item_number", "item_desc", "category"]),
    ("customer", ['"CustomerNumber"', '"CustomerName"', "email", "telephone",
                  '"City"', '"Address1"', '"Address2"', "salesman_id"]),
    ("order_header", ["order_nb", "order_type", "cust_nb",
                      "commit_intent_id"]),
    ("order_details", ["order_nb", "order_type", "line_nb", "item_nb",
                       "item_desc", "qty", "uom", "line_type", "is_free"]),
    ("qra_header", ["cust_nb", "from_date", "to_date", "status"]),
    ("qra_detail", ["cust_nb", "qra_type", "item_nb_buy", "item_nb_get",
                    "item_nb_price", "qty_buy", "qty_get", "qra_price"]),
    # id (surrogate PK) deliberately excluded - see module docstring.
    ("customer_ownership_history",
     ["cust_nb", "salesman_id", "effective_from", "effective_to"]),
]

# The Postgres source column names are double-quoted (mixed-case
# identifiers); the SQL Server destination doesn't need that (case-
# insensitive by default, and none of these are reserved words).
def _dst_col(c: str) -> str:
    return c.strip('"')


def main():
    src_engine = create_engine(SRC)
    dst_engine = create_engine(DST).execution_options(
        schema_translate_map={None: DST_SCHEMA})

    with src_engine.connect() as src, dst_engine.begin() as dst:
        for table, cols in TABLES:
            src_col_list = ", ".join(cols)
            rows = src.execute(
                text(f"SELECT {src_col_list} FROM {table}")).all()
            if not rows:
                print(f"{table}: 0 rows (nothing to copy)")
                continue
            dst_cols = [_dst_col(c) for c in cols]
            dst_col_list = ", ".join(dst_cols)
            placeholders = ", ".join(f":{c}" for c in dst_cols)
            stmt = text(f"INSERT INTO {table} ({dst_col_list}) "
                       f"VALUES ({placeholders})")
            for row in rows:
                dst.execute(stmt, dict(zip(dst_cols, row)))
            print(f"{table}: {len(rows)} rows copied")

        # Advance the new order_nb_seq past whatever the Postgres sequence
        # already reached, so freshly-committed orders on SQL Server never
        # collide with an order_nb already issued before the cutover.
        src_val = src.execute(text("SELECT last_value FROM order_nb_seq")).scalar()
        dst.execute(text(f"ALTER SEQUENCE order_nb_seq RESTART WITH {int(src_val) + 1}"))
        print(f"order_nb_seq: advanced to start at {int(src_val) + 1}")


if __name__ == "__main__":
    main()
