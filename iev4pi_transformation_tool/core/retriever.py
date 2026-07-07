from __future__ import annotations

from array import array
from concurrent.futures import as_completed

from iev4pi_transformation_tool.core.qos_helpers import QoSAwareThreadPoolExecutor
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

try:  # pragma: no cover - optional dependency
    from sklearn.feature_extraction.text import TfidfVectorizer
except Exception:  # pragma: no cover - optional dependency
    TfidfVectorizer = None

from iev4pi_transformation_tool.core.evidence_graph import EvidenceGraphBuilder
from iev4pi_transformation_tool.core.llm_client import OpenAICompatibleLLMClient
from iev4pi_transformation_tool.core.utils import cosine_similarity, ensure_dir, extract_component_tokens, normalize_identifier, tokenize
from iev4pi_transformation_tool.models import Chunk, DocumentFamily, EvidenceBundle, EvidenceGraph, RetrievalHit, SourceDocumentKind


ProgressCallback = Callable[[int, str], None]
SparseVector = dict[int, float]


class LocalHashEmbedding:
    def __init__(self, dimensions: int = 768) -> None:
        self.dimensions = dimensions

    def embed(self, text: str) -> SparseVector:
        vector: SparseVector = {}
        for token in tokenize(text):
            index = hash(token) % self.dimensions
            vector[index] = vector.get(index, 0.0) + 1.0
        return vector


