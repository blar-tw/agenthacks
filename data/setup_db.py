"""Create the 7 SingleStore tables (Brief 8.6) and fix vector column sizes. Safe to run again.

    uv run python -m data.setup_db
"""
from data import config
from data.store import connect, create_tables, ensure_vector_dims, migrate

if __name__ == "__main__":
    conn = connect()
    try:
        for t in create_tables(conn):
            print(f"table ready: {t}")
        for change in migrate(conn):
            print(f"migrated: {change}")
        for change in ensure_vector_dims(conn):
            print(f"vector column rebuilt: {change}")
        print(f"vectors: {config.SIE_EMBED_MODEL}, {config.EMBED_DIM} dims")
    finally:
        conn.close()
