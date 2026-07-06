"""Build the caDSR CDE SQLite database from the released CDE XML.

Streams the caDSR ``releasedCDEsXML`` dump (one ``<DataElement>`` at a time) into the
``cdes`` / ``cde_concepts`` tables + the ``cdes_fts`` FTS5 index that the read model
(:mod:`ontolib.repositories.cadsr.repository`) and search (#10) consume. This drops the
fairdata-copy dependency (issue #7).

Ported from fairdata's parser; the emitted ``cde_json`` is the flat shape ontoprism's
read model expects (top-level ``permissible_values`` + summary fields), not fairdata's
nested ``CDEModel``. Uses defusedxml (the dump is external input) with streaming
``iterparse`` so an 80k-CDE, multi-hundred-MB file never loads whole into memory.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import TYPE_CHECKING, Any

from defusedxml.ElementTree import iterparse

from ontolib.core.logging_config import get_logger

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path
    from xml.etree.ElementTree import Element

logger = get_logger(__name__)

# A concept code safe to use as an NCIt IRI segment (mirrors the graph store's guard).
_SAFE_CODE = re.compile(r"^[A-Za-z0-9:_.\-]+$")
# Permissible-value meanings must be strict NCIt codes (C\d+ / CL\d+).
_NCIT_CODE = re.compile(r"CL?\d+")
_SEARCH_SEP = " | "
_PV_CAP = 10  # permissible values folded into search_text
_CONCEPT_CAP = 20  # concept names folded into search_text

_SCHEMA = """
CREATE TABLE cdes (
    public_id TEXT NOT NULL, version TEXT NOT NULL, short_name TEXT NOT NULL,
    long_name TEXT NOT NULL, definition TEXT NOT NULL, context TEXT,
    workflow_status TEXT, registration_status TEXT, datatype TEXT,
    value_domain_type TEXT, search_text TEXT, cde_json TEXT NOT NULL,
    PRIMARY KEY (public_id, version)
);
CREATE TABLE cde_concepts (
    concept_code TEXT NOT NULL, concept_name TEXT NOT NULL, public_id TEXT NOT NULL,
    version TEXT NOT NULL, concept_type TEXT, is_primary INTEGER,
    FOREIGN KEY (public_id, version) REFERENCES cdes(public_id, version)
);
CREATE INDEX idx_cde_context ON cdes(context);
CREATE INDEX idx_concept_code ON cde_concepts(concept_code);
CREATE VIRTUAL TABLE cdes_fts USING fts5(
    public_id UNINDEXED, version UNINDEXED, short_name, long_name, definition,
    search_text, content='cdes', content_rowid='rowid'
);
"""


def _text(elem: Element | None, tag: str) -> str | None:
    if elem is None:
        return None
    child = elem.find(tag)
    if child is None or child.text is None:
        return None
    return child.text.strip() or None


def _bare_code(raw: str) -> str:
    """Recover the concept code from a possibly ``::``-qualified token."""
    return raw.split("::", 1)[0].strip()


def _split_csv(raw: str | None) -> list[str]:
    return [tok.strip() for tok in raw.split(",") if tok.strip()] if raw else []


class _Concept:
    __slots__ = ("code", "concept_type", "is_primary", "name")

    def __init__(self, code: str, name: str, concept_type: str, *, is_primary: bool):
        self.code = code
        self.name = name
        self.concept_type = concept_type
        self.is_primary = is_primary


def _structured_concepts(entity: Element | None, source_type: str) -> list[_Concept]:
    """Parse ``ConceptDetails_ITEM`` NCIt concepts under an ObjectClass/Property/etc."""
    if entity is None:
        return []
    details = entity.find("ConceptDetails")
    if details is None:
        return []
    out: list[_Concept] = []
    for item in details.findall("ConceptDetails_ITEM"):
        raw = _text(item, "PREFERRED_NAME")
        if not raw or not _SAFE_CODE.match(_bare_code(raw)):
            continue
        out.append(
            _Concept(
                _bare_code(raw),
                _text(item, "LONG_NAME") or _bare_code(raw),
                source_type,
                is_primary=_text(item, "PRIMARY_FLAG_IND") == "Yes",
            )
        )
    return out


def _pv_concepts(pv: Element) -> list[_Concept]:
    """Parse the parallel MEANINGCONCEPTS CSV arrays into value-meaning concepts."""
    codes = _split_csv(_text(pv, "MEANINGCONCEPTS"))
    if not codes:
        return []
    orders = _split_csv(_text(pv, "MEANINGCONCEPTDISPLAYORDER"))
    meaning = _text(pv, "VALUEMEANING")
    out: list[_Concept] = []
    for i, raw in enumerate(codes):
        code = _bare_code(raw)
        if not _NCIT_CODE.fullmatch(code):
            continue
        order = orders[i] if i < len(orders) else ""
        out.append(
            _Concept(code, meaning or code, "value_meaning", is_primary=order == "0")
        )
    return out


def _pv_dict(pv: Element, pv_concepts: list[_Concept]) -> dict[str, Any]:
    """One permissible-value dict for cde_json (meaning_code = primary NCIt code)."""
    primary = next((c for c in pv_concepts if c.is_primary), None) or (
        pv_concepts[0] if pv_concepts else None
    )
    return {
        "value": _text(pv, "VALIDVALUE") or "",
        "meaning": _text(pv, "VALUEMEANING"),
        "meaning_code": primary.code if primary else None,
    }


def _permissible_values(
    vd: Element | None,
) -> tuple[list[dict[str, Any]], list[_Concept]]:
    """Return (permissible-value dicts for cde_json, their value-meaning concepts)."""
    container = vd.find("PermissibleValues") if vd is not None else None
    if container is None:
        return [], []
    pvs: list[dict[str, Any]] = []
    concepts: list[_Concept] = []
    for pv in container.findall("PermissibleValues_ITEM"):
        pv_concepts = _pv_concepts(pv)
        concepts.extend(pv_concepts)
        pvs.append(_pv_dict(pv, pv_concepts))
    return pvs, concepts


def _dedupe(concepts: list[_Concept]) -> list[_Concept]:
    """First occurrence per code wins (object_class, property, representation, PV)."""
    seen: set[str] = set()
    out: list[_Concept] = []
    for c in concepts:
        if c.code not in seen:
            seen.add(c.code)
            out.append(c)
    return out


def _entity_text(elem: Element | None) -> list[str]:
    """LongName + PreferredDefinition of a DEC/VD element (empty when absent)."""
    if elem is None:
        return []
    return [_text(elem, "LongName") or "", _text(elem, "PreferredDefinition") or ""]


def _pv_search_parts(vd: Element | None) -> list[str]:
    """value + meaning of the first PVs, for search_text."""
    if vd is None:
        return []
    container = vd.find("PermissibleValues")
    items = container.findall("PermissibleValues_ITEM") if container is not None else []
    parts: list[str] = []
    for pv in items[:_PV_CAP]:
        parts += [_text(pv, "VALIDVALUE") or "", _text(pv, "VALUEMEANING") or ""]
    return parts


def _build_search_text(
    dec: Element | None, vd: Element | None, concepts: list[_Concept]
) -> str:
    """Concatenate DEC/VD text + first PVs + concept names (short/long/def excluded)."""
    # value_meaning concepts are excluded (they'd dilute MAP semantics).
    named = [c.name for c in concepts if c.concept_type != "value_meaning"]
    parts = (
        _entity_text(dec)
        + _entity_text(vd)
        + _pv_search_parts(vd)
        + named[:_CONCEPT_CAP]
    )
    return _SEARCH_SEP.join(p for p in parts if p)


class ParsedCde:
    """A parsed CDE ready to insert: the flat cde_json + concepts + search_text."""

    __slots__ = ("cde_json", "concepts", "search_text")

    def __init__(
        self, cde_json: dict[str, Any], concepts: list[_Concept], search_text: str
    ):
        self.cde_json = cde_json
        self.concepts = concepts
        self.search_text = search_text


def _collect_concepts(
    dec: Element | None, vd: Element | None, pv_concepts: list[_Concept]
) -> list[_Concept]:
    """Deduplicated concepts: object_class, property, representation, then PV codes."""
    oc = dec.find("ObjectClass") if dec is not None else None
    prop = dec.find("Property") if dec is not None else None
    rep = vd.find("Representation") if vd is not None else None
    return _dedupe(
        _structured_concepts(oc, "object_class")
        + _structured_concepts(prop, "property")
        + _structured_concepts(rep, "representation")
        + pv_concepts
    )


def _cde_json(
    elem: Element,
    public_id: str,
    version: str,
    vd: Element | None,
    pvs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble the flat cde_json the read model expects."""
    return {
        "public_id": public_id,
        "version": version,
        "short_name": _text(elem, "PREFERREDNAME") or "",
        "long_name": _text(elem, "LONGNAME") or "",
        "definition": _text(elem, "PREFERREDDEFINITION") or "",
        "context": _text(elem, "CONTEXTNAME"),
        "workflow_status": _text(elem, "WORKFLOWSTATUS"),
        "registration_status": _text(elem, "REGISTRATIONSTATUS"),
        "datatype": _text(vd, "Datatype"),
        "value_domain_type": _text(vd, "ValueDomainType"),
        "permissible_values": pvs,
    }


