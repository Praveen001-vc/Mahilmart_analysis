import os
from pathlib import Path

import psycopg
from psycopg import sql
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def main():
    db_name = os.getenv("POSTGRES_DB", "mahilmart_tracking")
    admin_db = os.getenv("POSTGRES_ADMIN_DB", "postgres")
    connection_kwargs = {
        "dbname": admin_db,
        "user": os.getenv("POSTGRES_USER", "postgres"),
        "password": os.getenv("POSTGRES_PASSWORD", "postgres"),
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": os.getenv("POSTGRES_PORT", "5432"),
    }

    try:
        with psycopg.connect(**connection_kwargs, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
                if cursor.fetchone():
                    print(f"Database '{db_name}' already exists.")
                    return

                cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
                print(f"Database '{db_name}' created successfully.")
    except psycopg.OperationalError as exc:
        print("PostgreSQL connection failed.")
        print(
            "Check POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_HOST, and POSTGRES_PORT in the .env file."
        )
        print(
            f"Tried user='{connection_kwargs['user']}' host='{connection_kwargs['host']}' port='{connection_kwargs['port']}'."
        )
        raise SystemExit(str(exc))


if __name__ == "__main__":
    main()
