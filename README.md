# Evidence-Aware Engineering Workbench

Evidence-Aware Engineering Workbench is a desktop workbench for provenance-preserving information extraction from heterogeneous process automation engineering documents. It converts source artifacts such as P&ID PDFs, DEXPI XML files, Excel workbooks, wiring and instrument documents, and IFC or 3D model data into reviewed, AAS-ready workbook outputs while keeping cell-level evidence references.

The project accompanies the IECON 2026 paper draft "Provenance-Preserving Information Extraction from Engineering Documents for AAS-Ready Process Automation Data". The paper material itself is not part of this repository.

## What This Repository Contains

- A PyQt6 desktop workbench for document scanning, extraction, review, and export.
- Evidence-aware data models for source paths, page or sheet locators, bounding boxes, snippets, scores, evidence type, and extraction engine.
- Deterministic-first extraction and normalization pipelines for engineering document families.
- Optional OCR, retrieval, large language model, and vision-language model assistance.
- Source preview support for PDF regions, XML locations, spreadsheet cells, text, JSON, CSV, and IFC-related evidence.
- Standardized Excel templates and AAS/ontology export helpers.
- A public companion document bundle in `Documents/`.
- Public Excel artifact exports in `Exports/Excel/`.

## What Is Not Included

The public repository intentionally excludes private and runtime-specific artifacts:

- `Paper/` manuscript drafts, screenshots, reviews, and submission material.
- `.iev4pi/` local SQLite state, caches, debug logs, embedding caches, and model caches.
- `Exports/` runtime outputs other than the committed `Exports/Excel/` companion artifacts.
- `data/filled_templates/` generated workbooks and provenance bundles.
- `tests/` and local validation result JSON files.
- `docs/` working notes and internal analysis documents.
- API keys, passwords, private endpoints, machine-specific settings, and local plugin symlinks.

Users must provide their own API credentials for optional model-assisted features.

## Core Idea

The workbench is designed around an evidence contract. A generated workbook cell is not only a value. It can also carry:

- the source file path,
- a page, sheet, XML node, cell range, or bounding box locator,
- a source snippet,
- an evidence score,
- the extraction or matching method,
- a cell confidence signal,
- a review state.

This makes the exported workbook suitable for engineering review. A reviewer can inspect a filled value, see why it was created, and return to the original source artifact.

## Main Capabilities

### Document Ingestion

- Scans project folders for PDF, Excel, DEXPI XML/XSD, IFC, CSV, JSON, and text sources.
- Classifies documents into process automation families such as P&ID, instrument index, terminal plan, interconnection list, circuit diagram, datasheet, and 3D/IFC model.
- Links related P&ID drawings and DEXPI XML files when matching evidence is available.

### Extraction and Normalization

- Uses deterministic rules, schema aliases, profile mappings, and engineering-specific normalizers first.
- Applies OCR and native text extraction for PDF evidence.
- Uses DEXPI XML structure for strong source evidence where available.
- Uses topology and naming heuristics for 3D/IFC candidate mappings.
- Keeps weak or ambiguous candidates in a review-required state.

### Bounded Model Assistance

Retrieval, LLM, and VLM features are optional. They are used as bounded assistants for tasks such as:

- unknown field-to-template mapping,
- OCR error disambiguation,
- ambiguous component correspondence,
- low-confidence evidence verification,
- cropped diagram interpretation.

The trusted export remains evidence-aware. Model outputs are not treated as confirmed engineering facts unless source evidence, confidence metadata, and review status make their role explicit.

### Evidence and Review

- Stores evidence references for workbook cells.
- Supports source preview and highlighting for PDF, XML, Excel, and text-like artifacts.
- Marks low-confidence or uncertain cells for review.
- Keeps missing, generated, and manually reviewed states distinguishable.

### AAS-Ready Outputs

The exported workbooks are intended as structured, reviewed, provenance-carrying engineering values for later AAS or ontology generation. The repository includes AAS and ontology export helpers, but a validated field-to-submodel mapping is still required for a project-specific AAS package.

## Installation

