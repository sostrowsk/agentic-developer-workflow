"""Build the doc exports: interactive HTML and DOCX with EMF diagrams.

Sources are the Markdown files docs/ADW-*.md. Diagrams are referenced there as
plain images (`![Caption](diagrams/<doc>/<name>.svg)`), so GitHub shows them too.

- HTML: pandoc -> the SVG is inlined and driven by docs/assets/diagram.js
  (step-through, hover details, flowing edges). CSS + JS are inlined, no CDN.
- DOCX: each SVG is converted to EMF with Inkscape and embedded at page width.

Usage: python scripts/build_docs.py          (writes docs/export/)
Requires: pandoc, inkscape.
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"
EXPORT = DOCS / "export"
ASSETS = DOCS / "assets"
SOURCES = ("ADW-HANDBOOK", "ADW-WORKFLOW", "ADW-SPEC")

_DIAGRAM_RE = re.compile(r"!\[([^\]]*)\]\((diagrams/[^)\s]+?)\.svg\)")
_IMG_RE = re.compile(r'<img src="diagrams/([^"]+?)\.svg"[^>]*?/?>')
_HREF_RE = re.compile(r'href="([^"#:/][^":]*)"')
_SIBLING_RE = re.compile(r"(ADW-[A-Z]+(?:\.de)?)\.md(#.*)?")
_XML_PROLOG_RE = re.compile(r"<\?xml[^>]*\?>\s*")
# Inkscape's EMF export drops these silently — arrowheads vanish, text disappears.
_EMF_UNSAFE = {
    "marker": re.compile(r"<marker\b|marker-(start|mid|end)="),
    "foreignObject": re.compile(r"<foreignObject\b"),
    "<style> element (use presentation attributes)": re.compile(r"<style\b"),
    "filter": re.compile(r"<filter\b|filter="),
}


def diagram_references(md: str) -> list[str]:
    return [f"{path}.svg" for _, path in _DIAGRAM_RE.findall(md)]


def docx_markdown(md: str, emf_root: Path) -> str:
    def to_emf(match: re.Match) -> str:
        caption, path = match.groups()
        return f"![{caption}]({emf_root / path}.emf){{width=16cm}}"

    return _DIAGRAM_RE.sub(to_emf, md)


def html_links(html: str) -> str:
    """Links are written relative to docs/; the exports live in docs/export/."""

    def retarget(match: re.Match) -> str:
        href = match.group(1)
        sibling = _SIBLING_RE.fullmatch(href)
        if sibling and sibling.group(1).removesuffix(".de") in SOURCES:
            return f'href="{sibling.group(1)}.html{sibling.group(2) or ""}"'
        return f'href="../{href}"'

    return _HREF_RE.sub(retarget, html)


def inline_diagrams(html: str, docs_dir: Path) -> str:
    def inline(match: re.Match) -> str:
        rel = match.group(1)
        svg_file = docs_dir / "diagrams" / f"{rel}.svg"
        if not svg_file.is_file():
            raise FileNotFoundError(f"diagram referenced but missing: {svg_file}")
        svg = _XML_PROLOG_RE.sub("", svg_file.read_text(encoding="utf-8")).strip()
        diagram_id = rel.removesuffix(".de")
        return f'<div class="dia" data-diagram="{diagram_id}">{svg}</div>'

    return _IMG_RE.sub(inline, html)


def emf_problems(svg: str) -> list[str]:
    return [name for name, pattern in _EMF_UNSAFE.items() if pattern.search(svg)]


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"failed: {' '.join(cmd)}\n{result.stderr}")


def _lang(stem: str) -> str:
    return "de" if stem.endswith(".de") else "en"


def _build_html(md_file: Path, header: Path) -> None:
    out = EXPORT / f"{md_file.stem}.html"
    title = md_file.read_text(encoding="utf-8").splitlines()[0].lstrip("# ").strip()
    _run([
        "pandoc", str(md_file), "-f", "gfm+implicit_figures", "-t", "html5",
        "-s", "--toc", "--toc-depth=2",
        "-M", f"pagetitle={title}", "-M", f"lang={_lang(md_file.stem)}",
        "-H", str(header), "-o", str(out),
    ])
    html = out.read_text(encoding="utf-8")
    out.write_text(html_links(inline_diagrams(html, DOCS)), encoding="utf-8")


def _build_docx(md_file: Path, emf_root: Path, work: Path) -> None:
    md = md_file.read_text(encoding="utf-8")
    staged = work / md_file.name
    staged.write_text(docx_markdown(md, emf_root), encoding="utf-8")
    _run([
        "pandoc", str(staged), "-f", "gfm+attributes+implicit_figures", "-t", "docx",
        "--toc", "--toc-depth=2",
        "-M", f"lang={_lang(md_file.stem)}", "-o", str(EXPORT / f"{md_file.stem}.docx"),
    ])


def _convert_emf(svg_paths: set[str], emf_root: Path) -> None:
    problems = []
    for rel in sorted(svg_paths):
        svg_file = DOCS / rel
        found = emf_problems(svg_file.read_text(encoding="utf-8"))
        if found:
            problems.append(f"{rel}: {', '.join(found)}")
            continue
        emf = emf_root / Path(rel).with_suffix(".emf")
        emf.parent.mkdir(parents=True, exist_ok=True)
        _run(["inkscape", str(svg_file), "--export-type=emf", f"--export-filename={emf}"])
    if problems:
        sys.exit("SVG constructs that EMF export drops:\n" + "\n".join(problems))


def main() -> None:
    sources = [DOCS / f"{name}{suffix}.md" for name in SOURCES for suffix in ("", ".de")]
    svgs = {ref for md in sources for ref in diagram_references(md.read_text(encoding="utf-8"))}
    EXPORT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        header = work / "header.html"
        css = (ASSETS / "doc.css").read_text(encoding="utf-8")
        js = (ASSETS / "diagram.js").read_text(encoding="utf-8")
        header.write_text(f"<style>\n{css}\n</style>\n<script>\n{js}\n</script>\n", "utf-8")
        _convert_emf(svgs, work / "emf")
        for md_file in sources:
            _build_html(md_file, header)
            _build_docx(md_file, work / "emf", work)
            print(f"{md_file.name} -> export/{md_file.stem}.html, .docx")


if __name__ == "__main__":
    main()
