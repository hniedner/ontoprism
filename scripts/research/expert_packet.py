"""Static G1 review packet and blank verdict form over #472 observations."""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
from collections.abc import Mapping, Sequence
from html import escape
from pathlib import Path

from scripts.research.expert_assembly import (
    ExpertConcept,
    assemble_run,
    sample_from_rehearsal,
)

from ontolib.decomposition.axis_contracts import AXIS_CONTRACTS

_CDE_VISIBLE = 10
_CDE_LOOKUP_LIMIT = 1_000


def _text(value: object) -> str:
    return escape(str(value), quote=True)


def _items(rows: Sequence[str]) -> str:
    return (
        f"<ul>{''.join(f'<li>{row}</li>' for row in rows)}</ul>"
        if rows
        else "<p>none found</p>"
    )


def _cde_items(rows: Sequence[tuple[str, str, str]]) -> str:
    visible = _items(
        [
            f"{_text(public_id)} v{_text(version)} — {_text(name)}"
            for public_id, version, name in rows[:_CDE_VISIBLE]
        ]
    )
    if len(rows) >= _CDE_LOOKUP_LIMIT:
        return f"{visible}<p>at least 1,000 (lookup limit)</p>"
    if len(rows) > _CDE_VISIBLE:
        return f"{visible}<p>{len(rows) - _CDE_VISIBLE} more</p>"
    return visible


def render_packet(
    concepts: Sequence[ExpertConcept],
    *,
    seed: int,
    frame_run: str,
    axis_definitions: Mapping[str, str],
) -> tuple[str, str]:
    """Return a standalone HTML view and its blank expert verdict CSV."""
    form = io.StringIO()
    writer = csv.writer(form)
    writer.writerow(
        ("concept_code", "cohort", "question", "axis", "filler", "verdict", "comment")
    )
    sections = []
    axes: set[str] = set()
    for concept in concepts:
        if not concept.label or not concept.outcome or not concept.outcome_reason:
            raise ValueError("each outcome needs a label and reason")
        if any(not kind or not reason for kind, reason in concept.flags):
            raise ValueError("each flag needs a kind and reason")
        code, cohort = concept.code, concept.cohort
        parents = _items(
            [f"{_text(c)} — {_text(label)}" for c, label in concept.parents]
        )
        roles = _items(
            [
                f"{_text(role)} {_text(label)} some {_text(target)} — {_text(name)}"
                for role, label, target, name in concept.stated_roles
            ]
        )
        constituents = []
        for axis, filler, label, flagged, reason in concept.constituents:
            axes.add(axis)
            definition = axis_definitions.get(
                axis, "NCIt source role; assess in context"
            )
            cdes = _cde_items(concept.cdes[filler])
            constituents.append(
                f"{_text(axis)} — {_text(filler)} {_text(label)}: "
                f"{_text(definition)}; "
                f"flag: {_text(reason) if flagged else 'none'}; "
                f"caDSR CDEs for constituent: {cdes}"
            )
            writer.writerow((code, cohort, "constituent", axis, filler, "", ""))
        anchors = _items(
            [
                f"{_text(c)}{' (self)' if self_ref else ''} — {_text(label)}"
                for c, label, self_ref in concept.anchors
            ]
        )
        genus = _items([f"{_text(c)} — {_text(label)}" for c, label in concept.genus])
        flags = _items(
            [f"{_text(kind)} — {_text(reason)}" for kind, reason in concept.flags]
        )
        concept_cdes = _cde_items(concept.cdes[code])
        writer.writerow(
            (code, cohort, "morphology-choice", "op:Morphology", "", "", "")
        )
        writer.writerow((code, cohort, "completeness", "", "", "", ""))
        sections.append(
            f'<section id="{_text(code)}"><h2>{_text(code)} — {_text(concept.label)} '
            f'({_text(cohort)})</h2><div class="columns">'
            f"<article><h3>Official stated NCIt</h3>"
            f"<h4>Stated named parents</h4>{parents}"
            f"<h4>Stated existential role restrictions</h4>{roles}"
            f"<h4>caDSR CDEs for concept</h4>{concept_cdes}</article>"
            f"<article><h3>Engine decomposition</h3><p>Outcome: "
            f"{_text(concept.outcome)} — {_text(concept.outcome_reason)}</p>"
            f"{_items(constituents)}<h4>Review flags and generic reasons</h4>{flags}"
            f"<h4>Morphology alternatives</h4><p>Current genus parents</p>{genus}"
            f"<p>Most-specific told NCIt P334 carriers: {concept.anchor_count} "
            f"anchor{'s' if concept.anchor_count != 1 else ''}</p>{anchors}</article>"
            "</div></section>"
        )
    for axis in sorted(axes):
        writer.writerow(("", "", "axis-cardinality", axis, "", "", ""))
    writer.writerow(("", "", "cadsr-usefulness", "", "", "", ""))
    definitions = _items(
        [
            f"{_text(axis)}: {
                _text(axis_definitions.get(axis, 'NCIt source role; assess in context'))
            }"
            for axis in sorted(axes)
        ]
    )
    html = (
        "<!doctype html><html lang='en'><meta charset='utf-8'>"
        "<title>NCIt expert review packet</title><style>"
        "body{font:1rem system-ui;max-width:90rem;margin:2rem auto;padding:1rem}"
        ".columns{display:grid;grid-template-columns:1fr 1fr;gap:2rem}"
        "section{border-top:2px solid #444;padding:1rem 0}li{margin:.4rem 0}"
        "@media print{section{break-before:page}}"
        "</style><h1>NCIt neoplasm expert review</h1>"
        f"<p>Source: complete file-only seeded frame run {_text(frame_run)}; "
        f"simple-random seed {seed}; 20 canonical oracle plus 30 decomposed non-oracle "
        "concepts drawn from the seeded 1,000. Candidates are not equivalences.</p>"
        "<p>Per constituent: correct / wrong / unsure. Per axis: may it hold several "
        "values? yes / no / unsure. Per concept: genus-as-morphology / histology "
        "anchor / neither / unsure. Does the decomposition miss anything this "
        "concept means? none / yes (free text) / unsure. Are the caDSR links shown "
        "useful for your work? yes / no / unsure + comment.</p>"
        "<p>Pre-registered G1 decision: GO at 80% correct among decided random-cohort "
        "constituents, with no axis below 60% among axes with at least 10 decided "
        "random-cohort judgments, and 80% owner-expert agreement on decided oracle "
        "pairs. RETHINK below 60% random acceptance or if the axis model is rejected; "
        "otherwise GO WITH CORRECTIONS. Report unsure and small axes separately. "
        "No expert verdict has been recorded in this packet.</p>"
        f"<h2>Plain-language axis definitions</h2>{definitions}"
        + "".join(sections)
        + "</html>"
    )
    return html, form.getvalue()


