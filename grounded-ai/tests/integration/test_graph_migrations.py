from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

import pytest
from neo4j import GraphDatabase, basic_auth


SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schema" / "v1_1"
SEED_FILE = Path(__file__).resolve().parents[2] / "scripts" / "seed.cypher"


def _load_statements(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    cleaned_lines = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        cleaned_lines.append(stripped)

    statements: list[str] = []
    for chunk in "\n".join(cleaned_lines).split(";"):
        stmt = chunk.strip()
        if stmt:
            statements.append(stmt)
    return statements


def _execute_statements(session, statements: Iterable[str]) -> None:
    for stmt in statements:
        session.run(stmt)


@pytest.fixture(scope="module")
def neo4j_driver():
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASS", "test1234")

    driver = GraphDatabase.driver(uri, auth=basic_auth(user, password))
    try:
        with driver.session() as session:
            session.run("RETURN 1")
    except Exception as exc:  # pragma: no cover - dependency not available
        driver.close()
        pytest.skip(f"Neo4j unavailable: {exc}")
    yield driver
    driver.close()


@pytest.fixture()
def clean_database(neo4j_driver):
    with neo4j_driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n")
    yield
    with neo4j_driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n")


def test_migration_and_seed_apply_successfully(neo4j_driver, clean_database):
    constraints = _load_statements(SCHEMA_DIR / "constraints.cypher")
    migration_up = _load_statements(SCHEMA_DIR / "migrations_up.cypher")
    seed_statements = _load_statements(SEED_FILE)

    with neo4j_driver.session() as session:
        _execute_statements(session, constraints)
        _execute_statements(session, migration_up)
        _execute_statements(session, seed_statements)

        orphan_inferences = session.run(
            """
            MATCH (ai:AIInference)
            WHERE NOT (ai)<-[:HAS_INFERENCE]-(:Image)
            RETURN count(ai) AS cnt
            """
        ).single()["cnt"]
        assert orphan_inferences == 0

        missing_versions = session.run(
            """
            MATCH (ai:AIInference)
            WHERE ai.version IS NULL
            RETURN count(ai) AS cnt
            """
        ).single()["cnt"]
        assert missing_versions == 0

        encounter_links = session.run(
            """
            MATCH (ai:AIInference)
            WHERE NOT (ai)<-[:HAS_INFERENCE]-(:Encounter)
            RETURN count(ai) AS cnt
            """
        ).single()["cnt"]
        assert encounter_links == 0

        provenance_links = session.run(
            """
            MATCH (ai:AIInference)
            OPTIONAL MATCH (ai)-[:DERIVES_FROM]->(src)
            WITH ai, count(src) AS provenance_count
            RETURN count { (ai) WHERE provenance_count > 0 } AS with_provenance,
                   count(ai) AS total
            """
        ).single()
        assert provenance_links["with_provenance"] == provenance_links["total"]

        version_node = session.run(
            "MATCH (v:OntologyVersion {version_id: '1.1'}) RETURN count(v) AS cnt"
        ).single()["cnt"]
        assert version_node == 1


def test_migration_down_rolls_back(neo4j_driver, clean_database):
    migration_up = _load_statements(SCHEMA_DIR / "migrations_up.cypher")
    migration_down = _load_statements(SCHEMA_DIR / "migrations_down.cypher")

    with neo4j_driver.session() as session:
        _execute_statements(session, migration_up)
        exists_before = session.run(
            "MATCH (v:OntologyVersion {version_id: '1.1'}) RETURN count(v) AS cnt"
        ).single()["cnt"]
        assert exists_before == 1

        _execute_statements(session, migration_down)
        exists_after = session.run(
            "MATCH (v:OntologyVersion {version_id: '1.1'}) RETURN count(v) AS cnt"
        ).single()["cnt"]
        assert exists_after == 0