class Retriever:
    def __init__(
        self,
        dimensions: int = 768,
        *,
        llm_client: OpenAICompatibleLLMClient | None = None,
        cache_dir: Path | None = None,
        logger: Callable[..., Any] | None = None,
    ) -> None:
        self._embedder = LocalHashEmbedding(dimensions)
        self._tfidf = TfidfVectorizer(lowercase=True, ngram_range=(1, 2)) if TfidfVectorizer is not None else None
        self._tfidf_matrix = None
        self._tfidf_row_for_chunk: dict[str, int] = {}
        self._vectors: dict[str, SparseVector] = {}
        self._embedding_vectors: dict[str, array] = {}
        self._shared_chunk_keys: dict[str, str] = {}
        self._chunks: dict[str, Chunk] = {}
        self._identifiers: dict[str, list[str]] = {}
        self._tags: dict[str, list[str]] = {}
        self._by_document: defaultdict[str, list[str]] = defaultdict(list)
        self._by_family: defaultdict[DocumentFamily, list[str]] = defaultdict(list)
        self._by_source_kind: defaultdict[SourceDocumentKind, list[str]] = defaultdict(list)
        self._graph_builder = EvidenceGraphBuilder()
        self._graph = EvidenceGraph()
        self._graph_needs_rebuild = False
        self._llm_client = llm_client
        self._cache_dir = ensure_dir(cache_dir) if cache_dir is not None else None
        self._logger = logger

    def _parallel_workers(self) -> int:
        config = getattr(self._llm_client, "config", None)
        raw_value = getattr(config, "parallel_workers", 1) if config is not None else 1
        try:
            return max(1, min(32, int(raw_value or 1)))
        except (TypeError, ValueError):
            return 1

    def _embedding_workers(self) -> int:
        from iev4pi_transformation_tool.core.qos_helpers import io_worker_count

        configured = self._parallel_workers()
        return min(io_worker_count(cap=8), configured) if configured > 1 else io_worker_count(cap=8)

    def _embedding_batch_size(self) -> int:
        return 128

    def _should_use_tfidf(self, chunk_count: int) -> bool:
        return chunk_count <= 12000

    def _log_debug(
        self,
        *,
        action: str,
        message: str,
        level: str = "INFO",
        details: dict[str, Any] | None = None,
    ) -> None:
        if self._logger is None:
            return
        self._logger(
            source="rag",
            action=action,
            message=message,
            level=level,
            details=details,
        )

    @property
    def evidence_graph(self) -> EvidenceGraph:
        if self._graph_needs_rebuild:
            self._graph = self._graph_builder.build(list(self._chunks.values()))
            self._graph_needs_rebuild = False
        return self._graph

    def build(self, chunks: list[Chunk], progress: ProgressCallback | None = None) -> None:
        self._tfidf_row_for_chunk.clear()
        self._vectors.clear()
        self._embedding_vectors.clear()
        self._shared_chunk_keys.clear()
        self._chunks.clear()
        self._identifiers.clear()
        self._tags.clear()
        self._by_document.clear()
        self._by_family.clear()
        self._by_source_kind.clear()
        shared_chunks: dict[str, Chunk] = {}
        for chunk in chunks:
            shared_key = self._shared_chunk_key(chunk)
            self._shared_chunk_keys[chunk.id] = shared_key
            existing = shared_chunks.get(shared_key)
            if existing is None:
                shared_chunks[shared_key] = chunk
            else:
                chunk.text = existing.text
                chunk.metadata = existing.metadata
                chunk.document_path = existing.document_path
                chunk.source_locator = existing.source_locator
            self._chunks[chunk.id] = chunk
            if shared_key not in self._vectors:
                self._vectors[shared_key] = self._embedder.embed(chunk.text)
                self._identifiers[shared_key] = self._chunk_identifiers(chunk)
                self._tags[shared_key] = extract_component_tokens(chunk.text)
            self._by_document[chunk.document_path].append(chunk.id)
            self._by_family[chunk.family].append(chunk.id)
            self._by_source_kind[chunk.source_kind].append(chunk.id)
        self._graph = EvidenceGraph()
        self._graph_needs_rebuild = True
        shared_items = list(shared_chunks.items())
        shared_texts = [chunk.text for _shared_key, chunk in shared_items]
        if shared_texts and self._tfidf is not None and self._should_use_tfidf(len(shared_texts)):
            try:
                self._tfidf_matrix = self._tfidf.fit_transform(shared_texts)
                row_by_shared_key = {
                    shared_key: row_index
                    for row_index, (shared_key, _chunk) in enumerate(shared_items)
                }
                self._tfidf_row_for_chunk = {
                    chunk_id: row_by_shared_key[shared_key]
                    for chunk_id, shared_key in self._shared_chunk_keys.items()
                    if shared_key in row_by_shared_key
                }
            except ValueError:
                self._tfidf_matrix = None
        else:
            self._tfidf_matrix = None
            if shared_texts and self._tfidf is not None and self._logger is not None and not self._should_use_tfidf(len(shared_texts)):
                self._logger(
                    source="rag",
                    action="tfidf_skipped",
                    message=(
                        f"Skipped TF-IDF matrix build for {len(shared_texts)} shared chunks "
                        f"covering {len(chunks)} total chunks to reduce memory pressure"
                    ),
                    level="WARNING",
                    details={
                        "shared_chunk_count": len(shared_texts),
                        "chunk_count": len(chunks),
                    },
                )
        if self._logger is not None and len(shared_chunks) < len(chunks):
            self._logger(
                source="rag",
                action="shared_chunk_reuse",
                message=f"Collapsed {len(chunks)} chunk rows into {len(shared_chunks)} shared retrieval payloads",
                details={
                    "chunk_count": len(chunks),
                    "shared_chunk_count": len(shared_chunks),
                    "shared_ratio": round(len(shared_chunks) / max(1, len(chunks)), 4),
                },
            )
        self._embedding_vectors = self._load_or_create_embeddings(chunks, progress)

    def _shared_chunk_key(self, chunk: Chunk) -> str:
        payload = "::".join(
            [
                str(chunk.document_path or ""),
                chunk.source_kind.value if isinstance(chunk.source_kind, SourceDocumentKind) else str(chunk.source_kind or ""),
                str(chunk.source_locator or ""),
                str(chunk.text or ""),
            ]
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def _shared_key_for_chunk_id(self, chunk_id: str) -> str:
        return self._shared_chunk_keys.get(chunk_id, chunk_id)

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        document_path: str | None = None,
        family: DocumentFamily | None = None,
        source_kind: SourceDocumentKind | None = None,
    ) -> list[RetrievalHit]:
        if not self._chunks:
            return []
        query_text = str(query or "").strip()
        if not query_text:
            return []
        self._log_debug(
            action="query",
            message=f"RAG query sent: {query_text}",
            details={
                "query": query_text,
                "top_k": top_k,
                "document_path": document_path or "",
                "family": family.value if family is not None else "",
                "source_kind": source_kind.value if source_kind is not None else "",
            },
        )
        query_hash = self._embedder.embed(query_text)
        query_identifiers = self._query_identifiers(query_text)
        query_tags = extract_component_tokens(query_text)
        query_tfidf = None
        if self._tfidf_matrix is not None:
            try:
                query_tfidf = self._tfidf.transform([query_text])
            except ValueError:
                query_tfidf = None
        query_embedding = self._query_embedding(query_text)

        candidate_ids = list(self._chunks.keys())
        if document_path:
            candidate_ids = list(self._by_document.get(document_path, []))
        if family:
            family_ids = set(self._by_family.get(family, []))
            candidate_ids = [chunk_id for chunk_id in candidate_ids if chunk_id in family_ids]
        if source_kind:
            source_ids = set(self._by_source_kind.get(source_kind, []))
            candidate_ids = [chunk_id for chunk_id in candidate_ids if chunk_id in source_ids]

        scored: list[RetrievalHit] = []
        for chunk_id in candidate_ids:
            chunk = self._chunks[chunk_id]
            breakdown = self._score_chunk(
                chunk_id,
                chunk=chunk,
                query_hash=query_hash,
                query_identifiers=query_identifiers,
                query_tags=query_tags,
                query_tfidf=query_tfidf,
                query_embedding=query_embedding,
                source_kind=source_kind,
            )
            scored.append(
                RetrievalHit(
                    chunk=chunk,
                    score=breakdown["total"],
                    breakdown=breakdown,
                )
            )
        top_hits = sorted(scored, key=lambda hit: hit.score, reverse=True)[:top_k]
        self._log_debug(
            action="results",
            message=f"RAG results received: {len(top_hits)} hits for {query_text}",
            details={
                "query": query_text,
                "top_k": top_k,
                "document_path": document_path or "",
                "family": family.value if family is not None else "",
                "source_kind": source_kind.value if source_kind is not None else "",
                "hits": [
                    {
                        "chunk_id": hit.chunk.id,
                        "document_path": hit.chunk.document_path,
                        "source_locator": hit.chunk.source_locator,
                        "score": hit.score,
                        "breakdown": hit.breakdown,
                        "text": hit.chunk.text,
                    }
                    for hit in top_hits
                ],
            },
        )
        return top_hits

    def evidence_bundle(
        self,
        query: str,
        *,
        top_k: int = 5,
        document_path: str | None = None,
        family: DocumentFamily | None = None,
        source_kind: SourceDocumentKind | None = None,
    ) -> EvidenceBundle:
        hits = self.search(
            query,
            top_k=top_k,
            document_path=document_path,
            family=family,
            source_kind=source_kind,
        )
        digest = hashlib.sha1(
            json.dumps(
                {
                    "query": query,
                    "hits": [hit.chunk.id for hit in hits],
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:16]
        support_ids = [hit.chunk.id for hit in hits if hit.score >= 0.45]
        contradiction_ids = [hit.chunk.id for hit in hits if hit.breakdown.get("exact_identifier", 0.0) <= 0.0 and hit.score < 0.2]
        return EvidenceBundle(
            id=f"bundle:{digest}",
            query=query,
            hits=hits,
            support_evidence_ids=support_ids,
            contradiction_evidence_ids=contradiction_ids,
            metadata={
                "document_path": document_path or "",
                "family": family.value if family is not None else "",
                "source_kind": source_kind.value if source_kind is not None else "",
            },
        )

    def _score_chunk(
        self,
        chunk_id: str,
        *,
        chunk: Chunk,
        query_hash: SparseVector,
        query_identifiers: list[str],
        query_tags: list[str],
        query_tfidf,
        query_embedding: list[float],
        source_kind: SourceDocumentKind | None,
    ) -> dict[str, float]:
        shared_key = self._shared_key_for_chunk_id(chunk_id)
        exact_identifier = 1.0 if set(query_identifiers).intersection(self._identifiers.get(shared_key, [])) else 0.0
        tag_overlap = self._jaccard(query_tags, self._tags.get(shared_key, []))
        tfidf_score = 0.0
        if query_tfidf is not None and self._tfidf_matrix is not None:
            try:
                matrix_index = self._tfidf_row_for_chunk.get(chunk_id)
                if matrix_index is not None:
                    tfidf_score = float((query_tfidf @ self._tfidf_matrix[matrix_index].T).toarray()[0][0])
            except Exception:
                tfidf_score = 0.0
        lexical_score = self._sparse_cosine_similarity(query_hash, self._vectors.get(shared_key, {}))
        embedding_score = 0.0
        chunk_embedding = self._embedding_vectors.get(shared_key)
        if query_embedding and chunk_embedding:
            embedding_score = cosine_similarity(query_embedding, chunk_embedding)
        source_type_prior = 0.0
        if source_kind is not None and chunk.source_kind == source_kind:
            source_type_prior = 1.0
        total = min(
            1.0,
            (0.35 * exact_identifier)
            + (0.15 * tag_overlap)
            + (0.25 * tfidf_score)
            + (0.15 * lexical_score)
            + (0.05 * embedding_score)
            + (0.05 * source_type_prior),
        )
        return {
            "exact_identifier": round(exact_identifier, 4),
            "tag_overlap": round(tag_overlap, 4),
            "tfidf": round(tfidf_score, 4),
            "lexical": round(lexical_score, 4),
            "embedding": round(embedding_score, 4),
            "source_type_prior": round(source_type_prior, 4),
            "total": round(total, 4),
        }

    def _chunk_identifiers(self, chunk: Chunk) -> list[str]:
        values: list[str] = []
        metadata = chunk.metadata or {}
        for key in (
            "tag_name",
            "node_id",
            "key",
            "value",
            "record_key",
            "display_name",
            "canonical_tag",
            "logical_tag",
            "sheet_name",
        ):
            normalized = normalize_identifier(str(metadata.get(key, "") or ""))
            if normalized and normalized not in values:
                values.append(normalized)
        for token in extract_component_tokens(chunk.text):
            normalized = normalize_identifier(token)
            if normalized and normalized not in values:
                values.append(normalized)
        normalized_text = normalize_identifier(chunk.text)
        if normalized_text and len(normalized_text) <= 40 and normalized_text not in values:
            values.append(normalized_text)
        return values

    def _query_identifiers(self, query: str) -> list[str]:
        values: list[str] = []
        normalized_query = normalize_identifier(query)
        if normalized_query:
            values.append(normalized_query)
        for token in extract_component_tokens(query):
            normalized = normalize_identifier(token)
            if normalized and normalized not in values:
                values.append(normalized)
        return values

    def _jaccard(self, left: list[str], right: list[str]) -> float:
        left_set = {normalize_identifier(value) for value in left if normalize_identifier(value)}
        right_set = {normalize_identifier(value) for value in right if normalize_identifier(value)}
        if not left_set or not right_set:
            return 0.0
        intersection = len(left_set.intersection(right_set))
        union = len(left_set.union(right_set))
        return intersection / max(1, union)

    def _sparse_cosine_similarity(self, left: SparseVector, right: SparseVector) -> float:
        if not left or not right:
            return 0.0
        if len(left) > len(right):
            left, right = right, left
        dot = sum(value * right.get(index, 0.0) for index, value in left.items())
        if dot <= 0.0:
            return 0.0
        norm_left = sum(value * value for value in left.values()) ** 0.5
        norm_right = sum(value * value for value in right.values()) ** 0.5
        if norm_left <= 0.0 or norm_right <= 0.0:
            return 0.0
        return dot / (norm_left * norm_right)

    def _query_embedding(self, query: str) -> list[float]:
        if self._llm_client is None:
            return []
        # Disk cache for query embeddings — same SHA1-keyed pattern as
        # chunk embeddings.  Eliminates repeated API calls for the same
        # query text across source types and across runs.
        if self._cache_dir is not None:
            model_name = self._llm_client.config.embedding_model or "embedding"
            cache_key = hashlib.sha1(
                f"{model_name}::query::{query}".encode("utf-8")
            ).hexdigest()
            query_cache_dir = ensure_dir(self._cache_dir / "query_embeddings")
            cache_path = query_cache_dir / f"{cache_key}.json"
            if cache_path.is_file():
                try:
                    data = json.loads(cache_path.read_text(encoding="utf-8"))
                    if isinstance(data, list) and data and isinstance(data[0], (int, float)):
                        return data
                except (json.JSONDecodeError, OSError):
                    pass
        vectors = self._llm_client.embed_texts(
            [query],
            trace_context={
                "workflow": "rag_query_embedding",
                "query": query,
            },
        )
        result = vectors[0] if vectors else []
        # Write cache (atomic: temp file + rename to avoid concurrent corruption)
        if self._cache_dir is not None and result:
            try:
                tmp_path = cache_path.with_suffix(".tmp")
                tmp_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
                tmp_path.rename(cache_path)
            except OSError:
                pass
        return result

    def _embedding_cache_path(self, chunk: Chunk, *, shared_key: str | None = None) -> Path | None:
        if self._cache_dir is None or self._llm_client is None:
            return None
        model_name = self._llm_client.config.embedding_model or "embedding"
        cache_key = shared_key or self._shared_chunk_key(chunk)
        digest = hashlib.sha1(f"{model_name}::{cache_key}".encode("utf-8")).hexdigest()
        return ensure_dir(self._cache_dir) / f"{digest}.json"

    def _load_or_create_embeddings(
        self,
        chunks: list[Chunk],
        progress: ProgressCallback | None = None,
    ) -> dict[str, array]:
        if self._llm_client is None or not self._llm_client.embedding_available():
            if progress:
                progress(
                    100,
                    f"Embedding fallback ready: local hash only across {len({chunk.document_path for chunk in chunks})} documents",
                )
            return {}
        document_chunks: dict[str, dict[str, Chunk]] = {}
        for chunk in chunks:
            shared_key = self._shared_key_for_chunk_id(chunk.id)
            document_chunks.setdefault(chunk.document_path, {}).setdefault(shared_key, chunk)
        document_positions = {
            document_path: (
                index,
                max(1, len(document_chunks)),
                Path(document_path).name or document_path,
            )
            for index, document_path in enumerate(document_chunks.keys(), start=1)
        }
        total_shared_chunks = sum(len(items) for items in document_chunks.values())
        if progress:
            model_name = self._llm_client.resolved_embedding_model() or self._llm_client.config.embedding_model or "embedding"
            progress(
                5,
                f"Embedding [{model_name}] preparing {total_shared_chunks} shared chunks covering {len(chunks)} chunk rows across {len(document_chunks)} documents",
            )
        vectors: dict[str, array] = {}
        missing_by_document: dict[str, list[tuple[str, Chunk]]] = {}
        for document_path, document_chunk_map in document_chunks.items():
            document_chunk_list = list(document_chunk_map.items())
            missing_document_chunks: list[tuple[str, Chunk]] = []
            cached_count = 0
            cached_characters = 0
            cached_dimensions = 0
            for shared_key, chunk in document_chunk_list:
                cache_path = self._embedding_cache_path(chunk, shared_key=shared_key)
                if cache_path is None or not cache_path.exists():
                    missing_document_chunks.append((shared_key, chunk))
                    continue
                try:
                    payload = json.loads(cache_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    missing_document_chunks.append((shared_key, chunk))
                    continue
                embedding = payload.get("embedding", [])
                if isinstance(embedding, list) and embedding:
                    vector = array("f", (float(value) for value in embedding))
                    vectors[shared_key] = vector
                    cached_count += 1
                    cached_characters += len(chunk.text)
                    if not cached_dimensions:
                        cached_dimensions = len(vector)
                else:
                    missing_document_chunks.append((shared_key, chunk))
            if missing_document_chunks:
                missing_by_document[document_path] = missing_document_chunks
            if cached_count and self._logger is not None:
                doc_index, total_docs, short_label = document_positions[document_path]
                self._logger(
                    source="embedding",
                    action="cache_hit",
                    message=f"Embedding cache hit for {short_label}",
                    details={
                        "workflow": "chunk_embedding_cache",
                        "document_path": document_path,
                        "document_name": short_label,
                        "document_index": doc_index,
                        "document_total": total_docs,
                        "cached_chunk_count": cached_count,
                        "cached_characters": cached_characters,
                        "vector_dimensions": cached_dimensions,
                    },
                )
            if progress:
                doc_index, total_docs, short_label = document_positions[document_path]
                cached_count = len(document_chunk_list) - len(missing_document_chunks)
                progress(
                    min(30, 5 + round(doc_index * 25 / max(1, total_docs))),
                    f"Embedding cache {short_label} ({doc_index}/{total_docs}): reused {cached_count}/{len(document_chunk_list)} shared chunks",
                )
        if missing_by_document:
            total_missing_docs = max(1, len(missing_by_document))
            worker_count = min(self._embedding_workers(), len(missing_by_document))

            def embed_document(
                document_path: str,
                missing_chunks: list[tuple[str, Chunk]],
            ) -> tuple[str, list[tuple[str, Chunk]], list[list[float]]]:
                doc_index, total_docs, short_label = document_positions[document_path]
                created_vectors: list[list[float]] = []
                batch_size = self._embedding_batch_size()
                total_batches = max(1, (len(missing_chunks) + batch_size - 1) // batch_size)
                for batch_index, start in enumerate(range(0, len(missing_chunks), batch_size), start=1):
                    batch = missing_chunks[start:start + batch_size]
                    batch_vectors = self._llm_client.embed_texts(
                        [chunk.text for _shared_key, chunk in batch],
                        trace_context={
                            "workflow": "chunk_embedding_create",
                            "document_path": document_path,
                            "document_name": short_label,
                            "document_index": doc_index,
                            "document_total": total_docs,
                            "batch_index": batch_index,
                            "batch_total": total_batches,
                        },
                    )
                    if len(batch_vectors) != len(batch):
                        return document_path, missing_chunks, created_vectors + batch_vectors
                    created_vectors.extend(batch_vectors)
                return document_path, missing_chunks, created_vectors

            missing_items = list(missing_by_document.items())
            if progress and worker_count > 1:
                progress(
                    30,
                    f"Embedding parallel dispatch: {len(missing_items)} documents with {worker_count} workers",
                )
            if worker_count <= 1:
                completed_items = [embed_document(document_path, missing_chunks) for document_path, missing_chunks in missing_items]
            else:
                completed_items = []
                with QoSAwareThreadPoolExecutor(max_workers=worker_count) as executor:
                    futures = [
                        executor.submit(embed_document, document_path, missing_chunks)
                        for document_path, missing_chunks in missing_items
                    ]
                    for future in as_completed(futures):
                        completed_items.append(future.result())

            for missing_index, (document_path, missing_chunks, created_vectors) in enumerate(completed_items, start=1):
                doc_index, total_docs, short_label = document_positions[document_path]
                if len(created_vectors) == len(missing_chunks):
                    for (shared_key, chunk), vector in zip(missing_chunks, created_vectors):
                        dense_vector = array("f", (float(value) for value in vector))
                        vectors[shared_key] = dense_vector
                        cache_path = self._embedding_cache_path(chunk, shared_key=shared_key)
                        if cache_path is not None:
                            cache_path.write_text(
                                json.dumps({"shared_key": shared_key, "chunk_id": chunk.id, "embedding": list(dense_vector)}, ensure_ascii=False),
                                encoding="utf-8",
                            )
                elif self._logger is not None:
                    self._logger(
                        source="embedding",
                        action="response_mismatch",
                        message=f"Embedding response length mismatch for {short_label}",
                        level="ERROR",
                        details={
                            "document_path": document_path,
                            "document_name": short_label,
                            "expected_count": len(missing_chunks),
                            "received_count": len(created_vectors),
                            "chunk_ids": [chunk.id for _shared_key, chunk in missing_chunks],
                        },
                    )
                if progress:
                    ready_count = sum(1 for shared_key in document_chunks[document_path] if shared_key in vectors)
                    progress(
                        30 + round(missing_index * 55 / total_missing_docs),
                        f"Embedding ready {short_label} ({doc_index}/{total_docs}): {ready_count}/{len(document_chunks[document_path])} shared chunks",
                    )
        if progress:
            progress(
                100,
                f"Embedding complete: {len(vectors)}/{total_shared_chunks} shared vectors covering {len(chunks)} chunk rows across {len(document_chunks)} documents",
            )
        return vectors
