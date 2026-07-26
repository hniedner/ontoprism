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
from dataclasses import InitVar, dataclass
from typing import TYPE_CHECKING, Any

from defusedxml.ElementTree import iterparse

from ontolib.core.exceptions import StorageError
from ontolib.core.logging_config import get_logger

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path
    from xml.etree.ElementTree import Element

    from ontolib.repositories.cadsr.archive import CadsrSource, ExtractedCadsrArchive

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
CREATE TABLE cadsr_source (
    url TEXT NOT NULL, downloaded_at TEXT NOT NULL, etag TEXT,
    last_modified TEXT, archive_size INTEGER NOT NULL,
    archive_sha256 TEXT NOT NULL, member_count INTEGER NOT NULL,
    member_names_sha256 TEXT NOT NULL, first_member_timestamp TEXT NOT NULL,
    last_member_timestamp TEXT NOT NULL,
    CHECK (archive_size > 0), CHECK (member_count > 0),
    CHECK (length(archive_sha256) = 64),
    CHECK (length(member_names_sha256) = 64)
);
"""

_REQUIRED_OBJECTS = {
    "table": {"cdes", "cde_concepts", "cdes_fts", "cadsr_source"},
    "index": {"idx_cde_context", "idx_concept_code"},
}
_EXPECTED_COLUMNS = {
    "cdes": (
        ("public_id", "TEXT", 1, 1),
        ("version", "TEXT", 1, 2),
        ("short_name", "TEXT", 1, 0),
        ("long_name", "TEXT", 1, 0),
        ("definition", "TEXT", 1, 0),
        ("context", "TEXT", 0, 0),
        ("workflow_status", "TEXT", 0, 0),
        ("registration_status", "TEXT", 0, 0),
        ("datatype", "TEXT", 0, 0),
        ("value_domain_type", "TEXT", 0, 0),
        ("search_text", "TEXT", 0, 0),
        ("cde_json", "TEXT", 1, 0),
    ),
    "cde_concepts": (
        ("concept_code", "TEXT", 1, 0),
        ("concept_name", "TEXT", 1, 0),
        ("public_id", "TEXT", 1, 0),
        ("version", "TEXT", 1, 0),
        ("concept_type", "TEXT", 0, 0),
        ("is_primary", "INTEGER", 0, 0),
    ),
    "cadsr_source": (
        ("url", "TEXT", 1, 0),
        ("downloaded_at", "TEXT", 1, 0),
        ("etag", "TEXT", 0, 0),
        ("last_modified", "TEXT", 0, 0),
        ("archive_size", "INTEGER", 1, 0),
        ("archive_sha256", "TEXT", 1, 0),
        ("member_count", "INTEGER", 1, 0),
        ("member_names_sha256", "TEXT", 1, 0),
        ("first_member_timestamp", "TEXT", 1, 0),
        ("last_member_timestamp", "TEXT", 1, 0),
    ),
}
_EXPECTED_INDEXES = {
    "idx_cde_context": ("context",),
    "idx_concept_code": ("concept_code",),
}
_EXPECTED_FTS_SQL = """CREATE VIRTUAL TABLE cdes_fts USING fts5(
    public_id UNINDEXED, version UNINDEXED, short_name, long_name, definition,
    search_text, content='cdes', content_rowid='rowid'
)"""
_JSON_TEXT_COLUMNS = (
    "public_id",
    "version",
    "short_name",
    "long_name",
    "definition",
)
_JSON_NULLABLE_TEXT_COLUMNS = (
    "context",
    "workflow_status",
    "registration_status",
    "datatype",
    "value_domain_type",
)
_CANDIDATE_SEAL = object()


@dataclass(frozen=True, slots=True)
class ValidatedCadsrCandidate:
    """A standalone caDSR database that passed all publication checks."""

    path: Path
    source: CadsrSource
    cde_count: int
    _seal: InitVar[object]

    def __post_init__(self, _seal: object) -> None:
        if _seal is not _CANDIDATE_SEAL:
            raise ValueError("candidate must be created by validate_database")
        if self.cde_count <= 0:
            raise ValueError("candidate CDE count must be positive")


def _source_values(source: CadsrSource) -> tuple[str | int | None, ...]:
    return (
        source.url,
        source.downloaded_at,
        source.etag,
        source.last_modified,
        source.archive_size,
        source.archive_sha256,
        source.member_count,
        source.member_names_sha256,
        source.first_member_timestamp,
        source.last_member_timestamp,
    )


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
    root: Element | None = None
    for event, elem in iterparse(str(xml_path), events=("start", "end")):
        document_root = elem if root is None else root
        root = document_root
        if event != "end" or elem.tag != "DataElement":
            continue
        try:
            parsed = parse_cde(elem)
            if parsed is not None:
                yield parsed
        finally:
            elem.clear()
            # Processed children remain attached unless the root is also cleared.
            document_root.clear()


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


def _remove_database_artifacts(
    db_path: Path, original: BaseException | None = None
) -> None:
    first_error: OSError | None = None
    for suffix in ("", "-journal", "-shm", "-wal"):
        try:
            db_path.with_name(db_path.name + suffix).unlink(missing_ok=True)
        except OSError as exc:
            if original is not None:
                original.add_note(f"Failed to remove caDSR candidate artifact: {exc}")
            elif first_error is None:
                first_error = exc
            else:
                first_error.add_note(
                    f"Also failed to remove caDSR candidate artifact: {exc}"
                )
    if first_error is not None:
        raise first_error


def _initialize_database(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    journal_mode = conn.execute("PRAGMA journal_mode = DELETE").fetchone()
    if journal_mode is None or str(journal_mode[0]).lower() != "delete":
        raise StorageError("caDSR candidate did not enter DELETE journal mode")
    conn.executescript(_SCHEMA)


def _load_database(
    conn: sqlite3.Connection,
    xml_paths: Sequence[Path],
    source: CadsrSource,
) -> int:
    with conn:  # single transaction
        conn.execute(
            "INSERT INTO cadsr_source "
            "(url, downloaded_at, etag, last_modified, archive_size, archive_sha256, "
            "member_count, member_names_sha256, first_member_timestamp, "
            "last_member_timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            _source_values(source),
        )
        for xml_path in xml_paths:
            member_count = 0
            for parsed in iter_cdes(xml_path):
                _insert(conn, parsed)
                member_count += 1
            if member_count == 0:
                raise StorageError(
                    f"caDSR XML member contains no usable CDEs: {xml_path.name}"
                )
    conn.execute("INSERT INTO cdes_fts(cdes_fts) VALUES ('rebuild')")
    conn.commit()
    count = int(conn.execute("SELECT COUNT(*) FROM cdes").fetchone()[0])
    if count == 0:
        raise StorageError("caDSR candidate contains no CDEs")
    return count


def build_database(
    archive: ExtractedCadsrArchive, db_path: Path
) -> ValidatedCadsrCandidate:
    """Build and validate a publication candidate from one extracted release.

    Returns a token carrying the final unique CDE count. Overwrites any existing DB.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _remove_database_artifacts(db_path)
    conn = sqlite3.connect(db_path)
    closed = False
    try:
        _initialize_database(conn)
        count = _load_database(conn, archive.xml_paths, archive.source)
        conn.close()
        closed = True
        candidate = validate_database(
            db_path,
            expected_source=archive.source,
            expected_cde_count=count,
        )
    except BaseException as original:
        if not closed:
            try:
                conn.close()
            except BaseException as close_error:
                original.add_note(f"Failed to close caDSR candidate: {close_error}")
        _remove_database_artifacts(db_path, original)
        raise
    logger.info("Built caDSR DB at %s with %d CDEs", db_path, count)
    return candidate


