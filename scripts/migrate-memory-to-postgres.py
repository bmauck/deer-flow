#!/usr/bin/env python3
"""Migrate memory data from JSON file to Postgres.

Run this once after switching memory.type from 'json' to 'postgres' in config.yaml.
Reads the existing memory.json and inserts it into the memory_store table.

Usage (from the deerflow root directory):
    python scripts/migrate-memory-to-postgres.py

Or from within the Docker container:
    docker exec deer-flow-gateway python /app/scripts/migrate-memory-to-postgres.py
"""

import json
import sys
from pathlib import Path

# Add backend to path so we can import deerflow modules
backend_dir = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(backend_dir))
sys.path.insert(0, str(backend_dir / "packages" / "harness"))


def main():
    import psycopg
    import psycopg.types.json

    # Load config to get connection string
    from deerflow.config.app_config import get_app_config

    config = get_app_config()

    # Determine connection string
    from deerflow.config.memory_config import get_memory_config

    mem_config = get_memory_config()
    conn_str = mem_config.connection_string
    if not conn_str and config.checkpointer:
        conn_str = config.checkpointer.connection_string
    if not conn_str:
        print("ERROR: No connection_string found in memory or checkpointer config")
        sys.exit(1)

    # Find the JSON file
    from deerflow.config.paths import get_paths

    paths = get_paths()
    if mem_config.storage_path:
        p = Path(mem_config.storage_path)
        json_path = p if p.is_absolute() else paths.base_dir / p
    else:
        json_path = paths.memory_file

    if not json_path.exists():
        print(f"No memory file found at {json_path}, nothing to migrate.")
        sys.exit(0)

    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    fact_count = len(data.get("facts", []))
    print(f"Loaded memory from {json_path} ({fact_count} facts)")

    # Connect and migrate
    with psycopg.connect(conn_str, autocommit=True) as conn:
        # Create table if needed
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_store (
                scope TEXT PRIMARY KEY,
                data JSONB NOT NULL DEFAULT '{}',
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)

        # Check if already migrated
        row = conn.execute("SELECT 1 FROM memory_store WHERE scope = 'global'").fetchone()
        if row:
            print("WARNING: Global memory already exists in Postgres.")
            resp = input("Overwrite? [y/N] ").strip().lower()
            if resp != "y":
                print("Aborted.")
                sys.exit(0)

        conn.execute(
            """
            INSERT INTO memory_store (scope, data, updated_at)
            VALUES ('global', %s, NOW())
            ON CONFLICT (scope) DO UPDATE SET data = EXCLUDED.data, updated_at = NOW()
            """,
            (psycopg.types.json.Jsonb(data),),
        )

    print(f"Successfully migrated {fact_count} facts to Postgres (scope=global)")
    print(f"The JSON file at {json_path} can now be archived or deleted.")


if __name__ == "__main__":
    main()
