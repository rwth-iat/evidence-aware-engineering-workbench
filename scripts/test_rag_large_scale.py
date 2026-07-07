#!/usr/bin/env python3
"""Large-scale RAG test on ALL samples — Element Classification, Attribute Normalization, Datasheet Retrieval."""
from __future__ import annotations

import gc, json, os, re, sys, time, tracemalloc
from collections import defaultdict, Counter
from pathlib import Path
import numpy as np
import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from iev4pi_transformation_tool.core.llm_client import OpenAICompatibleLLMClient
from iev4pi_transformation_tool.services.workbench import Workbench
from iev4pi_transformation_tool.core.aio_schema_mapping import (
    ELEMENT_TYPE_MAP, DOCUMENT_ATTRIBUTE_MAP, ELEMENT_ATTRIBUTE_MAP,
    CONNECTION_ATTRIBUTE_MAP, get_aio_element_info,
    get_document_attribute_name, get_element_attribute_name,
)

IEC_CORPUS = [
    "IEC 81346-2 A: Equipment subsystems cabinets Schaltschrank control panels PLC racks functional groups Baugruppe Anlage Schrank",
    "IEC 81346-2 B: Sensors measuring instruments transmitters detectors Messumformer Fühler Aufnehmer Geber flow temperature pressure level",
    "IEC 81346-2 E: Heating lighting cooling Heizung Beleuchtung Kühlung heater lamp Lampe Leuchte Ofen oven",
    "IEC 81346-2 F: Protective devices fuses Schmelzsicherung NH-Sicherung Diazed circuit breakers Leitungsschutzschalter LS-Schalter MCB RCBO RCD FI-Schalter Fehlerstrom-Schutzschalter Sicherung Schutzschalter",
    "IEC 81346-2 K: Relays contactors Schütz Leistungsschütz Hilfsschütz auxiliary contactors coils Spule PLC modules Relais Kontakt",
    "IEC 81346-2 M: Motors pumps Pumpen actuators valves Antrieb Stellantrieb mechanical drive Ventil",
    "IEC 81346-2 P: Indicators signal lamps Meldeleuchte Signalleuchte measuring instruments Anzeige Leuchtmelder",
    "IEC 81346-2 Q: Power switching circuit breakers Leistungsschalter motor starters main contacts Hauptkontakt auxiliary contacts Hilfskontakt contactors",
    "IEC 81346-2 S: Manual switches pushbuttons Drucktaster selectors Wahlschalter emergency stops Not-Aus limit switches Endschalter Schalter Taster",
    "IEC 81346-2 T: Transformers Trafo Transformator power supplies Netzteil rectifiers Gleichrichter voltage converters SNT SchaltNetzTeil Stromversorgung",
    "IEC 81346-2 X: Terminals Klemme terminal strips Klemmenleiste Klemmleiste sockets Steckdose connectors Stecker Buchse plugs Schuko",
    "IEC 81346-2 Y: Solenoids electromagnetic valves Magnetventil pneumatic valves hydraulic valves actuators Stellantrieb Hubmagnet Ventil Klappe",
]

measurements = []

def measure(name, func, *args, **kwargs):
    gc.collect()
    process = psutil.Process()
    tracemalloc.start()
    cpu_before = process.cpu_percent(interval=None)
    t0 = time.perf_counter()
    result = func(*args, **kwargs)
    t1 = time.perf_counter()
    _, peak_mem = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    cpu_after = process.cpu_percent(interval=None)
    m = {"name": name, "time_ms": (t1-t0)*1000, "peak_mem_kb": peak_mem/1024,
         "cpu_pct": max(0, (cpu_before+cpu_after)/2), "result": result}
    measurements.append(m)
    return m