def _check_integrity(conn: sqlite3.Connection) -> None:
    integrity = conn.execute("PRAGMA integrity_check").fetchall()
    if integrity != [("ok",)]:
        raise StorageError(f"caDSR SQLite integrity check failed: {integrity}")
    foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_keys:
        raise StorageError(f"caDSR SQLite foreign key check failed: {foreign_keys}")


def _check_object_names(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT type, name FROM sqlite_master WHERE type IN ('table', 'index')"
    ).fetchall()
    present = {(str(row[0]), str(row[1])) for row in rows}
    missing = {
        (object_type, name)
        for object_type, names in _REQUIRED_OBJECTS.items()
        for name in names
        if (object_type, name) not in present
    }
    if missing:
        raise StorageError(
            f"caDSR candidate is missing required SQLite objects: {sorted(missing)}"
        )


def _check_column_definitions(conn: sqlite3.Connection) -> None:
    for table, expected in _EXPECTED_COLUMNS.items():
        columns = tuple(
            (str(row[1]), str(row[2]).upper(), int(row[3]), int(row[5]))
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        )
        if columns != expected:
            raise StorageError(f"caDSR candidate schema definition differs for {table}")


def _check_index_definitions(conn: sqlite3.Connection) -> None:
    for index, expected in _EXPECTED_INDEXES.items():
        columns = tuple(
            str(row[2])
            for row in conn.execute(f"PRAGMA index_info({index})").fetchall()
        )
        if columns != expected:
            raise StorageError(f"caDSR candidate index definition differs for {index}")


