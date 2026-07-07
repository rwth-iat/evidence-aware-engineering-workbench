"""Head-to-head model comparison for IEV4PI tasks.

Tests all 6 available models on the same 4 task types,
measuring accuracy and response time.

Usage:
    PYTHONPATH=. .venv/bin/python scripts/benchmark_models.py
"""
from __future__ import annotations

import json, time, statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from iev4pi_transformation_tool.core.llm_client import OpenAICompatibleLLMClient
from iev4pi_transformation_tool.models import LLMBackendConfig

REPO = Path(__file__).resolve().parents[1]
settings = json.loads((REPO / ".iev4pi" / "settings.json").read_text())
base_cfg = settings["llm"]

# All models except embedding-only
MODELS = [
    "mistralai/Mistral-Small-3.2-24B-Instruct-2506",
    "openai/gpt-oss-120b",
    "mistralai/Devstral-Small-2-24B-Instruct-2512",
    "swiss-ai/Apertus-70B-Instruct-2509",
    "mistralai/Mistral-Small-4-119B-2603",
]

# ---------------------------------------------------------------------------
# Task definitions — same prompts for all models
# ---------------------------------------------------------------------------

@dataclass
class TaskResult:
    task: str
    expected: str = ""
    actual: str = ""
    correct: bool = False
    confidence: float = 0.0
    duration_ms: float = 0.0
    tokens: int = 0

@dataclass
class ModelResults:
    model: str
    results: list[TaskResult] = field(default_factory=list)
    total_time_ms: float = 0.0

    @property
    def accuracy(self) -> float:
        return sum(1 for r in self.results if r.correct) / max(1, len(self.results))

    @property
    def avg_time_ms(self) -> float:
        return statistics.mean([r.duration_ms for r in self.results]) if self.results else 0

    @property
    def avg_confidence(self) -> float:
        return statistics.mean([r.confidence for r in self.results if r.confidence > 0]) if self.results else 0


def chat_json_with_time(client: OpenAICompatibleLLMClient, system: str, user: str,
                        model: str) -> tuple[dict, float]:
    """Call LLM and measure wall-clock time."""
    start = time.monotonic()
    result = client.chat_json(system, user)
    elapsed = (time.monotonic() - start) * 1000
    return result, elapsed


# ---------------------------------------------------------------------------
# Task 1: AKZ OCR Error Correction (3 cases)
# ---------------------------------------------------------------------------

AKZ_TASKS = [
    {
        "id": "akz-1",
        "system": "You are an industrial engineer. Two AKZ (equipment tag) strings may refer to the same device. Consider OCR errors and separator variants. Output JSON: {\"is_same\": true/false, \"canonical\": \"...\", \"confidence\": 0.0, \"reasoning\": \"...\"}",
        "user": "AKZ A: 'TI TU10.T41' (from R&I P&ID, temperature sensor)\nAKZ B: 'TI TU1O.TA1' (from PDF OCR, appears near 'Temperaturmessung')\nAre these the same device?",
        "expected_is_same": True,
        "expected_canonical": "TITU10T41",
    },
    {
        "id": "akz-2",
        "system": "You are an industrial engineer. Two AKZ strings may refer to the same device. Consider OCR errors. Output JSON: {\"is_same\": true/false, \"canonical\": \"...\", \"confidence\": 0.0, \"reasoning\": \"...\"}",
        "user": "AKZ A: 'FIC 02.Y30' (from R&I P&ID, flow controller)\nAKZ B: 'F1C O2.Y3O' (from PDF OCR)\nAre these the same device?",
        "expected_is_same": True,
        "expected_canonical": "FIC02Y30",
    },
    {
        "id": "akz-3",
        "system": "You are an industrial engineer. Two AKZ strings may refer to the same device. Output JSON: {\"is_same\": true/false, \"canonical\": \"...\", \"confidence\": 0.0, \"reasoning\": \"...\"}",
        "user": "AKZ A: 'PIC 22.T20' (from R&I P&ID, pressure indicator controller)\nAKZ B: 'PLC 22.F20' (from PDF OCR, appears near 'Druckmessung')\nAre these the same device?",
        "expected_is_same": False,
        "expected_canonical": "PIC22T20",
    },
]

# ---------------------------------------------------------------------------
# Task 2: Cross-Vendor Field Mapping (3 cases)
# ---------------------------------------------------------------------------

