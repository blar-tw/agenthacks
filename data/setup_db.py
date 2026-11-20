"""Create the 7 SingleStore tables (Brief 8.6). Safe to run again.

    uv run python -m data.setup_db
"""
from data.store import connect, create_tables

if __name__ == "__main__":
    conn = connect()
    try:
        for t in create_tables(conn):
            print(f"table ready: {t}")
    finally:
        conn.close()
