#!/usr/bin/env python3
"""Index the two design docs into structured planning data.

Extracts the artifacts a backlog is planned from, deterministically, so no
agent has to re-read 6,400 lines of Markdown to answer "what is blocked?":

  decisions  DesignDoc B.3 decision register (DR-01..DR-n)
  opens      DbtBestPractices Appendix B open decisions (B.1..B.n)
  reviews    every `[REVIEW] RC<n>` / `Fixed (F<n>)` note, with its section
  stages     DesignDoc section 5 stage headings and A.5 corrected-list rows
  standards  DbtBestPractices section 3 enforcement matrix (3.1..3.n)

Usage:
    doc_index.py                      # everything, as JSON
    doc_index.py --section decisions --format text
    doc_index.py --blocking           # only rows that block work, as text
    doc_index.py --stage 3            # everything touching stage 3
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[4]
DESIGN_DOC = REPO / "docs" / "DesignDoc.md"
BEST_PRACTICES = REPO / "docs" / "DbtBestPractices.md"

BLOCKING_STATUSES = {"CONFLICT", "MISSING"}
HEADING_RE = re.compile(r"^(#{1,4})\s+(.*?)\s*$")
REVIEW_RE = re.compile(r"^>\s*\*\*\[REVIEW\s+(\d{4}-\d{2}-\d{2})\]\s*(.+?)\*\*\s*(.*)$")
STAGE_HEADING_RE = re.compile(r"^###\s+Stage\s+([0-9]+[a-z]?)\s*(?:—|-)\s*(.+?)\s*$")
A5_ROW_RE = re.compile(r"^\|\s*\*\*([0-9]+[a-z]?)\*\*\s*(?:—|-)\s*(.+?)\s*\|(.*)\|\s*$")
STANDARD_RE = re.compile(r"^\|\s*(3\.\d+)\s*\|(.*)$")
OPEN_DECISION_RE = re.compile(r"^\*\*(B\.\d+)\s*(?:—|-)\s*(.+?)\.?\*\*\s*(.*)$")


def _cells(line: str) -> list[str]:
    """Split a Markdown table row into stripped cells."""
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _plain(text: str) -> str:
    """Strip Markdown emphasis, strikethrough and backticks."""
    out = re.sub(r"(\*\*|~~|`)", "", text)
    return re.sub(r"\s+", " ", out).strip()


def _slice(lines: list[str], start_pat: str, end_pat: str) -> tuple[int, int]:
    """Return the [start, end) line indices of a section by heading regex."""
    start = end = -1
    for i, line in enumerate(lines):
        if start < 0 and re.match(start_pat, line):
            start = i
        elif start >= 0 and re.match(end_pat, line):
            end = i
            break
    if start < 0:
        return (0, 0)
    return (start, end if end > 0 else len(lines))


def _stages_named(text: str) -> list[str]:
    """Every stage id mentioned in a blob of prose ("Stage 0.3", "Stages 1/5")."""
    found: set[str] = set()
    for match in re.finditer(r"Stages?\s+((?:[0-9]+[a-z]?(?:\.[0-9]+)?)(?:\s*(?:,|/|and|–|-)\s*[0-9]+[a-z]?(?:\.[0-9]+)?)*)", text):
        for part in re.split(r"\s*(?:,|/|and|–|-)\s*", match.group(1)):
            part = part.strip()
            if part:
                found.add(part)
    return sorted(found, key=lambda s: [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", s) if p])


def parse_decisions(lines: list[str]) -> list[dict[str, Any]]:
    """DesignDoc B.3 decision register."""
    rows: list[dict[str, Any]] = []
    for line in lines:
        if not line.startswith("| DR-"):
            continue
        cells = _cells(line)
        if len(cells) < 5:
            continue
        status_raw = cells[2]
        status = _plain(status_raw)
        head = re.split(r"[\s(—-]", status, maxsplit=1)[0].upper()
        context = f"{status} {cells[4]}"
        rows.append(
            {
                "id": cells[0],
                "decision": _plain(cells[1]),
                "status": head,
                "status_detail": status,
                "value_in_force": _plain(cells[3]) or None,
                "notes": _plain(cells[4]),
                "blocking": head in BLOCKING_STATUSES or "blocks" in status.lower(),
                "blocks_stages": _stages_named(context),
                "source": "DesignDoc.md B.3",
            }
        )
    return rows


def parse_open_decisions(lines: list[str]) -> list[dict[str, Any]]:
    """DbtBestPractices Appendix B open decisions."""
    start, end = _slice(lines, r"^## Appendix B\b", r"^## Appendix C\b")
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    body: list[str] = []

    def flush() -> None:
        if current is None:
            return
        text = " ".join(body)
        rec = re.search(r"\*\*Recommendation:?\s*(.+?)\*\*", text)
        current["recommendation"] = _plain(rec.group(1)) if rec else None
        current["resolved"] = bool(re.search(r"\*\*Resolved", text))
        current["blocks_stages"] = _stages_named(text)
        current["blocking"] = not current["resolved"]
        current["body"] = _plain(text)[:600]
        rows.append(current)

    for line in lines[start:end]:
        match = OPEN_DECISION_RE.match(line)
        if match:
            flush()
            current = {
                "id": match.group(1),
                "title": _plain(match.group(2)),
                "source": "DbtBestPractices.md Appendix B",
            }
            body = [match.group(3)]
        elif current is not None and not line.startswith(">"):
            body.append(line)
    flush()
    return rows


def parse_reviews(lines: list[str], doc: str) -> list[dict[str, Any]]:
    """Inline [REVIEW] blockquote callouts, tagged with their section."""
    rows: list[dict[str, Any]] = []
    section = ""
    pending: dict[str, Any] | None = None
    body: list[str] = []

    def flush() -> None:
        if pending is not None:
            pending["body"] = _plain(" ".join(body))[:900]
            rows.append(pending)

    for i, line in enumerate(lines):
        heading = HEADING_RE.match(line)
        if heading:
            section = heading.group(2)
        match = REVIEW_RE.match(line)
        if match:
            flush()
            label = _plain(match.group(2))
            ident = label.split("—")[0].split(":")[0].strip()
            pending, body = (
                {
                    "id": ident,
                    "kind": "fixed" if ident.lower().startswith("fixed") else "open",
                    "date": match.group(1),
                    "title": label,
                    "section": section,
                    "doc": doc,
                    "line": i + 1,
                },
                [match.group(3)],
            )
        elif pending is not None:
            if line.startswith(">"):
                body.append(line.lstrip("> "))
            else:
                flush()
                pending, body = None, []
    flush()
    return rows


def parse_stages(lines: list[str]) -> list[dict[str, Any]]:
    """Section 5 stage headings merged with A.5 corrected-list rows."""
    body_start, body_end = _slice(lines, r"^## 5\. Staged plan", r"^## 6\. Verification")
    merged: dict[str, dict[str, Any]] = {}

    for i, line in enumerate(lines[body_start:body_end], start=body_start):
        match = STAGE_HEADING_RE.match(line)
        if match:
            merged[match.group(1)] = {
                "stage": match.group(1),
                "title": _plain(match.group(2)),
                "in_body_section_5": True,
                "body_line": i + 1,
                "a5_change": None,
                "a5_why": None,
            }

    a5_start, a5_end = _slice(lines, r"^## A\.5 Corrected stage list", r"^## A\.6\b")
    for line in lines[a5_start:a5_end]:
        match = A5_ROW_RE.match(line)
        if not match:
            continue
        stage, title = match.group(1), _plain(match.group(2))
        rest = _cells("|" + match.group(3) + "|")
        change = _plain(rest[0]) if rest else ""
        why = _plain(rest[1]) if len(rest) > 1 else ""
        entry = merged.setdefault(
            stage,
            {"stage": stage, "title": title, "in_body_section_5": False, "body_line": None},
        )
        entry["a5_change"] = change
        entry["a5_why"] = why or None
        entry["a5_verb"] = change.split(".")[0] if change else None

    ordered = sorted(
        merged.values(),
        key=lambda s: (int(re.match(r"\d+", s["stage"]).group()), s["stage"]),  # type: ignore[union-attr]
    )
    for entry in ordered:
        entry["reconciliation"] = (
            "body only — A.5 has no row"
            if entry["a5_change"] is None
            else "A.5 only — MISSING from normative section 5 (DR-11 / R3)"
            if not entry["in_body_section_5"]
            else "both — section 5 text may not reflect A.5's change (DR-11 / R3)"
        )
    return ordered


def parse_standards(lines: list[str]) -> list[dict[str, Any]]:
    """Section 3 enforcement matrix."""
    start, end = _slice(lines, r"^## 3\. The enforcement matrix", r"^## 4\. Tool stack")
    rows: list[dict[str, Any]] = []
    for line in lines[start:end]:
        match = STANDARD_RE.match(line)
        if not match:
            continue
        cells = _cells(line)
        if len(cells) < 5:
            continue
        on_violation = _plain(cells[4])
        rows.append(
            {
                "id": cells[0],
                "standard": _plain(cells[1]),
                "mechanism": _plain(cells[2]),
                "gate": _plain(cells[3]),
                "on_violation": on_violation,
                "enforced": "unenforced" not in on_violation.lower(),
            }
        )
    return rows


def build_index() -> dict[str, Any]:
    for path in (DESIGN_DOC, BEST_PRACTICES):
        if not path.exists():
            sys.exit(f"missing doc: {path}")
    design = DESIGN_DOC.read_text(encoding="utf-8").splitlines()
    practices = BEST_PRACTICES.read_text(encoding="utf-8").splitlines()

    decisions = parse_decisions(design)
    opens = parse_open_decisions(practices)
    reviews = parse_reviews(design, "DesignDoc.md") + parse_reviews(practices, "DbtBestPractices.md")
    stages = parse_stages(design)
    standards = parse_standards(practices)

    return {
        "decisions": decisions,
        "opens": opens,
        "reviews": reviews,
        "stages": stages,
        "standards": standards,
        "summary": {
            "decisions_total": len(decisions),
            "decisions_blocking": sum(1 for d in decisions if d["blocking"]),
            "decisions_open": sum(1 for d in decisions if d["status"] == "OPEN"),
            "opens_unresolved": sum(1 for o in opens if o["blocking"]),
            "reviews_open": sum(1 for r in reviews if r["kind"] == "open"),
            "stages_a5_only": sum(1 for s in stages if not s["in_body_section_5"]),
            "standards_total": len(standards),
            "standards_unenforced": sum(1 for s in standards if not s["enforced"]),
        },
    }


def filter_stage(index: dict[str, Any], stage: str) -> dict[str, Any]:
    prefix = stage.split(".")[0]

    def hits(stages: list[str]) -> bool:
        return any(s == stage or s.split(".")[0] == prefix for s in stages)

    return {
        "stage_filter": stage,
        "stages": [s for s in index["stages"] if s["stage"] == prefix or s["stage"].startswith(prefix)],
        "decisions": [d for d in index["decisions"] if hits(d["blocks_stages"])],
        "opens": [o for o in index["opens"] if hits(o["blocks_stages"])],
        "reviews": [r for r in index["reviews"] if f"Stage {prefix}" in r["body"] or f"Stage {prefix}" in r["section"]],
    }


def render_text(index: dict[str, Any], sections: list[str]) -> str:
    out: list[str] = []
    if "decisions" in sections and index.get("decisions"):
        out.append("## Decision register (DesignDoc B.3)")
        for d in index["decisions"]:
            flag = "!!" if d["blocking"] else ("? " if d["status"] == "OPEN" else "  ")
            blocks = f"  blocks: {', '.join(d['blocks_stages'])}" if d["blocks_stages"] else ""
            out.append(f"{flag} {d['id']}  {d['status']:<11} {d['decision']}{blocks}")
            if d["blocking"] and d["notes"]:
                out.append(f"       -> {d['notes']}")
        out.append("")
    if "opens" in sections and index.get("opens"):
        out.append("## Open decisions (DbtBestPractices Appendix B)")
        for o in index["opens"]:
            flag = "  " if o["resolved"] else "? "
            blocks = f"  blocks: {', '.join(o['blocks_stages'])}" if o["blocks_stages"] else ""
            out.append(f"{flag} {o['id']}  {o['title']}{blocks}")
            if o["recommendation"]:
                out.append(f"       rec: {o['recommendation']}")
        out.append("")
    if "stages" in sections and index.get("stages"):
        out.append("## Stage inventory (section 5 union A.5)")
        for s in index["stages"]:
            mark = "  " if s["in_body_section_5"] else "!!"
            out.append(f"{mark} Stage {s['stage']:<4} {s['title']}")
            out.append(f"       {s['reconciliation']}")
            if s.get("a5_change"):
                out.append(f"       A.5: {s['a5_change'][:150]}")
        out.append("")
    if "reviews" in sections and index.get("reviews"):
        open_reviews = [r for r in index["reviews"] if r["kind"] == "open"]
        out.append(f"## Review notes ({len(open_reviews)} open)")
        for r in open_reviews:
            out.append(f"   {r['id']:<6} {r['doc']}:{r['line']}  [{r['section'][:40]}]")
            out.append(f"       {r['title'][:160]}")
        out.append("")
    if "standards" in sections and index.get("standards"):
        unenforced = [s for s in index["standards"] if not s["enforced"]]
        out.append(f"## Enforcement matrix: {len(index['standards'])} standards, {len(unenforced)} unenforced")
        for s in unenforced:
            out.append(f"   {s['id']:<6} {s['standard'][:100]}")
        out.append("")
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--section",
        action="append",
        choices=["decisions", "opens", "reviews", "stages", "standards"],
        help="limit output (repeatable); default is all",
    )
    parser.add_argument("--stage", help="only rows touching this stage id, e.g. 3 or 0.3")
    parser.add_argument("--blocking", action="store_true", help="only blocking decisions and unresolved opens")
    parser.add_argument("--format", choices=["json", "text"], default="json")
    args = parser.parse_args()

    index = build_index()
    if args.stage:
        index = filter_stage(index, args.stage)
    if args.blocking:
        index = {
            "decisions": [d for d in index.get("decisions", []) if d["blocking"]],
            "opens": [o for o in index.get("opens", []) if o["blocking"]],
            "stages": [s for s in index.get("stages", []) if not s["in_body_section_5"]],
        }

    sections = args.section or ["decisions", "opens", "reviews", "stages", "standards"]
    if args.format == "text":
        print(render_text(index, sections))
    else:
        payload = {k: v for k, v in index.items() if k in {*sections, "summary", "stage_filter"}}
        print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