async def _run(args: argparse.Namespace) -> None:
    sample = await sample_from_rehearsal(
        args.frame_run, args.frame_manifest, args.oracle, seed=args.seed
    )
    records = await assemble_run(args.frame_run, sample)
    definitions = {
        axis: contract.definition for axis, contract in AXIS_CONTRACTS.items()
    }
    definitions["op:Morphology"] = "Type of neoplasm suggested by its stated genus."
    html, verdict = render_packet(
        records, seed=args.seed, frame_run=args.frame_run, axis_definitions=definitions
    )
    if args.html.resolve() == args.csv.resolve():
        raise ValueError("HTML and verdict CSV must have different paths")
    if args.html.exists():
        raise ValueError(f"refusing to replace completed packet artifact: {args.html}")
    if args.csv.exists() and args.csv.read_bytes() != verdict.encode():
        raise ValueError("existing verdict CSV differs from the rendered packet")
    args.html.write_text(html)
    if not args.csv.exists():
        args.csv.write_text(verdict)
    print(f"packet: {len(records)} concepts; seed {args.seed}; {args.html}; {args.csv}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frame-run", required=True)
    parser.add_argument("--frame-manifest", type=Path, required=True)
    parser.add_argument(
        "--oracle",
        type=Path,
        default=Path("ontolib/tests/decomposition/golden/neoplasm-adjudicated.json"),
    )
    parser.add_argument("--seed", type=int, default=472)
    parser.add_argument("--html", type=Path, required=True)
    parser.add_argument("--csv", type=Path, required=True)
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