Python 3.10 or newer is required. A virtual environment is recommended because OCR and document processing dependencies can be heavy.

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt
```

For a lighter development setup, install the package dependencies from `pyproject.toml` first and add OCR backends only as needed.

```bash
./.venv/bin/python -m pip install -e .
./.venv/bin/python -m pip install -e ".[ocr]"
./.venv/bin/python -m pip install -e ".[ifc]"
```

Some feature-specific dependencies are intentionally optional. PaddleOCR requires a platform-compatible PaddlePaddle runtime, so install the matching `paddlepaddle` wheel for the target machine when selecting the PaddleOCR backend. AAS JSON validation can use `.[aas]`, and AASX export through AAS Manager can use `.[aasx]`.

## Running the Workbench

```bash
./.venv/bin/python main.py
```

The application stores runtime state in local ignored folders such as `.iev4pi/` and non-Excel subfolders under `Exports/`.

## API and Model Configuration

The deterministic extraction, DEXPI parsing, Excel export, and many OCR paths can be used without an external LLM API. Optional RAG, LLM, embedding, and VLM features require a user-provided OpenAI-compatible API endpoint and API key.

Recommended setup:

- Chat and reasoning: OpenAI GPT models, Mistral instruct models, Qwen instruct models, or any compatible model exposed through an OpenAI-compatible server.
- Vision-language assistance: GPT vision-capable models, Mistral vision-capable models, Qwen vision-capable models, or another OpenAI-compatible VLM.
- Embeddings: OpenAI embedding models, Qwen embedding models, or the built-in `local-hash-768` fallback for no-API development.
- OCR: Surya OCR, PaddleOCR, RapidOCR, EasyOCR, or Apple Vision on macOS.

Credentials can be supplied through the UI settings page or environment variables:

```bash
export IEVPI_LLM_API_KEY="replace-with-your-own-key"
export OPENAI_API_KEY="replace-with-your-own-key"
```

Do not commit `.env`, local settings files, API keys, passwords, private base URLs, generated caches, or non-public exported project data.

## Typical Workflow

1. Configure local input folders, OCR backend, and optional model settings.
2. Scan a project workspace and classify source documents.
3. Parse native sources and run OCR where needed.
4. Extract source-located evidence from PDF, XML, Excel, IFC, and text-like files.
5. Normalize values into standardized workbook fields.
6. Fill target workbooks and attach cell-level provenance.
7. Review low-confidence or missing values in the UI.
8. Open source preview to inspect PDF regions, XML lines, or spreadsheet cells.
9. Export reviewed workbooks, AAS candidates, or ontology artifacts.

## Repository Layout

```text
.
├── iev4pi_transformation_tool/
│   ├── core/                  # extraction, OCR, DEXPI, IFC, provenance, retrieval
│   ├── services/              # workbench orchestration, AAS and ontology services
│   ├── t1t5/                  # transformation stage rule engine
│   ├── tx/                    # transformation rule graph support
│   ├── ui/                    # PyQt6 desktop interface
│   └── vendor/                # optional OCR bridge code
├── assets/
│   ├── aas_templates/         # AAS template fragments
│   └── semantic_ids/          # semantic identifier mappings
├── data/
│   ├── templates/             # blank standardized Excel templates
│   ├── templates-AIO/         # AIO schema workbook and specification
│   └── examples/              # public example templates
├── Documents/                 # public companion source artifacts
├── Exports/Excel/             # public workbook exports and provenance bundles
├── profiles/                  # extraction profile YAML files
├── scripts/                   # audit, build, benchmark, and regeneration scripts
├── main.py                    # application entry point
├── pyproject.toml
└── requirements.txt
```

## Data and Security Notes

- Keep private source documents outside git unless they are explicitly cleared for publication.
- Keep generated filled workbooks and provenance JSON outside git unless they are intended as public companion artifacts.
- Keep runtime state, local caches, and debug logs outside git.
- Prefer environment variables for API keys.
- Treat source snippets in provenance files as potentially sensitive project data.

## Scope and Limitations

This repository is a paper companion artifact intended for reproducibility, inspection, and extension of the evidence-aware workflow described in the associated IECON 2026 manuscript.

The workflow is workbench-first by design. Reproducibility is therefore centered on the committed source code, standardized templates, public source artifact bundle, Excel export artifacts, and provenance structures rather than on local runtime databases or a packaged command-line application.

The repository does not include the manuscript source, local settings, model caches, API credentials, or full runtime state. Optional LLM, VLM, embedding, and OCR services must be configured by the user.

## Citation

If you use this repository, please cite the associated paper:

> Bowen Chen, Benedikt Schmetz, Torben Miny, Tobias Kleinert, and Birgit Vogel-Heuser,  
> *Provenance-Preserving Information Extraction from Engineering Documents for AAS-Ready Process Automation Data*,  
> manuscript prepared for IECON 2026.

See [CITATION.cff](CITATION.cff) for repository citation metadata.

## License

This repository is released under the [MIT License](LICENSE).

## Acknowledgment

This work is developed in the context of research on inconsistency detection and traceability for process automation engineering data at RWTH Aachen University.
