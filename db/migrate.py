"""
Minimal migration runner. Applies .sql files in db/migrations/ in order,
tracking what's already been applied in a `schema_migrations` table.

Usage: python -m db.migrate
"""

import asyncio
import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

load_dotenv()

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


async def run_migrations():
    conn = await asyncpg.connect(dsn=os.environ["DATABASE_URL"])
    try:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename    TEXT PRIMARY KEY,
                applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )

        applied = {
            r["filename"] for r in await conn.fetch("SELECT filename FROM schema_migrations")
        }

        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in applied:
                print(f"skip  {path.name} (already applied)")
                continue

            print(f"apply {path.name}")
            sql = path.read_text()
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (filename) VALUES ($1)", path.name
                )

        print("done")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run_migrations())
