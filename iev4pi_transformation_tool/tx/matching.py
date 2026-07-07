from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from iev4pi_transformation_tool.core.utils import clean_cell, normalize_identifier


@dataclass(frozen=True)
class EntityResolution:
    source_key: str
    target_key: str = ""
    score: float = 0.0
    method: str = "unmatched"
    needs_review_reason: str = ""
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Candidate:
    source_key: str
    target_key: str
    score: float
    evidence: tuple[str, ...] = ()


class WeightedEntityResolver:
    def resolve(
        self,
        sources: list[dict[str, Any]],
        targets: list[dict[str, Any]],
    ) -> dict[str, EntityResolution]:
        source_items = [_ScoredEntity.from_row(row, key_field="record_key") for row in sources if clean_cell(row.get("record_key", ""))]
        target_items = [_ScoredEntity.from_row(row, key_field="device_id") for row in targets if clean_cell(row.get("device_id", ""))]
        if not source_items or not target_items:
            return {item.key: EntityResolution(source_key=item.key) for item in source_items}

        candidates = self._build_candidates(source_items, target_items)
        assignments = self._assign(candidates)
        results: dict[str, EntityResolution] = {}
        for item in source_items:
            candidate = assignments.get(item.key)
            if candidate is None:
                results[item.key] = EntityResolution(
                    source_key=item.key,
                    method="unmatched",
                    needs_review_reason="no_candidate_above_threshold",
                )
                continue
            review_reason = ""
            if candidate.score < 0.95:
                review_reason = "non_exact_global_assignment"
            results[item.key] = EntityResolution(
                source_key=item.key,
                target_key=candidate.target_key,
                score=round(candidate.score, 4),
                method="weighted_bipartite_assignment",
                needs_review_reason=review_reason,
                evidence=candidate.evidence,
            )
        return results

    def _build_candidates(
        self,
        sources: list["_ScoredEntity"],
        targets: list["_ScoredEntity"],
    ) -> list[_Candidate]:
        candidates: list[_Candidate] = []
        for source in sources:
            for target in targets:
                score, evidence = _pair_score(source, target)
                if score >= 0.35:
                    candidates.append(
                        _Candidate(
                            source_key=source.key,
                            target_key=target.key,
                            score=score,
                            evidence=tuple(evidence),
                        )
                    )
        return candidates

    def _assign(self, candidates: list[_Candidate]) -> dict[str, _Candidate]:
        if not candidates:
            return {}
        source_edges: dict[str, list[_Candidate]] = defaultdict(list)
        target_edges: dict[str, list[_Candidate]] = defaultdict(list)
        for candidate in candidates:
            source_edges[candidate.source_key].append(candidate)
            target_edges[candidate.target_key].append(candidate)

        source_to_candidate: dict[str, _Candidate] = {}
        for component_sources, component_targets in self._components(source_edges, target_edges):
            component_candidates = [
                candidate
                for source_key in component_sources
                for candidate in source_edges.get(source_key, [])
                if candidate.target_key in component_targets
            ]
            if not component_candidates:
                continue
            if len(component_sources) <= 8 and len(component_targets) <= 8:
                chosen = self._assign_component_dp(component_sources, component_targets, component_candidates)
            else:
                chosen = self._assign_component_greedy(component_candidates)
            source_to_candidate.update(chosen)
        return source_to_candidate

    def _components(
        self,
        source_edges: dict[str, list[_Candidate]],
        target_edges: dict[str, list[_Candidate]],
    ) -> list[tuple[list[str], list[str]]]:
        remaining_sources = set(source_edges.keys())
        components: list[tuple[list[str], list[str]]] = []
        while remaining_sources:
            start = next(iter(remaining_sources))
            queue = deque([("source", start)])
            component_sources: set[str] = set()
            component_targets: set[str] = set()
            while queue:
                side, key = queue.popleft()
                if side == "source":
                    if key in component_sources:
                        continue
                    component_sources.add(key)
                    remaining_sources.discard(key)
                    for candidate in source_edges.get(key, []):
                        queue.append(("target", candidate.target_key))
                else:
                    if key in component_targets:
                        continue
                    component_targets.add(key)
                    for candidate in target_edges.get(key, []):
                        queue.append(("source", candidate.source_key))
            components.append((sorted(component_sources), sorted(component_targets)))
        return components

    def _assign_component_greedy(self, candidates: list[_Candidate]) -> dict[str, _Candidate]:
        picked: dict[str, _Candidate] = {}
        used_targets: set[str] = set()
        for candidate in sorted(candidates, key=lambda item: (-item.score, item.source_key, item.target_key)):
            if candidate.source_key in picked or candidate.target_key in used_targets:
                continue
            picked[candidate.source_key] = candidate
            used_targets.add(candidate.target_key)
        return picked

    def _assign_component_dp(
        self,
        sources: list[str],
        targets: list[str],
        candidates: list[_Candidate],
    ) -> dict[str, _Candidate]:
        target_index = {target: index for index, target in enumerate(targets)}
        candidate_map: dict[str, list[_Candidate]] = defaultdict(list)
        for candidate in candidates:
            candidate_map[candidate.source_key].append(candidate)

        memo: dict[tuple[int, int], tuple[float, list[_Candidate]]] = {}

        def solve(source_idx: int, used_mask: int) -> tuple[float, list[_Candidate]]:
            key = (source_idx, used_mask)
            if key in memo:
                return memo[key]
            if source_idx >= len(sources):
                memo[key] = (0.0, [])
                return memo[key]
            best_score, best_assignment = solve(source_idx + 1, used_mask)
            source_key = sources[source_idx]
            for candidate in candidate_map.get(source_key, []):
                bit = 1 << target_index[candidate.target_key]
                if used_mask & bit:
                    continue
                next_score, next_assignment = solve(source_idx + 1, used_mask | bit)
                total_score = candidate.score + next_score
                if total_score > best_score:
                    best_score = total_score
                    best_assignment = [candidate, *next_assignment]
            memo[key] = (best_score, best_assignment)
            return memo[key]

        _score, assignment = solve(0, 0)
        return {candidate.source_key: candidate for candidate in assignment}


