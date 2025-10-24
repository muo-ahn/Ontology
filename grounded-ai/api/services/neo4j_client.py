"""
Async-friendly wrapper around Neo4j's Python driver.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class Neo4jClient:
    uri: str
    user: str
    password: str
    database: Optional[str] = None
    _driver: Optional[object] = None

    def __post_init__(self) -> None:
        try:
            from neo4j import GraphDatabase, basic_auth  # type: ignore

            self._driver = GraphDatabase.driver(self.uri, auth=basic_auth(self.user, self.password))
        except Exception:  # pragma: no cover - fallback path
            self._driver = None

    @classmethod
    def from_env(cls) -> "Neo4jClient":
        uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        user = os.getenv("NEO4J_USER", "neo4j")
        password = os.getenv("NEO4J_PASS", "test1234")
        database = os.getenv("NEO4J_DATABASE")
        return cls(uri=uri, user=user, password=password, database=database)

    async def run_query(self, query: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        if self._driver is None:
            raise RuntimeError("Neo4j driver not initialised")

        def _work() -> List[Dict[str, Any]]:
            driver = self._driver
            assert driver is not None
            with driver.session(database=self.database) as session:
                result = session.run(query, params or {})
                return [record.data() for record in result]

        return await asyncio.to_thread(_work)

    def close(self) -> None:
        if self._driver is None:
            return
        try:
            self._driver.close()  # type: ignore[attr-defined]
        except Exception:
            pass

    async def health(self) -> bool:
        """Check that Neo4j responds to a trivial read query."""

        try:
            rows = await self.run_query("RETURN 1 AS up")
        except Exception:
            return False
        if not rows:
            return False
        return rows[0].get("up") == 1