FIELD_TASKS = [
    {
        "id": "field-1",
        "system": "You map industrial datasheet fields to standard columns. Available columns with descriptions:\n- nominal_diameter: Nominal pipe/valve diameter (DN100, 100mm)\n- nominal_pressure: Nominal pressure rating (PN40, Class 150)\n- flow_coefficient_kv: Flow coefficient Kv/Kvs (m³/h)\n- body_material: Body/housing material\n- actuator_type: Actuator type (pneumatic, electric)\n- failure_position: Fail-safe position (fail-close, fail-open)\n- supply_pressure: Supply/auxiliary pressure\n- face_to_face_length: Installation length (mm)\n- measurement_range: Measurement range\n- signal_type: Output signal type\n- power_supply: Power/auxiliary supply\nOutput JSON: {\"column\": \"...\", \"confidence\": 0.0, \"reasoning\": \"...\"}",
        "user": "Vendor field: 'Zuluftdruck', Value: '1.4-2.3 bar'\nWhich standard column does this map to?",
        "expected_column": "supply_pressure",
    },
    {
        "id": "field-2",
        "system": "You map industrial datasheet fields to standard columns. Available columns with descriptions:\n- nominal_diameter: Nominal pipe/valve diameter (DN100, 100mm)\n- nominal_pressure: Nominal pressure rating (PN40, Class 150)\n- flow_coefficient_kv: Flow coefficient Kv/Kvs (m³/h)\n- body_material: Body/housing material\n- actuator_type: Actuator type (pneumatic, electric)\n- failure_position: Fail-safe position (fail-close, fail-open)\n- supply_pressure: Supply/auxiliary pressure\n- face_to_face_length: Installation length (mm)\n- measurement_range: Measurement range\n- signal_type: Output signal type\n- power_supply: Power/auxiliary supply\nOutput JSON: {\"column\": \"...\", \"confidence\": 0.0, \"reasoning\": \"...\"}",
        "user": "Vendor field: 'Sicherheitsstellung', Value: 'Feder schließt (fail-close)'\nWhich standard column does this map to?",
        "expected_column": "failure_position",
    },
    {
        "id": "field-3",
        "system": "You map industrial datasheet fields to standard columns. Available columns with descriptions:\n- nominal_diameter: Nominal pipe/valve diameter (DN100, 100mm)\n- nominal_pressure: Nominal pressure rating (PN40, Class 150)\n- flow_coefficient_kv: Flow coefficient Kv/Kvs (m³/h)\n- body_material: Body/housing material\n- actuator_type: Actuator type (pneumatic, electric)\n- failure_position: Fail-safe position (fail-close, fail-open)\n- supply_pressure: Supply/auxiliary pressure\n- face_to_face_length: Installation length (mm)\n- measurement_range: Measurement range\n- signal_type: Output signal type\n- power_supply: Power/auxiliary supply\nOutput JSON: {\"column\": \"...\", \"confidence\": 0.0, \"reasoning\": \"...\"}",
        "user": "Vendor field: 'Einbaulänge', Value: '310 mm'\nWhich standard column does this map to?",
        "expected_column": "face_to_face_length",
    },
]

# ---------------------------------------------------------------------------
# Task 3: UC1 Inconsistency Judgment (2 cases)
# ---------------------------------------------------------------------------

UC1_TASKS = [
    {
        "id": "uc1-1",
        "system": "You check engineering document consistency. A PLT-Stelle (identified by AKZ) appears in R&I P&ID. According to rules: it MUST appear in Stellenplan (min 1) and Klemmenplan (min 1). Output JSON: {\"verdict\": \"consistent|missing_correspondence\", \"missing_in\": [...], \"severity\": \"critical|warning|info\", \"confidence\": 0.0, \"reasoning\": \"...\"}",
        "user": "AKZ: TITU10T41 (TI TU10.T41, temperature sensor)\nOccurrences:\n- R&I: present (DEXPI XML)\n- Stellenplan: NOT FOUND\n- Klemmenplan: NOT FOUND\nWhat is the verdict?",
        "expected_verdict": "missing_correspondence",
        "expected_missing": ["Stellenplan", "Klemmenplan"],
    },
    {
        "id": "uc1-2",
        "system": "You check engineering document consistency. A PLT-Stelle in R&I MUST appear in Stellenplan (min 1) and Klemmenplan (min 1). Output JSON: {\"verdict\": \"consistent|missing_correspondence\", \"missing_in\": [...], \"severity\": \"critical|warning|info\", \"confidence\": 0.0, \"reasoning\": \"...\"}",
        "user": "AKZ: FV38J36 (FV 38.J36, flow control valve)\nOccurrences:\n- R&I: present (DEXPI XML)\n- Stellenplan: present (as 'FV 38 J36')\n- Klemmenplan: present (as 'FV38J36')\nWhat is the verdict?",
        "expected_verdict": "consistent",
        "expected_missing": [],
    },
]

# ---------------------------------------------------------------------------
# Task 4: Profile Generation (1 case — mini)
# ---------------------------------------------------------------------------

