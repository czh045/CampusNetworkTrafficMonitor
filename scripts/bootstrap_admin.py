"""Create the first administrator account after the database schema exists."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from werkzeug.security import generate_password_hash

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import get_db_connection


def main() -> int:
    parser = argparse.ArgumentParser(description="Create an administrator account.")
    parser.add_argument("--username", default=os.getenv("BOOTSTRAP_ADMIN_USERNAME", "admin"))
    parser.add_argument("--email", default=os.getenv("BOOTSTRAP_ADMIN_EMAIL", "admin@example.com"))
    parser.add_argument("--password", default=os.getenv("BOOTSTRAP_ADMIN_PASSWORD"))
    args = parser.parse_args()

    password = args.password or getpass.getpass("Administrator password: ")
    if len(password) < 8:
        raise SystemExit("Password must contain at least 8 characters.")

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM sys_user WHERE username = %s", (args.username,))
        if cursor.fetchone():
            raise SystemExit(f"User '{args.username}' already exists.")

        cursor.execute(
            """
            INSERT INTO sys_user (username, password_hash, email, role_id, is_active)
            VALUES (%s, %s, %s, 1, 1)
            """,
            (args.username, generate_password_hash(password), args.email),
        )
        conn.commit()
        print(f"Created administrator account: {args.username}")
        return 0
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