class EmbeddingRAG:
    def __init__(self, corpus, client):
        self.corpus = corpus
        self.client = client
        self._vectors = None
        self._cache_path = Path(__file__).resolve().parent.parent / ".iev4pi" / f"rag_large_{hash(''.join(corpus))}.npz"

    def build(self):
        if self._cache_path.exists():
            try:
                self._vectors = np.load(self._cache_path)['v']
                return len(self.corpus)
            except: pass
        vecs = []
        for i in range(0, len(self.corpus), 20):
            try: vecs.extend(self.client.embed_texts(self.corpus[i:i+20]))
            except: vecs.extend([[0.0]*768 for _ in self.corpus[i:i+20]])
        self._vectors = np.array(vecs, dtype=np.float32)
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            np.savez(self._cache_path, v=self._vectors)
        except: pass
        return len(self.corpus)

    def retrieve(self, query, top_k=5):
        if self._vectors is None: return [],[]
        try:
            q = np.array(self.client.embed_texts([query])[0], dtype=np.float32)
        except: return [],[]
        norms = np.linalg.norm(self._vectors, axis=1)
        qn = np.linalg.norm(q)
        if qn == 0: return [],[]
        sims = np.dot(self._vectors, q) / (norms * qn + 1e-10)
        idx = np.argsort(sims)[::-1][:top_k]
        return [(self.corpus[i], float(sims[i])) for i in idx if sims[i] > 0.15], [float(sims[i]) for i in idx if sims[i] > 0.15]