PROFILE_TASK = {
    "id": "profile-1",
    "system": "You are generating a vendor datasheet profile. Map vendor field names (German) to standard column names. Standard columns: nominal_diameter, nominal_pressure, flow_coefficient_kv, body_material, seat_material, actuator_type, failure_position, supply_pressure, face_to_face_length, weight, protection_class, explosion_protection. Output JSON: {\"mappings\": {\"vendor_field\": \"standard_column\", ...}, \"confidence\": 0.0, \"reasoning\": \"...\"}",
    "user": "SAMSON Type 3241 Datasheet fields:\n- Nennweite: DN80\n- Nenndruck: PN40\n- Kvs-Wert: 100 m³/h\n- Gehäusewerkstoff: 1.0619\n- Sitzwerkstoff: 1.4006\n- Antriebstyp: Pneumatisch Typ 3271\n- Sicherheitsstellung: Feder schließt\n- Einbaulänge: 310 mm\n- Gewicht: ca. 35 kg\n- Schutzart: IP54\n\nMap each vendor field to the correct standard column.",
    "expected_mappings": {
        "Nennweite": "nominal_diameter",
        "Nenndruck": "nominal_pressure",
        "Kvs-Wert": "flow_coefficient_kv",
        "Gehäusewerkstoff": "body_material",
        "Sitzwerkstoff": "seat_material",
        "Antriebstyp": "actuator_type",
        "Sicherheitsstellung": "failure_position",
        "Einbaulänge": "face_to_face_length",
        "Gewicht": "weight",
        "Schutzart": "protection_class",
    },
}


# ---------------------------------------------------------------------------
# Main benchmark
# ---------------------------------------------------------------------------

def evaluate_akz(result: dict, expected: dict) -> TaskResult:
    is_same = result.get("is_same", None)
    canonical = str(result.get("canonical", "")).upper().replace(".", "").replace("-", "").replace(" ", "")
    expected_canon = expected["expected_canonical"].upper().replace(".", "").replace("-", "").replace(" ", "")
    correct = (is_same == expected["expected_is_same"]) and (expected_canon in canonical or canonical in expected_canon)
    return TaskResult(
        task=expected["id"],
        expected=str(expected["expected_is_same"]),
        actual=str(is_same),
        correct=correct,
        confidence=float(result.get("confidence", 0)),
    )

def evaluate_field(result: dict, expected: dict) -> TaskResult:
    col = str(result.get("column", "")).strip().lower().replace(" ", "_")
    expected_col = expected["expected_column"].lower().replace(" ", "_")
    return TaskResult(
        task=expected["id"],
        expected=expected_col,
        actual=col,
        correct=(col == expected_col),
        confidence=float(result.get("confidence", 0)),
    )

def evaluate_uc1(result: dict, expected: dict) -> TaskResult:
    verdict = str(result.get("verdict", "")).strip().lower()
    expected_v = expected["expected_verdict"].lower()
    correct = verdict == expected_v
    return TaskResult(
        task=expected["id"],
        expected=expected_v,
        actual=verdict,
        correct=correct,
        confidence=float(result.get("confidence", 0)),
    )

def evaluate_profile(result: dict, expected: dict) -> TaskResult:
    mappings = result.get("mappings", {})
    expected_mappings = expected["expected_mappings"]
    if not isinstance(mappings, dict):
        return TaskResult(task=expected["id"], expected=str(len(expected_mappings)), actual="invalid", correct=False)
    correct_count = sum(1 for k, v in expected_mappings.items()
                        if mappings.get(k, "").lower().replace(" ", "_") == v.lower().replace(" ", "_"))
    total = len(expected_mappings)
    return TaskResult(
        task=expected["id"],
        expected=str(total),
        actual=str(correct_count),
        correct=(correct_count >= total * 0.8),
        confidence=float(result.get("confidence", 0)),
    )


