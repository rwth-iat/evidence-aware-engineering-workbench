from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
import re
import unicodedata

from iev4pi_transformation_tool.core.dexpi import peek_drawing_metadata
from iev4pi_transformation_tool.models import DocumentDescriptor, DocumentFamily, RiBundle, SourceDocumentKind


class DocumentClassifier:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root

    def classify(self, path: Path, relative_to: Path | None = None) -> DocumentDescriptor:
        relative_path = self._relative_display_path(path, relative_to)
        folded_name = self._fold_text(path.name)
        folded_relative = self._fold_text(relative_path)
        compact_name = self._compact_text(folded_name)
        compact_relative = self._compact_text(folded_relative)
        extension = path.suffix.lower()
        source_root = relative_path.split("/", 1)[0] if "/" in relative_path else relative_path

        if "stellenplaene" in compact_relative:
            if "stellenubersicht" in compact_name or "stellenuebersicht" in compact_name:
                source_kind = SourceDocumentKind.STELLEN_OVERVIEW
                families = [DocumentFamily.STELLEN_OVERVIEW_RECORD]
            elif self._is_datasheet_name(compact_name, compact_relative):
                source_kind = SourceDocumentKind.DEVICE_DATASHEET
                families = [DocumentFamily.STELLEN_TU_DATASHEET]
            else:
                source_kind = SourceDocumentKind.STELLEN_TU
                families = [DocumentFamily.STELLEN_TU_DATASHEET]
        elif self._is_ri_text(compact_relative):
            source_kind = SourceDocumentKind.RI_FLOWSHEET
            if extension == ".pdf":
                families = [
                    DocumentFamily.RI_EQUIPMENT_ROW,
                    DocumentFamily.RI_INSTRUMENT_FUNCTION_ROW,
                    DocumentFamily.RI_PIPING_COMPONENT_ROW,
                    DocumentFamily.RI_CONNECTION_ROW,
                ]
            else:
                families = []
        elif extension == ".ifc":
            source_kind = SourceDocumentKind.IFC_MODEL
            families = [
                DocumentFamily.IFC_PIPING_ITEM_ROW,
                DocumentFamily.IFC_CONNECTION_ROW,
            ]
        elif "verschaltungslisten" in compact_relative:
            if extension == ".pdf" and (
                "stromlauf" in compact_name
                or "sromlauf" in compact_name
                or "stromkreis" in compact_name
                or "iobaugruppe" in compact_name
                or "stromlaufplane" in compact_relative
            ):
                source_kind = SourceDocumentKind.STROMLAUFPLAN
                families = [
                    DocumentFamily.STROMLAUF_COMPONENT_GROUP,
                    DocumentFamily.STROMLAUF_COMPONENT,
                    DocumentFamily.STROMLAUF_CONNECTION,
                ]
            elif "klemmenplan" in compact_name or "klemmplan" in compact_name:
                source_kind = SourceDocumentKind.KLEMMENPLAN
                families = [DocumentFamily.KLEMMENPLAN_ROW]
            elif "verschaltungsliste" in compact_name:
                source_kind = SourceDocumentKind.VERSCHALTUNGSLISTE
                families = [DocumentFamily.VERSCHALTUNGSLISTE_ROW]
            else:
                source_kind = SourceDocumentKind.CABINET_REFERENCE
                families = [DocumentFamily.CABINET_REFERENCE_ROW]
        else:
            if self._is_datasheet_name(compact_name, compact_relative):
                source_kind = SourceDocumentKind.DEVICE_DATASHEET
                families = [DocumentFamily.STELLEN_TU_DATASHEET]
            elif extension == ".pdf":
                source_kind = SourceDocumentKind.STROMLAUFPLAN
                families = [
                    DocumentFamily.STROMLAUF_COMPONENT_GROUP,
                    DocumentFamily.STROMLAUF_COMPONENT,
                    DocumentFamily.STROMLAUF_CONNECTION,
                ]
            elif "assembly3dtemplate" in compact_name:
                source_kind = SourceDocumentKind.IFC_MODEL
                families = [
                    DocumentFamily.IFC_3D_ASSEMBLY_STEP,
                    DocumentFamily.IFC_3D_ASSEMBLY_CONNECTION,
                    DocumentFamily.IFC_3D_POSITION,
                    DocumentFamily.IFC_3D_PART_LIBRARY,
                ]
            elif extension in {".xls", ".xlsx"}:
                # Exclude Piping Diagram support workbooks (mapping, assembly filled)
                # — they are not cabinet/wiring reference documents.
                if "piping" in compact_relative and (
                    "mapping" in compact_name
                    or "assembly" in compact_name
                    or "filled" in compact_name
                ):
                    source_kind = SourceDocumentKind.IFC_MODEL
                    families = []
                else:
                    source_kind = SourceDocumentKind.CABINET_REFERENCE
                    families = [DocumentFamily.CABINET_REFERENCE_ROW]
            else:
                raise ValueError(f"Unsupported file type for classification: {path}")

        stat = path.stat()
        return DocumentDescriptor(
            path=path,
            relative_path=relative_path,
            extension=extension,
            source_kind=source_kind,
            output_families=families,
            size_bytes=stat.st_size,
            modified_at=stat.st_mtime,
            source_root=source_root,
        )

    def iter_supported_files(self, input_dirs: list[str | Path]) -> list[Path]:
        supported: list[Path] = []
        for input_dir in input_dirs:
            base_dir = Path(input_dir)
            if not base_dir.is_absolute():
                base_dir = self.workspace_root / base_dir
            if not base_dir.exists():
                continue
            for path in base_dir.rglob("*"):
                if not path.is_file():
                    continue
                suffix = path.suffix.lower()
                if suffix in {".pdf", ".xls", ".xlsx", ".ifc"}:
                    supported.append(path)
                    continue
                if suffix in {".xml", ".xsd"} and self._is_ri_support_file(path):
                    supported.append(path)
        return sorted(supported, key=self._sort_key)

    def discover_ri_bundles(self, documents: list[DocumentDescriptor]) -> list[RiBundle]:
        ri_docs = [doc for doc in documents if doc.source_kind == SourceDocumentKind.RI_FLOWSHEET]
        by_root: dict[str, list[DocumentDescriptor]] = {}
        for document in ri_docs:
            by_root.setdefault(document.source_root, []).append(document)

        bundles: list[RiBundle] = []
        for source_root, items in by_root.items():
            pdfs = [doc for doc in items if doc.extension == ".pdf"]
            xmls = [doc for doc in items if doc.extension == ".xml"]
            xsds = [doc for doc in items if doc.extension == ".xsd"]
            shared_xsd = xsds[0] if xsds else None
            xml_metadata = {
                doc.relative_path: peek_drawing_metadata(doc.path)
                for doc in xmls
            }

            if len(pdfs) == 1 and len(xmls) == 1:
                pdf_doc = pdfs[0]
                xml_doc = xmls[0]
                metadata = xml_metadata.get(xml_doc.relative_path, {})
                bundles.append(
                    self._make_ri_bundle(
                        source_root,
                        pdf_doc,
                        xml_doc,
                        shared_xsd,
                        score=1.0,
                        notes="Single PDF/XML pair in folder.",
                        metadata=metadata,
                    )
                )
                continue

            remaining_xmls = {doc.relative_path: doc for doc in xmls}
            for pdf_doc in pdfs:
                best_score = 0.0
                best_xml: DocumentDescriptor | None = None
                best_metadata: dict[str, str] = {}
                for xml_doc in remaining_xmls.values():
                    metadata = xml_metadata.get(xml_doc.relative_path, {})
                    score = self._ri_pair_score(pdf_doc, xml_doc, metadata)
                    if score > best_score:
                        best_score = score
                        best_xml = xml_doc
                        best_metadata = metadata
                if best_xml is None:
                    bundles.append(
                        self._make_ri_bundle(
                            source_root,
                            pdf_doc,
                            None,
                            shared_xsd,
                            score=0.0,
                            notes="No XML candidate found.",
                            metadata={},
                        )
                    )
                    continue
                if best_score < 0.28:
                    bundles.append(
                        self._make_ri_bundle(
                            source_root,
                            pdf_doc,
                            None,
                            shared_xsd,
                            score=best_score,
                            notes=f"Best XML candidate {best_xml.path.name} below pairing threshold.",
                            metadata=best_metadata,
                        )
                    )
                    continue
                remaining_xmls.pop(best_xml.relative_path, None)
                bundles.append(
                    self._make_ri_bundle(
                        source_root,
                        pdf_doc,
                        best_xml,
                        shared_xsd,
                        score=best_score,
                        notes=f"Matched PDF {pdf_doc.path.name} to XML {best_xml.path.name}.",
                        metadata=best_metadata,
                    )
                )
        return sorted(bundles, key=lambda item: item.bundle_id)

    def _relative_display_path(self, path: Path, relative_to: Path | None = None) -> str:
        roots: list[Path] = []
        if relative_to is not None:
            roots.append(relative_to)
        roots.append(self.workspace_root)
        for root in roots:
            try:
                return path.relative_to(root).as_posix()
            except ValueError:
                continue
        return path.as_posix()

    @staticmethod
    def _sort_key(path: Path) -> tuple[str, int, str]:
        lower_name = path.name.lower()
        match = re.search(r"(\d+)", lower_name)
        number = int(match.group(1)) if match else 0
        return (path.parent.as_posix().lower(), number, lower_name)

    @staticmethod
    def _fold_text(text: str) -> str:
        replacements = {
            "ß": "ss",
            "ẞ": "SS",
            "æ": "ae",
            "Æ": "AE",
            "œ": "oe",
            "Œ": "OE",
            "脽": "ss",
        }
        for source, target in replacements.items():
            text = text.replace(source, target)
        folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
        return folded.lower().replace("\\", "/")

    @staticmethod
    def _compact_text(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", text)

    _DATASHEET_KEYWORDS = (
        "geratedaten", "geraetedaten",
        "datenblatt", "datasheet",
        "spezifikation", "specification",
        "geratespezifikation", "geraetespezifikation",
    )

    @classmethod
    def _is_datasheet_name(cls, compact_name: str, compact_relative: str) -> bool:
        """Check whether file name or path suggests a real device datasheet."""
        combined = f"{compact_name} {compact_relative}"
        return any(kw in combined for kw in cls._DATASHEET_KEYWORDS)

    def _is_ri_text(self, compact_text: str) -> bool:
        return any(
            token in compact_text
            for token in (
                "rifliessbild",
                "rifliesbild",
                "riflieszbild",
            )
        )

    def _is_ri_support_file(self, path: Path) -> bool:
        folded_relative = self._fold_text(self._relative_display_path(path))
        return self._is_ri_text(self._compact_text(folded_relative))

    def _make_ri_bundle(
        self,
        source_root: str,
        pdf_doc: DocumentDescriptor | None,
        xml_doc: DocumentDescriptor | None,
        xsd_doc: DocumentDescriptor | None,
        *,
        score: float,
        notes: str,
        metadata: dict[str, str],
    ) -> RiBundle:
        display_name = (
            pdf_doc.path.stem
            if pdf_doc is not None
            else metadata.get("drawing_name") or metadata.get("drawing_title") or "ri_bundle"
        )
        bundle_id = self._ri_bundle_id(source_root, display_name)
        pairing_status = "paired" if pdf_doc and xml_doc and xsd_doc else "incomplete"
        if pdf_doc and xml_doc and not xsd_doc:
            pairing_status = "missing_xsd"
        elif pdf_doc and not xml_doc:
            pairing_status = "ambiguous" if score > 0 else "missing_xml"
        return RiBundle(
            bundle_id=bundle_id,
            source_root=source_root,
            pdf_path=pdf_doc.path if pdf_doc is not None else None,
            xml_path=xml_doc.path if xml_doc is not None else None,
            xsd_path=xsd_doc.path if xsd_doc is not None else None,
            display_name=display_name,
            drawing_name=metadata.get("drawing_name", ""),
            drawing_title=metadata.get("drawing_title", ""),
            pairing_score=score,
            pairing_notes=notes,
            pairing_status=pairing_status,
        )

    def _ri_pair_score(
        self,
        pdf_doc: DocumentDescriptor,
        xml_doc: DocumentDescriptor,
        metadata: dict[str, str],
    ) -> float:
        pdf_name = self._fold_text(pdf_doc.path.stem).replace("_", " ")
        xml_name = self._fold_text(xml_doc.path.stem).replace("_", " ")
        drawing_name = self._fold_text(metadata.get("drawing_name", "")).replace("_", " ")
        drawing_title = self._fold_text(metadata.get("drawing_title", "")).replace("_", " ")
        candidate_scores = [
            SequenceMatcher(None, pdf_name, xml_name).ratio(),
            SequenceMatcher(None, pdf_name, drawing_name).ratio() if drawing_name else 0.0,
            SequenceMatcher(None, pdf_name, drawing_title).ratio() if drawing_title else 0.0,
        ]
        pdf_tokens = Counter(token for token in re.split(r"[^a-z0-9]+", pdf_name) if token)
        xml_tokens = Counter(
            token
            for token in re.split(r"[^a-z0-9]+", " ".join([xml_name, drawing_name, drawing_title]))
            if token
        )
        overlap = set(pdf_tokens) & set(xml_tokens)
        overlap_score = len(overlap) / max(1, len(set(pdf_tokens)))
        return max(candidate_scores) * 0.7 + overlap_score * 0.3

    def _ri_bundle_id(self, source_root: str, display_name: str) -> str:
        folded_root = re.sub(r"[^a-z0-9]+", "_", self._fold_text(source_root)).strip("_")
        folded_name = re.sub(r"[^a-z0-9]+", "_", self._fold_text(display_name)).strip("_")
        return f"{folded_root or 'ri'}::{folded_name or 'bundle'}"
