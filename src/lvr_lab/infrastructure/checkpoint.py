"""
Indexer checkpoint storage — persist (last_block_processed) per pipeline.

A real indexer must survive restarts. The checkpoint records the highest
block fully processed; on startup, the indexer resumes from checkpoint+1.

Two implementations:
  - FileCheckpointStore: JSON file on disk. Default for local dev.
  - PostgresCheckpointStore: row in a control table. Default for production.
"""

from __future__ import annotations
import json
from pathlib import Path
from typing import Optional


class CheckpointStore:
    """Abstract base."""

    def get(self, pipeline: str) -> Optional[int]:
        raise NotImplementedError

    def set(self, pipeline: str, block: int) -> None:
        raise NotImplementedError


class FileCheckpointStore(CheckpointStore):
    """JSON-file-backed checkpoint. Single-writer; no locking."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("{}")

    def _read(self) -> dict[str, int]:
        try:
            return json.loads(self.path.read_text())
        except json.JSONDecodeError:
            return {}

    def get(self, pipeline: str) -> Optional[int]:
        return self._read().get(pipeline)

    def set(self, pipeline: str, block: int) -> None:
        data = self._read()
        data[pipeline] = block
        self.path.write_text(json.dumps(data, indent=2))


class PostgresCheckpointStore(CheckpointStore):
    """TimescaleDB / Postgres-backed checkpoint.

    Schema:
        CREATE TABLE indexer_checkpoint (
            pipeline TEXT PRIMARY KEY,
            last_block BIGINT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """

    def __init__(self, conn):
        """conn: a psycopg2 / psycopg3 connection."""
        self.conn = conn

    def get(self, pipeline: str) -> Optional[int]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT last_block FROM indexer_checkpoint WHERE pipeline = %s",
                (pipeline,),
            )
            row = cur.fetchone()
            return int(row[0]) if row else None

    def set(self, pipeline: str, block: int) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO indexer_checkpoint (pipeline, last_block)
                VALUES (%s, %s)
                ON CONFLICT (pipeline)
                DO UPDATE SET last_block = EXCLUDED.last_block, updated_at = now();
                """,
                (pipeline, block),
            )
        self.conn.commit()


class InMemoryCheckpointStore(CheckpointStore):
    """For tests."""

    def __init__(self):
        self._data: dict[str, int] = {}

    def get(self, pipeline: str) -> Optional[int]:
        return self._data.get(pipeline)

    def set(self, pipeline: str, block: int) -> None:
        self._data[pipeline] = block