def _check_foreign_key_definitions(conn: sqlite3.Connection) -> None:
    foreign_keys = {
        (str(row[2]), str(row[3]), str(row[4]))
        for row in conn.execute("PRAGMA foreign_key_list(cde_concepts)").fetchall()
    }
    expected_foreign_keys = {
        ("cdes", "public_id", "public_id"),
        ("cdes", "version", "version"),
    }
    if foreign_keys != expected_foreign_keys:
        raise StorageError("caDSR candidate foreign key definition differs")


def _check_fts_definition(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'cdes_fts'"
    ).fetchone()
    actual = re.sub(r"\s+", "", str(row[0])) if row is not None else ""
    expected = re.sub(r"\s+", "", _EXPECTED_FTS_SQL)
    if actual != expected:
        raise StorageError("caDSR candidate FTS5 definition differs")


def _check_required_objects(conn: sqlite3.Connection) -> None:
    _check_object_names(conn)
    _check_column_definitions(conn)
    _check_index_definitions(conn)
    _check_foreign_key_definitions(conn)
    _check_fts_definition(conn)


def _check_row_content(conn: sqlite3.Connection) -> None:
    text_conditions = [
        f"json_type(cde_json, '$.{column}') IS NOT 'text' "
        f"OR json_extract(cde_json, '$.{column}') IS NOT {column}"
        for column in _JSON_TEXT_COLUMNS
    ]
    nullable_conditions = [
        f"COALESCE(json_type(cde_json, '$.{column}'), 'missing') "
        f"NOT IN ('text', 'null') "
        f"OR json_extract(cde_json, '$.{column}') IS NOT {column}"
        for column in _JSON_NULLABLE_TEXT_COLUMNS
    ]
    json_conditions = " OR ".join(
        [
            "json_type(cde_json) IS NOT 'object'",
            *text_conditions,
            *nullable_conditions,
            "json_type(cde_json, '$.permissible_values') IS NOT 'array'",
        ]
    )
    invalid_cdes = int(
        conn.execute(
            "SELECT COUNT(*) FROM cdes WHERE trim(public_id) = '' "  # noqa: S608
            "OR trim(version) = '' OR CASE WHEN json_valid(cde_json) THEN "
            f"{json_conditions} ELSE 1 END"
        ).fetchone()[0]
    )
    invalid_permissible_values = int(
        conn.execute(
            "SELECT COUNT(*) FROM cdes, json_each("
            "CASE WHEN json_valid(cde_json) THEN cde_json "
            "ELSE '{\"permissible_values\":[]}' END, '$.permissible_values') AS pv "
            "WHERE json_type(pv.value) IS NOT 'object' "
            "OR json_type(pv.value, '$.value') IS NOT 'text' "
            "OR COALESCE(json_type(pv.value, '$.meaning'), 'missing') "
            "NOT IN ('text', 'null') "
            "OR COALESCE(json_type(pv.value, '$.meaning_code'), 'missing') "
            "NOT IN ('text', 'null')"
        ).fetchone()[0]
    )
    invalid_concepts = int(
        conn.execute(
            "SELECT COUNT(*) FROM cde_concepts WHERE trim(concept_code) = '' "
            "OR trim(concept_name) = '' OR trim(public_id) = '' OR trim(version) = '' "
            "OR concept_code GLOB '*[^A-Za-z0-9:_.-]*' "
            "OR concept_type IS NULL OR trim(concept_type) = '' "
            "OR is_primary IS NULL OR is_primary NOT IN (0, 1)"
        ).fetchone()[0]
    )
    if invalid_cdes or invalid_permissible_values or invalid_concepts:
        raise StorageError(
            "caDSR candidate has invalid CDE row content: "
            f"cdes={invalid_cdes}, permissible_values={invalid_permissible_values}, "
            f"concepts={invalid_concepts}"
        )