def parse_cde(elem: Element) -> ParsedCde | None:
    """Parse one ``<DataElement>`` into an insertable CDE, or None if it has no id."""
    public_id = _text(elem, "PUBLICID")
    version = _text(elem, "VERSION")
    if not public_id or not version:
        return None
    dec = elem.find("DATAELEMENTCONCEPT")
    vd = elem.find("VALUEDOMAIN")
    pvs, pv_concepts = _permissible_values(vd)
    concepts = _collect_concepts(dec, vd, pv_concepts)
    cde_json = _cde_json(elem, public_id, version, vd, pvs)
    return ParsedCde(cde_json, concepts, _build_search_text(dec, vd, concepts))


def iter_cdes(xml_path: Path) -> Iterator[ParsedCde]:
    """Stream-parse ``<DataElement>`` records from *xml_path* (memory-bounded)."""
    it = iterparse(str(xml_path), events=("end",))
    for _event, elem in it:
        if elem.tag != "DataElement":
            continue
        try:
            parsed = parse_cde(elem)
            if parsed is not None:
                yield parsed
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            logger.warning("Skipping unparseable CDE: %s", exc)
        finally:
            elem.clear()
            # clear() empties the element but leaves it attached to the root, which
            # would otherwise accumulate one node per record — drop processed siblings.
            root = getattr(it, "root", None)
            if root is not None:
                root.clear()