def benchmark_model(client: OpenAICompatibleLLMClient, model_id: str) -> ModelResults:
    short_name = model_id.split("/")[-1][:30]
    print(f"\n{'='*70}")
    print(f"Testing: {short_name}")
    print(f"{'='*70}")

    results = ModelResults(model=model_id)
    all_tasks = []

    # AKZ tasks
    for task in AKZ_TASKS:
        raw, elapsed = chat_json_with_time(client, task["system"], task["user"], model_id)
        tr = evaluate_akz(raw, task)
        tr.duration_ms = elapsed
        all_tasks.append(("AKZ", tr))

    # Field mapping tasks
    for task in FIELD_TASKS:
        raw, elapsed = chat_json_with_time(client, task["system"], task["user"], model_id)
        tr = evaluate_field(raw, task)
        tr.duration_ms = elapsed
        all_tasks.append(("Field", tr))

    # UC1 tasks
    for task in UC1_TASKS:
        raw, elapsed = chat_json_with_time(client, task["system"], task["user"], model_id)
        tr = evaluate_uc1(raw, task)
        tr.duration_ms = elapsed
        all_tasks.append(("UC1", tr))

    # Profile task
    raw, elapsed = chat_json_with_time(client, PROFILE_TASK["system"], PROFILE_TASK["user"], model_id)
    tr = evaluate_profile(raw, PROFILE_TASK)
    tr.duration_ms = elapsed
    all_tasks.append(("Profile", tr))

    results.results = [t for _, t in all_tasks]
    results.total_time_ms = sum(t.duration_ms for t in results.results)

    # Print per-task results
    for category, tr in all_tasks:
        icon = "✓" if tr.correct else "✗"
        print(f"  {icon} {category:7s} {tr.task:10s} | conf={tr.confidence:.2f} | {tr.duration_ms:7.0f}ms | expected={tr.expected[:20]:20s} actual={str(tr.actual)[:20]:20s}")

    print(f"  {'─'*60}")
    print(f"  Accuracy: {results.accuracy:.0%} ({sum(1 for r in results.results if r.correct)}/{len(results.results)})")
    print(f"  Avg time: {results.avg_time_ms:.0f}ms | Total: {results.total_time_ms:.0f}ms")
    return results


def main():
    print("=" * 70)
    print("IEV4PI Model Comparison Benchmark")
    print(f"Models: {len(MODELS)} | Tasks: 9 (3 AKZ + 3 Field + 2 UC1 + 1 Profile)")
    print("=" * 70)

    all_model_results: list[ModelResults] = []

    for model_id in MODELS:
        cfg = LLMBackendConfig(
            enabled=True,
            base_url=base_cfg["base_url"],
            chat_model=model_id,
            api_key=base_cfg["api_key"],
            timeout=120.0,
            temperature=0.0,
            max_retries=1,
        )
        client = OpenAICompatibleLLMClient(cfg)
        if not client.available():
            print(f"\n  SKIP {model_id}: not available")
            continue
        mr = benchmark_model(client, model_id)
        all_model_results.append(mr)

    # -------------------------------------------------------------------
    # Final ranking
    # -------------------------------------------------------------------
    print("\n\n" + "=" * 70)
    print("FINAL RANKING (sorted by accuracy, then speed)")
    print("=" * 70)
    print(f"{'Rank':<5} {'Model':<50} {'Acc':>5} {'AvgTime':>8} {'TotalTime':>9} {'AvgConf':>7}")
    print("-" * 90)

    ranked = sorted(all_model_results, key=lambda m: (m.accuracy, -m.avg_time_ms), reverse=True)
    for rank, mr in enumerate(ranked, 1):
        short = mr.model.split("/")[-1][:48]
        print(f"{rank:<5} {short:<50} {mr.accuracy:>4.0%} {mr.avg_time_ms:>7.0f}ms {mr.total_time_ms:>8.0f}ms {mr.avg_confidence:>6.2f}")

    print("-" * 90)
    best = ranked[0]
    print(f"\n  Best overall: {best.model.split('/')[-1]}")
    print(f"  Accuracy: {best.accuracy:.0%} | Avg response: {best.avg_time_ms:.0f}ms")

    # Breakdown by task type
    print("\n\n--- Accuracy by Task Type ---")
    print(f"{'Model':<50} {'AKZ':>6} {'Field':>6} {'UC1':>6} {'Profile':>8}")
    print("-" * 80)
    for mr in all_model_results:
        short = mr.model.split("/")[-1][:48]
        akz_acc = sum(1 for r in mr.results if r.task.startswith("akz") and r.correct) / max(1, sum(1 for r in mr.results if r.task.startswith("akz")))
        field_acc = sum(1 for r in mr.results if r.task.startswith("field") and r.correct) / max(1, sum(1 for r in mr.results if r.task.startswith("field")))
        uc1_acc = sum(1 for r in mr.results if r.task.startswith("uc1") and r.correct) / max(1, sum(1 for r in mr.results if r.task.startswith("uc1")))
        prof_acc = sum(1 for r in mr.results if r.task.startswith("profile") and r.correct) / max(1, sum(1 for r in mr.results if r.task.startswith("profile")))
        print(f"{short:<50} {akz_acc:>5.0%} {field_acc:>5.0%} {uc1_acc:>5.0%} {prof_acc:>7.0%}")

    # Speed ranking
    print("\n\n--- Speed Ranking (avg ms) ---")
    speed_ranked = sorted(all_model_results, key=lambda m: m.avg_time_ms)
    for rank, mr in enumerate(speed_ranked, 1):
        short = mr.model.split("/")[-1][:48]
        print(f"  {rank}. {short:<50} {mr.avg_time_ms:>7.0f}ms (acc: {mr.accuracy:.0%})")

    print("\nDone.")


if __name__ == "__main__":
    main()