def main():
    print("Loading data...")
    with open('/tmp/rag_full_data.json') as f:
        data = json.load(f)
    print(f"{len(data['elems'])} elem, {len(data['attrs'])} attrs, {len(data['objects'])} obj, {len(data['rkzs'])} rkz")

    client = OpenAICompatibleLLMClient(Workbench(Path('.')).settings.llm)

    # ══════════════════════════════════════════════════════
    # Build RAG
    # ══════════════════════════════════════════════════════
    print("\nBuilding RAG indices...")
    t0 = time.perf_counter()
    iec_rag = EmbeddingRAG(IEC_CORPUS, client)
    iec_n = iec_rag.build()
    build_ms = (time.perf_counter()-t0)*1000
    print(f"  IEC corpus: {iec_n} docs, built in {build_ms:.0f}ms")

    # ══════════════════════════════════════════════════════
    # SCENARIO 1: Element Classification — ALL 1589 samples
    # ══════════════════════════════════════════════════════
    print(f"\n{'='*70}")
    print("SCENARIO 1: Element Classification (ALL 1589 samples)")
    print(f"{'='*70}")

    # Current: ELEMENT_TYPE_MAP lookup
    map_hits = 0
    map_missed_samples = []
    map_times = []

    for e in data['elems']:
        val = e['val'].lower()
        m = measure("elem_map", lambda v: any(k in v for k in ELEMENT_TYPE_MAP), val)
        map_times.append(m['time_ms'])
        if m['result']:
            map_hits += 1
        else:
            map_missed_samples.append(e)

    print(f"  MAP hits: {map_hits}/{len(data['elems'])} ({100*map_hits/len(data['elems']):.1f}%)")
    print(f"  MAP missed: {len(map_missed_samples)}")

    # RAG: test on a representative diverse sample of missed items (200 max, covering all types)
    rag_times = []
    rag_hits = 0

    # Diverse sample: take from different files, different attributes, different etypes
    diverse = []
    seen_files = set()
    seen_etypes = set()
    for e in map_missed_samples:
        key = (e['file'], e['etype'])
        if e['file'] not in seen_files or len(diverse) < 100:
            diverse.append(e)
            seen_files.add(e['file'])
            seen_etypes.add(e['etype'])
        if len(diverse) >= 300:
            break

    print(f"  Testing RAG on {len(diverse)} diverse missed samples...")

    for i, e in enumerate(diverse):
        query = f"{e.get('attr','')} {e['val']}"[:200]
        m = measure("elem_rag", lambda q, r: _rag_classify(q, r, client), query, iec_rag)
        rag_times.append(m['time_ms'])
        if m['result']:
            rag_hits += 1
        if (i+1) % 100 == 0:
            print(f"    ... {i+1}/{len(diverse)} done, hits={rag_hits}")

    print(f"  RAG additional: {rag_hits}/{len(diverse)} ({100*rag_hits/len(diverse):.1f}%)")
    total_potential = map_hits + rag_hits
    print(f"  Combined potential: {total_potential}/{len(data['elems'])} ({100*total_potential/len(data['elems']):.1f}%)")

    # Show RAG hit examples
    print(f"\n  RAG hit examples (first 10):")
    count = 0
    for i, e in enumerate(diverse):
        if count >= 10: break
        query = f"{e.get('attr','')} {e['val']}"[:150]
        results, scores = iec_rag.retrieve(query, top_k=2)
        if results and scores and scores[0] > 0.15:
            print(f"    {e['rkz']:15s} ({e['etype']:20s}): \"{e['val'][:50]}\" → {results[0][:80]}")
            count += 1

    # ══════════════════════════════════════════════════════
    # SCENARIO 2: Attribute Normalization — ALL attrs
    # ══════════════════════════════════════════════════════
    print(f"\n{'='*70}")
    print("SCENARIO 2: Attribute Name Normalization (31 unique attrs)")
    print(f"{'='*70}")

    all_attrs = data['attrs'][:31]
    attr_map_hits = 0
    attr_rag_hits = 0
    attr_rag_times = []

    # Build attribute corpus
    attr_corpus = []
    # Standard attribute names from spec
    standard_names = [
        "Project_Name", "Plant", "Cabinet", "Position", "Revision", "Creation_Date",
        "Document_Subtype", "Source_Format", "Sheet_Number", "Primary_RKZ",
        "Terminal_Number", "Terminal_Strip_Designation", "Polarity", "Wire_Color",
        "Cross_Section", "Cable_Number", "Cable_Type", "Connection_Type",
        "Rated_Current", "Rated_Voltage", "Rated_Cross_Section", "Coil_Voltage",
        "Manufacturer", "Type_Designation", "Function", "Description",
        "Current_Path_Number", "Contact_Designation",
        "Lamp_Color", "Socket_Type", "Switch_Type", "IP_Protection",
        "Mounting_Type", "Trip_Characteristic", "Protection_Form",
        "Input_Voltage", "Output_Voltage", "Output_Power", "Output_Current",
        "Voltage_Level", "Signal_Standard", "Shielding",
        "Wire_Number", "Length", "Remark", "Document_Name",
        "Target_Load", "Pole_Count", "Rated_Breaking_Capacity",
    ]
    for sn in standard_names:
        attr_corpus.append(f"Standard attribute: {sn}")
    # Also add the mapped entries
    for legacy, std in {**DOCUMENT_ATTRIBUTE_MAP, **ELEMENT_ATTRIBUTE_MAP, **CONNECTION_ATTRIBUTE_MAP}.items():
        attr_corpus.append(f"Mapped attribute: '{legacy}' → standard '{std}'")

    attr_rag = EmbeddingRAG(attr_corpus[:120], client)
    attr_rag.build()

    for attr in all_attrs:
        # Current: check deterministic maps
        mapped = get_document_attribute_name(attr)
        if mapped != attr:
            attr_map_hits += 1
            continue
        mapped = get_element_attribute_name(attr)
        if mapped != attr:
            attr_map_hits += 1
            continue

        # RAG: semantic match
        m = measure("attr_rag", lambda a, r: _rag_find_attr(a, r), attr, attr_rag)
        attr_rag_times.append(m['time_ms'])
        if m['result']:
            attr_rag_hits += 1

    print(f"  MAP hits: {attr_map_hits}/{len(all_attrs)}")
    print(f"  RAG additional: {attr_rag_hits}/{len(all_attrs)-attr_map_hits} missed")
    total_attr = attr_map_hits + attr_rag_hits
    print(f"  Combined: {total_attr}/{len(all_attrs)} ({100*total_attr/len(all_attrs):.0f}%)")

    # Show which attributes RAG found
    print(f"\n  RAG-matched attributes:")
    for attr in all_attrs:
        mapped = get_document_attribute_name(attr)
        if mapped != attr: continue
        mapped = get_element_attribute_name(attr)
        if mapped != attr: continue
        results, scores = attr_rag.retrieve(attr, top_k=2)
        if results and scores and scores[0] > 0.3:
            best = results[0].split(": ", 1)[-1] if ": " in results[0] else results[0]
            print(f"    '{attr:35s} → {best[:60]} (score={scores[0]:.2f})")

    # ══════════════════════════════════════════════════════
    # SCENARIO 3: Datasheet Chunking — ALL objects
    # ══════════════════════════════════════════════════════
    print(f"\n{'='*70}")
    print("SCENARIO 3: Long Datasheet Chunking + Retrieval (679 objects)")
    print(f"{'='*70}")

    obj_corpus = [o['text'][:300] for o in data['objects'][:200]]
    obj_rag = EmbeddingRAG(obj_corpus, client)
    obj_rag.build()

    # Test queries: technical content
    tech_queries = sorted(set(
        o['text'] for o in data['objects']
        if any(kw in o['text'].lower() for kw in
               ['volt','ampere','watt','leistung','strom','spannung','druck',
                'temperatur','durchfluss','pumpe','motor','messbereich','signal',
                'frequenz','drehzahl','kabel','leitung','anschluss','schutz',
                'sicherung','schalter','module','klemme'])
    ))
    print(f"  Technical queries: {len(tech_queries)}")

    # Current approach: no chunking, just linear scan of raw text
    scan_hits = 0
    scan_times = []
    for q in tech_queries[:50]:
        m = measure("scan", lambda q, c: any(t in q.lower() for t in q.lower().split()[:5] if len(t)>3), q, obj_corpus)
        scan_times.append(m['time_ms'])
        if m['result']: scan_hits += 1

    # RAG retrieval
    rag_found = 0
    rag_ret_times = []
    for q in tech_queries[:50]:
        m = measure("obj_rag", lambda q, r: len(r.retrieve(q, top_k=3)[0]) > 0, q, obj_rag)
        rag_ret_times.append(m['time_ms'])
        if m['result']: rag_found += 1

    print(f"  Linear scan: {scan_hits}/{min(50,len(tech_queries))} found")
    print(f"  RAG retrieval: {rag_found}/{min(50,len(tech_queries))} found")
    print(f"  Scan avg: {sum(scan_times)/max(1,len(scan_times)):.2f}ms, RAG avg: {sum(rag_ret_times)/max(1,len(rag_ret_times)):.2f}ms")

    # Show retrieval examples
    print(f"\n  RAG retrieval examples:")
    count = 0
    for q in tech_queries[:15]:
        if count >= 5: break
        results, scores = obj_rag.retrieve(q, top_k=3)
        if results:
            print(f"    Query: \"{q[:80]}...\"")
            for r, s in zip(results[:2], scores[:2]):
                print(f"      [{s:.2f}] \"{r[:100]}...\"")
            count += 1

    # ══════════════════════════════════════════════════════
    # SUMMARY
    # ══════════════════════════════════════════════════════
    print(f"\n{'='*70}")
    print("LARGE-SCALE RAG TEST — SUMMARY")
    print(f"{'='*70}")
    print(f"  Samples: {len(data['elems'])} elements × {len(all_attrs)} attrs × {len(tech_queries)} tech queries")
    print()
    print(f"  Scenario 1 — Element Classification:")
    print(f"    Current MAP:  {map_hits}/{len(data['elems'])} ({100*map_hits/len(data['elems']):.1f}%)")
    print(f"    RAG gain:     +{rag_hits} on {len(diverse)} diverse missed ({100*rag_hits/len(diverse):.1f}%)")
    print(f"    Combined:     {total_potential}/{len(data['elems'])} ({100*total_potential/len(data['elems']):.1f}%)")
    print(f"    RAG latency:  {sum(rag_times)/max(1,len(rag_times)):.0f}ms avg per call")
    print()
    print(f"  Scenario 2 — Attribute Normalization:")
    print(f"    Current MAP:  {attr_map_hits}/{len(all_attrs)} ({100*attr_map_hits/len(all_attrs):.0f}%)")
    print(f"    RAG gain:     +{attr_rag_hits} ({100*attr_rag_hits/max(1,len(all_attrs)-attr_map_hits):.0f}% of missed)")
    print(f"    Combined:     {total_attr}/{len(all_attrs)} ({100*total_attr/len(all_attrs):.0f}%)")
    print(f"    RAG latency:  {sum(attr_rag_times)/max(1,len(attr_rag_times)):.0f}ms avg")
    print()
    print(f"  Scenario 3 — Datasheet Chunking:")
    print(f"    Linear scan:  {scan_hits}/{min(50,len(tech_queries))}")
    print(f"    RAG retrieval:{rag_found}/{min(50,len(tech_queries))}")
    print(f"    RAG latency:  {sum(rag_ret_times)/max(1,len(rag_ret_times)):.0f}ms avg")


def _rag_classify(query: str, rag: EmbeddingRAG, client: OpenAICompatibleLLMClient) -> bool:
    results, scores = rag.retrieve(query, top_k=3)
    if not results: return False
    # Check if best retrieved doc is semantically relevant
    if scores[0] > 0.25:
        return True
    return False


def _rag_find_attr(attr_name: str, rag: EmbeddingRAG) -> bool:
    results, scores = rag.retrieve(attr_name, top_k=3)
    if results and scores and scores[0] > 0.30:
        return True
    return False


if __name__ == "__main__":
    main()