@dataclass(frozen=True)
class _ScoredEntity:
    key: str
    canonical_tag: str = ""
    normalized_tags: tuple[str, ...] = ()
    label_tokens: tuple[str, ...] = ()
    context_tokens: tuple[str, ...] = ()
    fields: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: dict[str, Any], *, key_field: str) -> "_ScoredEntity":
        normalized_tags = _ordered_unique(
            normalize_identifier(row.get(field, ""))
            for field in ("canonical_tag", "tag", "logical_tag", "messstelle", "signal_tag", "plt_stelle", "device_id")
        )
        return cls(
            key=clean_cell(row.get(key_field, "")),
            canonical_tag=clean_cell(row.get("canonical_tag", "")),
            normalized_tags=tuple(normalized_tags),
            label_tokens=tuple(_tokenize(row.get("display_name", ""))),
            context_tokens=tuple(
                _ordered_unique(
                    token
                    for field in ("device_information", "project", "funktion", "beschreibung", "art", "address", "position")
                    for token in _tokenize(row.get(field, ""))
                )
            ),
            fields={clean_cell(key): clean_cell(value) for key, value in row.items()},
        )


def _pair_score(source: _ScoredEntity, target: _ScoredEntity) -> tuple[float, list[str]]:
    score = 0.0
    evidence: list[str] = []
    tag_overlap = sorted(set(source.normalized_tags).intersection(target.normalized_tags))
    if tag_overlap:
        if any(tag == normalize_identifier(source.canonical_tag) == normalize_identifier(target.canonical_tag) for tag in tag_overlap):
            score += 0.7
            evidence.append(f"canonical_tag={tag_overlap[0]}")
        else:
            score += 0.55
            evidence.append(f"shared_tag={tag_overlap[0]}")

    label_overlap = _jaccard(set(source.label_tokens), set(target.label_tokens))
    if label_overlap:
        score += 0.15 * label_overlap
        evidence.append(f"label_overlap={label_overlap:.2f}")

    context_overlap = _jaccard(set(source.context_tokens), set(target.context_tokens))
    if context_overlap:
        score += 0.2 * context_overlap
        evidence.append(f"context_overlap={context_overlap:.2f}")

    if normalize_identifier(source.fields.get("device_information", "")) == normalize_identifier(target.fields.get("device_information", "")) and clean_cell(source.fields.get("device_information", "")):
        score += 0.1
        evidence.append("device_information_exact")

    return min(score, 1.0), evidence


def _ordered_unique(values: Any) -> list[str]:
    items: list[str] = []
    for value in values:
        cleaned = clean_cell(value)
        if cleaned and cleaned not in items:
            items.append(cleaned)
    return items


def _tokenize(value: Any) -> list[str]:
    normalized = normalize_identifier(value)
    if not normalized:
        return []
    return [part for part in normalized.split("_") if part]


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = left.union(right)
    if not union:
        return 0.0
    return len(left.intersection(right)) / len(union)
