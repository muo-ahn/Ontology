#!/usr/bin/env python3
"""
Graph consistency checker/fixer for the Neo4j ontology.

Usage examples:
  python scripts/graph_check.py --summary
  python scripts/graph_check.py --fix --ontology-version 1.1

Defaults read NEO4J_URI/NEO4J_USER/NEO4J_PASS from the environment,
falling back to bolt://localhost:7687, neo4j, test1234 respectively.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from typing import Iterable, List

from neo4j import GraphDatabase, basic_auth


DEFAULT_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
DEFAULT_USER = os.getenv("NEO4J_USER", "neo4j")
DEFAULT_PASSWORD = os.getenv("NEO4J_PASS") or os.getenv("NEO4J_PASSWORD", "test1234")


@dataclass
class IssueSummary:
    non_string_version: int = 0
    missing_version_link: int = 0
    missing_image_link: int = 0
    missing_encounter_link: int = 0
    provenance_missing: int = 0

    def __str__(self) -> str:
        rows = [
            ("AIInference.version not string", self.non_string_version),
            ("AIInference missing RECORDED_WITH", self.missing_version_link),
            ("AIInference missing image link", self.missing_image_link),
            ("AIInference missing encounter link", self.missing_encounter_link),
            ("AIInference missing provenance link", self.provenance_missing),
        ]
        longest = max(len(label) for label, _ in rows)
        return "\n".join(f"{label:<{longest}} : {count}" for label, count in rows)


def single_value_query(session, query: str, **parameters):
    return session.run(query, **parameters).single()


def collect_ids(session, query: str, **parameters) -> List[str]:
    result = session.run(query, **parameters)
    return [record["id"] for record in result]


def check_and_fix(driver, *, ontology_version: str, fix: bool) -> IssueSummary:
    summary = IssueSummary()
    with driver.session() as session:
        rows = session.run(
            """
            MATCH (ai:AIInference)
            WHERE ai.version IS NOT NULL
            RETURN ai.inference_id AS id, ai.version AS version
            """
        )
        non_string = [row for row in rows if not isinstance(row["version"], str)]
        summary.non_string_version = len(non_string)
        if fix and non_string:
            session.run(
                """
                UNWIND $rows AS row
                MATCH (ai:AIInference {inference_id: row.id})
                SET ai.version = toString(row.version)
                """,
                rows=[{"id": row["id"], "version": row["version"]} for row in non_string],
            )

        missing_version_rel = collect_ids(
            session,
            """
            MATCH (ai:AIInference)
            WHERE NOT (ai)-[:RECORDED_WITH]->(:OntologyVersion)
            RETURN ai.inference_id AS id
            """,
        )
        summary.missing_version_link = len(missing_version_rel)
        if fix and missing_version_rel:
            session.run(
                """
                MERGE (v:OntologyVersion {version_id: $version})
                ON CREATE SET v.applied_at = datetime(), v.description = 'auto-created by graph_check'
                """,
                version=ontology_version,
            )
            session.run(
                """
                UNWIND $ids AS id
                MATCH (ai:AIInference {inference_id: id})
                MERGE (v:OntologyVersion {version_id: $version})
                MERGE (ai)-[:RECORDED_WITH]->(v)
                """,
                ids=missing_version_rel,
                version=ontology_version,
            )

        missing_image_rel = collect_ids(
            session,
            """
            MATCH (ai:AIInference)
            WHERE NOT (ai)<-[:HAS_INFERENCE]-(:Image)
            RETURN ai.inference_id AS id
            """,
        )
        summary.missing_image_link = len(missing_image_rel)

        missing_encounter_rel = collect_ids(
            session,
            """
            MATCH (ai:AIInference)
            WHERE NOT (ai)<-[:HAS_INFERENCE]-(:Encounter)
            RETURN ai.inference_id AS id
            """,
        )
        summary.missing_encounter_link = len(missing_encounter_rel)

        missing_provenance = collect_ids(
            session,
            """
            MATCH (ai:AIInference)
            WHERE NOT (ai)-[:DERIVES_FROM]->()
            RETURN ai.inference_id AS id
            """,
        )
        summary.provenance_missing = len(missing_provenance)

        if fix and missing_provenance:
            print(
                "[WARN] Some inferences still lack provenance relationships. "
                "Manual inspection required for: "
                + ", ".join(missing_provenance)
            )

    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check and optionally fix Neo4j graph consistency.")
    parser.add_argument("--uri", default=DEFAULT_URI, help=f"Neo4j bolt URI (default {DEFAULT_URI})")
    parser.add_argument("--user", default=DEFAULT_USER, help=f"Neo4j user (default {DEFAULT_USER})")
    parser.add_argument(
        "--password", default=DEFAULT_PASSWORD, help="Neo4j password (default from env or test1234)"
    )
    parser.add_argument("--ontology-version", default="1.1", help="OntologyVersion node to attach when fixing")
    parser.add_argument("--fix", action="store_true", help="Apply automatic fixes where possible")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    driver = GraphDatabase.driver(args.uri, auth=basic_auth(args.user, args.password))
    try:
        summary = check_and_fix(driver, ontology_version=args.ontology_version, fix=args.fix)
    finally:
        driver.close()

    print("Graph consistency report:\n")
    print(summary)
    if args.fix:
        print("\nFixes applied where possible.")


if __name__ == "__main__":
    main()