def _check_fts_content(conn: sqlite3.Connection) -> None:
    try:
        conn.execute(
            "SELECT COUNT(*) FROM cdes_fts WHERE cdes_fts MATCH ?",
            ("__ontoprism_validation_no_match__",),
        ).fetchone()
        conn.execute(
            "INSERT INTO cdes_fts(cdes_fts, rank) VALUES ('integrity-check', 1)"
        )
        conn.rollback()
    except sqlite3.DatabaseError as exc:
        raise StorageError(f"caDSR candidate FTS5 content differs: {exc}") from exc


def _check_identity(
    conn: sqlite3.Connection,
    expected_source: CadsrSource,
    expected_cde_count: int,
) -> None:
    journal_mode = conn.execute("PRAGMA journal_mode").fetchone()
    if journal_mode is None or str(journal_mode[0]).lower() != "delete":
        raise StorageError("caDSR candidate is not in DELETE journal mode")
    source_rows = conn.execute(
        "SELECT url, downloaded_at, etag, last_modified, archive_size, "
        "archive_sha256, member_count, member_names_sha256, "
        "first_member_timestamp, last_member_timestamp FROM cadsr_source"
    ).fetchall()
    if source_rows != [_source_values(expected_source)]:
        raise StorageError("caDSR candidate source provenance does not match")
    actual_count = int(conn.execute("SELECT COUNT(*) FROM cdes").fetchone()[0])
    if actual_count != expected_cde_count:
        raise StorageError(
            "caDSR candidate CDE count does not match the build: "
            f"{actual_count} != {expected_cde_count}"
        )
    if actual_count == 0:
        raise StorageError("caDSR candidate contains no CDEs")


def validate_database(
    db_path: Path,
    *,
    expected_source: CadsrSource,
    expected_cde_count: int,
) -> ValidatedCadsrCandidate:
    """Reject a caDSR candidate that is incomplete or unsafe to publish."""
    try:
        conn = sqlite3.connect(db_path)
        try:
            _check_integrity(conn)
            _check_required_objects(conn)
            _check_identity(conn, expected_source, expected_cde_count)
            _check_row_content(conn)
            _check_fts_content(conn)
        except BaseException as original:
            try:
                conn.close()
            except BaseException as close_error:
                original.add_note(
                    f"Failed to close caDSR candidate after validation: {close_error}"
                )
            raise
        else:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise StorageError(f"invalid caDSR candidate database: {exc}") from exc
    return ValidatedCadsrCandidate(
        db_path, expected_source, expected_cde_count, _CANDIDATE_SEAL
    )
