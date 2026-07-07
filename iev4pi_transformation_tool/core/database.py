from __future__ import annotations

from collections import OrderedDict
import json
import sqlite3
from pathlib import Path
from typing import Iterable

from iev4pi_transformation_tool.core.utils import ensure_dir, json_dumps
from iev4pi_transformation_tool.models import (
    DocumentDescriptor,
    EvidenceRef,
    ExtractedFieldResult,
    ExtractedRecord,
    ReviewFeedback,
    ReviewRow,
    RunSummary,
    SchemaFamily,
)


class Database:
    def __init__(self, path: Path) -> None:
        ensure_dir(path.parent)
        self.path = path
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            PRAGMA foreign_keys = ON;

            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                relative_path TEXT UNIQUE NOT NULL,
                path TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                output_families_json TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                modified_at REAL NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chunks (
                id TEXT PRIMARY KEY,
                document_path TEXT NOT NULL,
                family TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                source_locator TEXT NOT NULL,
                text TEXT NOT NULL,
                tokens INTEGER NOT NULL,
                metadata_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS schema_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                family TEXT NOT NULL,
                scope_id TEXT NOT NULL DEFAULT '',
                version INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT NOT NULL,
                summary_json TEXT NOT NULL,
                config_json TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                family TEXT NOT NULL,
                source_path TEXT NOT NULL,
                source_root TEXT NOT NULL DEFAULT '',
                scope_id TEXT NOT NULL DEFAULT '',
                record_key TEXT NOT NULL,
                display_name TEXT NOT NULL,
                notes TEXT NOT NULL,
                trace_json TEXT NOT NULL DEFAULT '{}',
                warnings_json TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS field_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                record_id INTEGER NOT NULL,
                field_name TEXT NOT NULL,
                value TEXT NOT NULL,
                normalized_value TEXT NOT NULL,
                confidence REAL NOT NULL,
                status TEXT NOT NULL,
                notes TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                FOREIGN KEY(record_id) REFERENCES records(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS review_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                record_key TEXT NOT NULL,
                field_name TEXT NOT NULL,
                feedback_status TEXT NOT NULL,
                comment TEXT NOT NULL DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_review_feedback_lookup
            ON review_feedback (run_id, record_key, field_name, id DESC);
            """
        )
        self._ensure_column("schema_versions", "scope_id", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("records", "source_root", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("records", "scope_id", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("records", "trace_json", "TEXT NOT NULL DEFAULT '{}'")
        self._conn.commit()

    def _ensure_column(self, table_name: str, column_name: str, definition: str) -> None:
        columns = {
            str(row["name"])
            for row in self._conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name in columns:
            return
        self._conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")

    def upsert_documents(self, documents: Iterable[DocumentDescriptor]) -> None:
        rows = [
            (
                doc.relative_path,
                str(doc.path),
                doc.source_kind.value,
                json_dumps([family.value for family in doc.output_families]),
                doc.size_bytes,
                doc.modified_at,
                doc.model_dump_json(),
            )
            for doc in documents
        ]
        self._conn.executemany(
            """
            INSERT INTO documents (
                relative_path, path, source_kind, output_families_json,
                size_bytes, modified_at, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(relative_path) DO UPDATE SET
                path = excluded.path,
                source_kind = excluded.source_kind,
                output_families_json = excluded.output_families_json,
                size_bytes = excluded.size_bytes,
                modified_at = excluded.modified_at,
                payload_json = excluded.payload_json
            """,
            rows,
        )
        self._conn.commit()

    def replace_chunks(self, chunks: Iterable[dict[str, object]]) -> None:
        self._conn.execute("DELETE FROM chunks")
        self._conn.executemany(
            """
            INSERT INTO chunks (
                id, document_path, family, source_kind, source_locator,
                text, tokens, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    chunk["id"],
                    chunk["document_path"],
                    chunk["family"],
                    chunk["source_kind"],
                    chunk["source_locator"],
                    chunk["text"],
                    chunk["tokens"],
                    chunk["metadata_json"],
                )
                for chunk in chunks
            ),
        )
        self._conn.commit()

    def save_schema(self, schema: SchemaFamily) -> None:
        self._conn.execute(
            "INSERT INTO schema_versions (family, scope_id, version, payload_json) VALUES (?, ?, ?, ?)",
            (schema.family.value, schema.scope_id or "", schema.version, schema.model_dump_json()),
        )
        self._conn.commit()

    def load_latest_schemas(self) -> dict[str, SchemaFamily]:
        return self.load_latest_global_schemas()

    def load_latest_global_schemas(self) -> dict[str, SchemaFamily]:
        rows = self._conn.execute(
            """
            SELECT family, payload_json
            FROM schema_versions
            WHERE id IN (
                SELECT MAX(id)
                FROM schema_versions
                WHERE scope_id = ''
                GROUP BY family
            )
            """
        ).fetchall()
        return {row["family"]: SchemaFamily.model_validate_json(row["payload_json"]) for row in rows}

    def load_latest_scoped_schemas(self) -> dict[tuple[str, str], SchemaFamily]:
        rows = self._conn.execute(
            """
            SELECT scope_id, family, payload_json
            FROM schema_versions
            WHERE scope_id != ''
              AND id IN (
                  SELECT MAX(id)
                  FROM schema_versions
                  WHERE scope_id != ''
                  GROUP BY scope_id, family
              )
            """
        ).fetchall()
        return {
            (row["scope_id"], row["family"]): SchemaFamily.model_validate_json(row["payload_json"])
            for row in rows
        }

    def document_path_for_relative_path(self, relative_path: str) -> Path | None:
        row = self._conn.execute(
            "SELECT path FROM documents WHERE relative_path = ? LIMIT 1",
            (relative_path,),
        ).fetchone()
        if not row or not row["path"]:
            return None
        return Path(str(row["path"]))

    def reset_state(self) -> None:
        self._conn.commit()
        self._conn.execute("PRAGMA foreign_keys = OFF")
        try:
            self._conn.executescript(
                """
                DELETE FROM field_results;
                DELETE FROM records;
                DELETE FROM runs;
                DELETE FROM schema_versions;
                DELETE FROM chunks;
                DELETE FROM documents;
                DELETE FROM sqlite_sequence
                WHERE name IN ('field_results', 'records', 'runs', 'schema_versions', 'documents');
                """
            )
            self._conn.commit()
            self._conn.execute("VACUUM")
            self._conn.commit()
        finally:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.commit()

    def create_run(self, config: dict[str, object]) -> int:
        cursor = self._conn.execute(
            "INSERT INTO runs (status, summary_json, config_json) VALUES (?, ?, ?)",
            ("running", "{}", json_dumps(config)),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def save_run_records(self, run_id: int, records: Iterable[ExtractedRecord]) -> None:
        self._conn.execute("DELETE FROM field_results WHERE record_id IN (SELECT id FROM records WHERE run_id = ?)", (run_id,))
        self._conn.execute("DELETE FROM records WHERE run_id = ?", (run_id,))
        for record in records:
            cursor = self._conn.execute(
                """
                INSERT INTO records (
                run_id, family, source_path, source_root, scope_id,
                    record_key, display_name, notes, trace_json, warnings_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    record.family.value,
                    record.source_path,
                    record.source_root,
                    record.scope_id,
                    record.record_key,
                    record.display_name,
                    record.notes,
                    json_dumps(record.decision_trace),
                    json_dumps(record.cross_validation_warnings),
                ),
            )
            self._save_field_results(int(cursor.lastrowid), record.results)
        self._conn.commit()

    def _save_field_results(self, record_id: int, results: Iterable[ExtractedFieldResult]) -> None:
        self._conn.executemany(
            """
            INSERT INTO field_results (
                record_id, field_name, value, normalized_value, confidence,
                status, notes, evidence_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    record_id,
                    result.field_name,
                    result.value,
                    result.normalized_value,
                    result.confidence,
                    result.status.value,
                    result.notes,
                    result.model_dump_json(
                        include={
                            "evidence_refs",
                            "decision_confidence",
                            "evidence_bundle_id",
                            "uncertainty_reason",
                            "llm_verification_status",
                            "rule_support",
                            "review_feedback_status",
                        }
                    ),
                )
                for result in results
            ],
        )

    def finalize_run(self, summary: RunSummary) -> None:
        self._conn.execute(
            "UPDATE runs SET status = ?, summary_json = ? WHERE id = ?",
            (summary.status, summary.model_dump_json(), summary.run_id),
        )
        self._conn.commit()

    def latest_run_summary(self) -> RunSummary | None:
        row = self._conn.execute(
            "SELECT summary_json FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row or not row["summary_json"] or row["summary_json"] == "{}":
            return None
        return RunSummary.model_validate_json(row["summary_json"])

    def _latest_run_id(self) -> int | None:
        row = self._conn.execute("SELECT id FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return None
        return int(row["id"])

    def load_latest_records(self) -> list[ExtractedRecord]:
        run_id = self._latest_run_id()
        if run_id is None:
            return []
        rows = self._conn.execute(
            """
            SELECT
                r.id AS record_id,
                r.family,
                r.source_path,
                r.source_root,
                r.scope_id,
                r.record_key,
                r.display_name,
                r.notes,
                r.trace_json,
                r.warnings_json,
                fr.field_name,
                fr.value,
                fr.normalized_value,
                fr.confidence,
                fr.status,
                fr.notes AS field_notes,
                fr.evidence_json
            FROM records r
            JOIN field_results fr ON fr.record_id = r.id
            WHERE r.run_id = ?
            ORDER BY r.id, fr.id
            """,
            (run_id,),
        ).fetchall()
        feedback_map = self._latest_feedback_map(run_id)
        records: dict[int, ExtractedRecord] = {}
        for row in rows:
            record_id = int(row["record_id"])
            if record_id not in records:
                records[record_id] = ExtractedRecord(
                    family=row["family"],
                    source_path=row["source_path"],
                    source_root=row["source_root"],
                    scope_id=row["scope_id"],
                    record_key=row["record_key"],
                    display_name=row["display_name"],
                    notes=row["notes"],
                    decision_trace=json.loads(row["trace_json"] or "{}"),
                    cross_validation_warnings=json.loads(row["warnings_json"] or "[]"),
                    results=[],
                )
            evidence_payload = json.loads(row["evidence_json"] or "{}")
            evidence_refs = [
                EvidenceRef.model_validate(item)
                for item in evidence_payload.get("evidence_refs", [])
            ]
            feedback_key = (str(row["record_key"]), str(row["field_name"]))
            records[record_id].results.append(
                ExtractedFieldResult(
                    field_name=row["field_name"],
                    value=row["value"],
                    normalized_value=row["normalized_value"],
                    confidence=row["confidence"],
                    decision_confidence=evidence_payload.get("decision_confidence"),
                    status=row["status"],
                    notes=row["field_notes"],
                    evidence_refs=evidence_refs,
                    evidence_bundle_id=str(evidence_payload.get("evidence_bundle_id", "") or ""),
                    uncertainty_reason=str(evidence_payload.get("uncertainty_reason", "") or ""),
                    llm_verification_status=str(evidence_payload.get("llm_verification_status", "") or ""),
                    rule_support=list(evidence_payload.get("rule_support", []) or []),
                    review_feedback_status=feedback_map.get(feedback_key, ""),
                )
            )
        return list(records.values())

    def _review_record_filter_sql(
        self,
        run_id: int,
        family: str | None = None,
        scope_id: str | None = None,
        keyword: str | None = None,
    ) -> tuple[str, tuple[object, ...]]:
        clauses: list[str] = ["r.run_id = ?"]
        params: list[object] = [run_id]
        if family:
            clauses.append("r.family = ?")
            params.append(family)
        if scope_id:
            clauses.append("r.scope_id = ?")
            params.append(scope_id)
        normalized_keyword = (keyword or "").strip()
        if normalized_keyword:
            pattern = f"%{normalized_keyword}%"
            clauses.append(
                """
                (
                    r.family LIKE ?
                    OR r.record_key LIKE ?
                    OR r.display_name LIKE ?
                    OR r.source_path LIKE ?
                    OR EXISTS (
                        SELECT 1
                        FROM field_results frs
                        WHERE frs.record_id = r.id
                          AND (
                              frs.field_name LIKE ?
                              OR frs.value LIKE ?
                              OR frs.status LIKE ?
                              OR COALESCE(json_extract(frs.evidence_json, '$.evidence_refs[0].page_or_sheet'), '') LIKE ?
                              OR COALESCE(json_extract(frs.evidence_json, '$.evidence_refs[0].cell_range_or_bbox'), '') LIKE ?
                              OR COALESCE(json_extract(frs.evidence_json, '$.evidence_refs[0].snippet'), '') LIKE ?
                          )
                    )
                )
                """
            )
            params.extend([pattern] * 10)
        return " WHERE " + " AND ".join(clauses), tuple(params)

    def review_record_count(
        self,
        family: str | None = None,
        *,
        scope_id: str | None = None,
        keyword: str | None = None,
    ) -> int:
        run_id = self._latest_run_id()
        if run_id is None:
            return 0
        where_sql, params = self._review_record_filter_sql(run_id, family, scope_id, keyword)
        row = self._conn.execute(
            "SELECT COUNT(*) AS row_count FROM records r" + where_sql,
            params,
        ).fetchone()
        return int(row["row_count"]) if row else 0

    def review_records_page(
        self,
        family: str | None = None,
        *,
        scope_id: str | None = None,
        keyword: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ExtractedRecord]:
        run_id = self._latest_run_id()
        if run_id is None:
            return []
        where_sql, base_params = self._review_record_filter_sql(run_id, family, scope_id, keyword)
        base_query = (
            """
            SELECT
                r.id AS record_id,
                r.family,
                r.source_path,
                r.source_root,
                r.scope_id,
                r.record_key,
                r.display_name,
                r.notes,
                r.trace_json,
                r.warnings_json
            FROM records r
            """
            + where_sql
            + """
            ORDER BY r.family, r.display_name, r.id
            """
        )
        if int(limit) <= 0:
            record_rows = self._conn.execute(base_query, base_params).fetchall()
        else:
            record_rows = self._conn.execute(
                base_query + "\nLIMIT ? OFFSET ?",
                (*base_params, max(1, int(limit)), max(0, int(offset))),
            ).fetchall()
        if not record_rows:
            return []

        records: OrderedDict[int, ExtractedRecord] = OrderedDict()
        for row in record_rows:
            record_id = int(row["record_id"])
            records[record_id] = ExtractedRecord(
                family=row["family"],
                source_path=row["source_path"],
                source_root=row["source_root"],
                scope_id=row["scope_id"],
                record_key=row["record_key"],
                display_name=row["display_name"],
                notes=row["notes"],
                decision_trace=json.loads(row["trace_json"] or "{}"),
                cross_validation_warnings=json.loads(row["warnings_json"] or "[]"),
                results=[],
            )
        feedback_map = self._latest_feedback_map(run_id)

        placeholders = ",".join("?" for _ in records)
        field_rows = self._conn.execute(
            f"""
            SELECT
                fr.record_id,
                fr.field_name,
                fr.value,
                fr.normalized_value,
                fr.confidence,
                fr.status,
                fr.notes AS field_notes,
                fr.evidence_json
            FROM field_results fr
            WHERE fr.record_id IN ({placeholders})
            ORDER BY fr.record_id, fr.id
            """,
            tuple(records.keys()),
        ).fetchall()

        for row in field_rows:
            record_id = int(row["record_id"])
            evidence_payload = json.loads(row["evidence_json"] or "{}")
            evidence_refs = [
                EvidenceRef.model_validate(item)
                for item in evidence_payload.get("evidence_refs", [])
            ]
            feedback_key = (str(records[record_id].record_key), str(row["field_name"]))
            records[record_id].results.append(
                ExtractedFieldResult(
                    field_name=row["field_name"],
                    value=row["value"],
                    normalized_value=row["normalized_value"],
                    confidence=row["confidence"],
                    decision_confidence=evidence_payload.get("decision_confidence"),
                    status=row["status"],
                    notes=row["field_notes"],
                    evidence_refs=evidence_refs,
                    evidence_bundle_id=str(evidence_payload.get("evidence_bundle_id", "") or ""),
                    uncertainty_reason=str(evidence_payload.get("uncertainty_reason", "") or ""),
                    llm_verification_status=str(evidence_payload.get("llm_verification_status", "") or ""),
                    rule_support=list(evidence_payload.get("rule_support", []) or []),
                    review_feedback_status=feedback_map.get(feedback_key, ""),
                )
            )
        return list(records.values())

    def latest_review_selection_rows(self) -> list[sqlite3.Row]:
        run_id = self._latest_run_id()
        if run_id is None:
            return []
        return self._conn.execute(
            """
            SELECT DISTINCT
                r.family,
                r.source_root,
                r.scope_id
            FROM records r
            WHERE r.run_id = ?
            ORDER BY r.source_root, r.scope_id, r.family
            """,
            (run_id,),
        ).fetchall()

    def _review_filter_sql(self, family: str | None = None, keyword: str | None = None) -> tuple[str, tuple[object, ...]]:
        clauses: list[str] = []
        params: list[object] = []
        if family:
            clauses.append("r.family = ?")
            params.append(family)
        normalized_keyword = (keyword or "").strip()
        if normalized_keyword:
            pattern = f"%{normalized_keyword}%"
            search_clauses = [
                "r.family LIKE ?",
                "r.record_key LIKE ?",
                "r.display_name LIKE ?",
                "fr.field_name LIKE ?",
                "fr.value LIKE ?",
                "fr.status LIKE ?",
                "r.source_path LIKE ?",
                "COALESCE(json_extract(fr.evidence_json, '$.evidence_refs[0].page_or_sheet'), '') LIKE ?",
                "COALESCE(json_extract(fr.evidence_json, '$.evidence_refs[0].snippet'), '') LIKE ?",
            ]
            clauses.append("(" + " OR ".join(search_clauses) + ")")
            params.extend([pattern] * len(search_clauses))
        if not clauses:
            return "", tuple()
        return " WHERE " + " AND ".join(clauses), tuple(params)

    def review_rows(self, family: str | None = None, keyword: str | None = None) -> list[ReviewRow]:
        run_id = self._latest_run_id() or 0
        query = """
            SELECT
                r.family,
                r.record_key,
                r.display_name,
                fr.field_name,
                fr.value,
                fr.status,
                fr.confidence,
                r.source_path,
                json_extract(fr.evidence_json, '$.evidence_refs[0].page_or_sheet') AS location,
                json_extract(fr.evidence_json, '$.evidence_refs[0].snippet') AS snippet,
                json_extract(fr.evidence_json, '$.decision_confidence') AS decision_confidence,
                json_extract(fr.evidence_json, '$.evidence_bundle_id') AS evidence_bundle_id,
                json_extract(fr.evidence_json, '$.uncertainty_reason') AS uncertainty_reason,
                json_extract(fr.evidence_json, '$.llm_verification_status') AS llm_verification_status
            FROM field_results fr
            JOIN records r ON r.id = fr.record_id
        """
        where_sql, params = self._review_filter_sql(family, keyword)
        query += where_sql
        query += " ORDER BY r.family, r.display_name, fr.field_name"
        rows = self._conn.execute(query, params).fetchall()
        return [
            ReviewRow(
                family=row["family"],
                record_key=row["record_key"],
                display_name=row["display_name"],
                field_name=row["field_name"],
                value=row["value"],
                status=row["status"],
                confidence=row["confidence"],
                source_path=row["source_path"],
                location=row["location"] or "",
                snippet=row["snippet"] or "",
                decision_confidence=row["decision_confidence"],
                evidence_bundle_id=row["evidence_bundle_id"] or "",
                uncertainty_reason=row["uncertainty_reason"] or "",
                llm_verification_status=row["llm_verification_status"] or "",
                review_feedback_status=self.review_feedback_status(run_id, row["record_key"], row["field_name"]),
            )
            for row in rows
        ]

    def review_row_count(self, family: str | None = None, keyword: str | None = None) -> int:
        query = """
            SELECT COUNT(*) AS row_count
            FROM field_results fr
            JOIN records r ON r.id = fr.record_id
        """
        where_sql, params = self._review_filter_sql(family, keyword)
        query += where_sql
        row = self._conn.execute(query, params).fetchone()
        return int(row["row_count"]) if row else 0

    def review_rows_page(
        self,
        family: str | None = None,
        *,
        keyword: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[ReviewRow]:
        query = """
            SELECT
                r.family,
                r.record_key,
                r.display_name,
                fr.field_name,
                fr.value,
                fr.status,
                fr.confidence,
                r.source_path,
                json_extract(fr.evidence_json, '$.evidence_refs[0].page_or_sheet') AS location,
                json_extract(fr.evidence_json, '$.evidence_refs[0].snippet') AS snippet,
                json_extract(fr.evidence_json, '$.decision_confidence') AS decision_confidence,
                json_extract(fr.evidence_json, '$.evidence_bundle_id') AS evidence_bundle_id,
                json_extract(fr.evidence_json, '$.uncertainty_reason') AS uncertainty_reason,
                json_extract(fr.evidence_json, '$.llm_verification_status') AS llm_verification_status
            FROM field_results fr
            JOIN records r ON r.id = fr.record_id
        """
        where_sql, base_params = self._review_filter_sql(family, keyword)
        query += where_sql
        params = list(base_params)
        query += " ORDER BY r.family, r.display_name, fr.field_name LIMIT ? OFFSET ?"
        params.extend([max(1, int(limit)), max(0, int(offset))])
        rows = self._conn.execute(query, tuple(params)).fetchall()
        run_id = self._latest_run_id() or 0
        return [
            ReviewRow(
                family=row["family"],
                record_key=row["record_key"],
                display_name=row["display_name"],
                field_name=row["field_name"],
                value=row["value"],
                status=row["status"],
                confidence=row["confidence"],
                source_path=row["source_path"],
                location=row["location"] or "",
                snippet=row["snippet"] or "",
                decision_confidence=row["decision_confidence"],
                evidence_bundle_id=row["evidence_bundle_id"] or "",
                uncertainty_reason=row["uncertainty_reason"] or "",
                llm_verification_status=row["llm_verification_status"] or "",
                review_feedback_status=self.review_feedback_status(run_id, row["record_key"], row["field_name"]),
            )
            for row in rows
        ]

    def save_review_feedback(
        self,
        record_key: str,
        field_name: str,
        feedback_status: str,
        *,
        comment: str = "",
    ) -> ReviewFeedback | None:
        run_id = self._latest_run_id()
        if run_id is None:
            return None
        self._conn.execute(
            """
            INSERT INTO review_feedback (
                run_id, record_key, field_name, feedback_status, comment
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, record_key, field_name, feedback_status, comment),
        )
        self._conn.commit()
        row = self._conn.execute(
            """
            SELECT run_id, record_key, field_name, feedback_status, comment, created_at
            FROM review_feedback
            WHERE run_id = ? AND record_key = ? AND field_name = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (run_id, record_key, field_name),
        ).fetchone()
        if row is None:
            return None
        return ReviewFeedback(
            run_id=int(row["run_id"]),
            record_key=row["record_key"],
            field_name=row["field_name"],
            feedback_status=row["feedback_status"],
            comment=row["comment"] or "",
            created_at=row["created_at"] or "",
        )

    def review_feedback_status(self, run_id: int, record_key: str, field_name: str) -> str:
        if run_id <= 0:
            return ""
        row = self._conn.execute(
            """
            SELECT feedback_status
            FROM review_feedback
            WHERE run_id = ? AND record_key = ? AND field_name = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (run_id, record_key, field_name),
        ).fetchone()
        if row is None:
            return ""
        return str(row["feedback_status"] or "")

    def _latest_feedback_map(self, run_id: int) -> dict[tuple[str, str], str]:
        if run_id <= 0:
            return {}
        rows = self._conn.execute(
            """
            SELECT rf.record_key, rf.field_name, rf.feedback_status
            FROM review_feedback rf
            JOIN (
                SELECT record_key, field_name, MAX(id) AS latest_id
                FROM review_feedback
                WHERE run_id = ?
                GROUP BY record_key, field_name
            ) latest
              ON latest.latest_id = rf.id
            """,
            (run_id,),
        ).fetchall()
        return {
            (str(row["record_key"]), str(row["field_name"])): str(row["feedback_status"] or "")
            for row in rows
        }

    def close(self) -> None:
        self._conn.close()