def _insert(conn: sqlite3.Connection, parsed: ParsedCde) -> None:
    j = parsed.cde_json
    conn.execute(
        "INSERT OR REPLACE INTO cdes (public_id, version, short_name, long_name, "
        "definition, context, workflow_status, registration_status, datatype, "
        "value_domain_type, search_text, cde_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            j["public_id"],
            j["version"],
            j["short_name"],
            j["long_name"],
            j["definition"],
            j["context"],
            j["workflow_status"],
            j["registration_status"],
            j["datatype"],
            j["value_domain_type"],
            parsed.search_text,
            json.dumps(j),
        ),
    )
    conn.execute(
        "DELETE FROM cde_concepts WHERE public_id = ? AND version = ?",
        (j["public_id"], j["version"]),
    )
    conn.executemany(
        "INSERT INTO cde_concepts (concept_code, concept_name, public_id, version, "
        "concept_type, is_primary) VALUES (?,?,?,?,?,?)",
        [
            (
                c.code,
                c.name,
                j["public_id"],
                j["version"],
                c.concept_type,
                int(c.is_primary),
            )
            for c in parsed.concepts
        ],
    )


def build_database(xml_paths: list[Path], db_path: Path) -> int:
    """Build the caDSR SQLite DB at *db_path* from the CDE XML file(s).

    Returns the number of CDEs written. Overwrites any existing DB.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_SCHEMA)
        count = 0
        with conn:  # single transaction
            for xml_path in xml_paths:
                for parsed in iter_cdes(xml_path):
                    _insert(conn, parsed)
                    count += 1
        # Sync the external-content FTS index from the populated cdes table.
        conn.execute("INSERT INTO cdes_fts(cdes_fts) VALUES ('rebuild')")
        conn.commit()
    finally:
        conn.close()
    logger.info("Built caDSR DB at %s with %d CDEs", db_path, count)
    return count
