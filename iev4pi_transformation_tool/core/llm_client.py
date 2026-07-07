from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
from typing import Any, Callable

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - optional dependency at runtime
    OpenAI = None

from iev4pi_transformation_tool.models import LLMBackendConfig


_JSON_FENCE_PREFIX = re.compile(r"^\s*```(?:json)?\s*", re.IGNORECASE)
_JSON_FENCE_SUFFIX = re.compile(r"\s*```\s*$", re.IGNORECASE)


class OpenAICompatibleLLMClient:
    def __init__(
        self,
        config: LLMBackendConfig,
        logger: Callable[..., Any] | None = None,
    ) -> None:
        self.config = config
        self._client: Any | None = None
        self._model_ids_cache: list[str] | None = None
        self._logger = logger
        self._client_lock = threading.RLock()
        self._chat_cache = None
        self._embedding_cache = None

    def _log_debug(
        self,
        *,
        source: str,
        action: str,
        message: str,
        level: str = "INFO",
        details: dict[str, Any] | None = None,
    ) -> None:
        if self._logger is None:
            return
        self._logger(
            source=source,
            action=action,
            message=message,
            level=level,
            details=details,
        )

    def resolved_api_key(self) -> str:
        return (
            os.getenv("IEVPI_LLM_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or self.config.api_key
            or "not-needed"
        )

    def available(self) -> bool:
        return bool(
            OpenAI is not None
            and self.config.enabled
            and self.config.base_url
            and self.config.chat_model
        )

    def embedding_available(self) -> bool:
        return bool(
            OpenAI is not None
            and self.config.enabled
            and self.config.base_url
            and self.resolved_embedding_model()
        )

    def list_models(self) -> list[str]:
        if self._model_ids_cache is not None:
            return list(self._model_ids_cache)
        with self._client_lock:
            if self._model_ids_cache is not None:
                return list(self._model_ids_cache)
            client = self._openai_client()
            if client is None:
                return []
            try:
                response = client.models.list()
            except Exception:
                return []
            self._model_ids_cache = [str(model.id) for model in getattr(response, "data", []) if getattr(model, "id", None)]
        return list(self._model_ids_cache)

    def resolved_embedding_model(self) -> str:
        configured = (self.config.embedding_model or "").strip()
        if configured and configured.lower() != "local-hash-768":
            return configured
        models = self.list_models()
        preferred = [
            model_id
            for model_id in models
            if "qwen3-embedding-8b" in model_id.lower()
        ]
        if preferred:
            return preferred[0]
        generic = [model_id for model_id in models if "embedding" in model_id.lower()]
        return generic[0] if generic else ""

    def runtime_probe(self) -> dict[str, object]:
        models = self.list_models()
        return {
            "available": self.available(),
            "embedding_available": self.embedding_available(),
            "base_url": self.config.base_url,
            "chat_model": self.config.chat_model,
            "vlm_model": self.config.vlm_model,
            "embedding_model": self.resolved_embedding_model() or self.config.embedding_model,
            "model_count": len(models),
            "models": models,
        }

    def chat_json(
        self,
        system_prompt: str,
        user_prompt: str | None = None,
        *,
        trace_context: dict[str, Any] | None = None,
    ) -> dict[str, object]:
        if user_prompt is None:
            user_prompt = system_prompt
            system_prompt = "Return ONLY valid JSON."
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return self.chat_json_messages(messages, trace_context=trace_context)

    def chat_json_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        trace_context: dict[str, Any] | None = None,
    ) -> dict[str, object]:
        selected_model = model or self.config.chat_model
        context = dict(trace_context or {})
        cache_key = self._api_cache_key(
            "chat_json_messages",
            {
                "base_url": self.config.base_url,
                "model": selected_model,
                "temperature": self.config.temperature,
                "messages": messages,
                "response_format": "json_object",
            },
        )
        cached = self._chat_disk_cache().get(cache_key)
        if isinstance(cached, dict):
            self._log_debug(
                source="llm",
                action="cache_hit",
                message=f"LLM disk cache hit for {selected_model}",
                details={
                    **context,
                    "model": selected_model,
                    "cache_key": cache_key,
                    "messages": messages,
                    "parsed_response": cached,
                },
            )
            return copy.deepcopy(cached)

        self._log_debug(
            source="llm",
            action="request",
            message=f"LLM request sent to {selected_model}",
            details={
                **context,
                "model": selected_model,
                "messages": messages,
            },
        )
        client = self._openai_client()
        if client is None:
            self._log_debug(
                source="llm",
                action="request_skipped",
                message="LLM request skipped because client is unavailable",
                level="WARNING",
                details={
                    **context,
                    "model": selected_model,
                    "messages": messages,
                },
            )
            return {}
        try:
            response = client.chat.completions.create(
                model=selected_model,
                temperature=self.config.temperature,
                response_format={"type": "json_object"},
                messages=messages,
            )
        except TypeError:
            try:
                response = client.chat.completions.create(
                    model=selected_model,
                    temperature=self.config.temperature,
                    messages=messages,
                )
            except Exception as exc:
                self._log_debug(
                    source="llm",
                    action="response_error",
                    message=f"LLM request failed: {exc}",
                    level="ERROR",
                    details={
                        **context,
                        "model": selected_model,
                        "messages": messages,
                    },
                )
                return {}
        except Exception as exc:
            self._log_debug(
                source="llm",
                action="response_error",
                message=f"LLM request failed: {exc}",
                level="ERROR",
                details={
                    **context,
                    "model": selected_model,
                    "messages": messages,
                },
            )
            return {}
        content = getattr(response.choices[0].message, "content", "") or "{}"
        parsed = self._parse_json_response(content)
        self._log_debug(
            source="llm",
            action="response",
            message=f"LLM response received from {selected_model}",
            details={
                **context,
                "model": selected_model,
                "messages": messages,
                "raw_response": content,
                "parsed_response": parsed,
            },
        )
        if parsed:
            self._chat_disk_cache()[cache_key] = parsed
        return parsed

    def embed_texts(
        self,
        texts: list[str],
        *,
        model: str | None = None,
        trace_context: dict[str, Any] | None = None,
    ) -> list[list[float]]:
        cleaned = [str(text or "") for text in texts]
        selected_model = model or self.resolved_embedding_model()
        context = dict(trace_context or {})
        request_summary = {
            **context,
            "model": selected_model,
            "input_count": len(cleaned),
            "input_characters": sum(len(text) for text in cleaned),
        }
        if not cleaned or not self.embedding_available():
            self._log_debug(
                source="embedding",
                action="request_skipped",
                message="Embedding request skipped because inputs are empty or embedding is unavailable",
                level="WARNING",
                details=request_summary,
            )
            return []
        cache_key = self._api_cache_key(
            "embed_texts",
            {
                "base_url": self.config.base_url,
                "model": selected_model,
                "texts": cleaned,
            },
        )
        cached = self._embedding_disk_cache().get(cache_key)
        if (
            isinstance(cached, list)
            and len(cached) == len(cleaned)
            and all(isinstance(item, list) for item in cached)
        ):
            vectors = [[float(value) for value in item] for item in cached]
            self._log_debug(
                source="embedding",
                action="cache_hit",
                message=f"Embedding disk cache hit for {selected_model}",
                details={
                    **request_summary,
                    "cache_key": cache_key,
                    "output_count": len(vectors),
                    "output_dimensions": len(vectors[0]) if vectors else 0,
                },
            )
            return vectors
        client = self._openai_client()
        if client is None:
            self._log_debug(
                source="embedding",
                action="request_skipped",
                message="Embedding request skipped because client is unavailable",
                level="WARNING",
                details=request_summary,
            )
            return []
        self._log_debug(
            source="embedding",
            action="request",
            message=f"Embedding request sent to {selected_model}",
            details=request_summary,
        )
        try:
            response = client.embeddings.create(
                model=selected_model,
                input=cleaned,
            )
        except Exception as exc:
            self._log_debug(
                source="embedding",
                action="response_error",
                message=f"Embedding request failed: {exc}",
                level="ERROR",
                details=request_summary,
            )
            return []
        vectors: list[list[float]] = []
        for item in getattr(response, "data", []):
            embedding = getattr(item, "embedding", None)
            if isinstance(embedding, list):
                vectors.append([float(value) for value in embedding])
        self._log_debug(
            source="embedding",
            action="response",
            message=f"Embedding response received from {selected_model}",
            details={
                **request_summary,
                "output_count": len(vectors),
                "output_dimensions": len(vectors[0]) if vectors else 0,
                "response_received": bool(vectors),
            },
        )
        if len(vectors) == len(cleaned):
            self._embedding_disk_cache()[cache_key] = vectors
            return vectors
        return []

    def _openai_client(self):
        if OpenAI is None or not self.config.enabled or not self.config.base_url:
            return None
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    if OpenAI is None:
                        return None
                    self._client = OpenAI(
                        base_url=self.config.base_url,
                        api_key=self.resolved_api_key(),
                        timeout=self.config.timeout,
                        max_retries=self.config.max_retries,
                    )
        return self._client

    def _parse_json_response(self, raw: str) -> dict[str, object]:
        cleaned = _JSON_FENCE_PREFIX.sub("", raw.strip())
        cleaned = _JSON_FENCE_SUFFIX.sub("", cleaned)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _chat_disk_cache(self):
        if self._chat_cache is None:
            from iev4pi_transformation_tool.core.disk_cache import DiskDict
            self._chat_cache = DiskDict("llm_api_chat_json")
        return self._chat_cache

    def _embedding_disk_cache(self):
        if self._embedding_cache is None:
            from iev4pi_transformation_tool.core.disk_cache import DiskDict
            self._embedding_cache = DiskDict("llm_api_embeddings")
        return self._embedding_cache

    def _api_cache_key(self, method: str, payload: dict[str, Any]) -> str:
        raw = json.dumps(
            {"method": method, **payload},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
