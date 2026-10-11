"""Doc export pipeline: Markdown with SVG diagrams -> interactive HTML / DOCX with EMF."""

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent / "scripts" / "build_docs.py"
_spec = importlib.util.spec_from_file_location("build_docs", _SCRIPT)
build_docs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_docs)


def test_diagram_references_lists_svgs_in_document_order():
    md = "x\n\n![B](diagrams/wf/b.svg)\n\ntext ![A](diagrams/wf/a.de.svg)\n"
    assert build_docs.diagram_references(md) == ["diagrams/wf/b.svg", "diagrams/wf/a.de.svg"]


def test_docx_markdown_swaps_svg_for_emf_with_page_width(tmp_path):
    md = "Intro\n\n![Phases at a glance](diagrams/wf/phases.svg)\n"
    out = build_docs.docx_markdown(md, tmp_path)
    expected = tmp_path / "diagrams" / "wf" / "phases.emf"
    assert f"![Phases at a glance]({expected}){{width=16cm}}" in out
    assert ".svg" not in out


def test_docx_markdown_leaves_other_images_alone(tmp_path):
    md = "![Logo](img/logo.png)\n"
    assert build_docs.docx_markdown(md, tmp_path) == md


def test_html_links_point_to_sibling_exports():
    html = (
        '<a href="ADW-SPEC.de.md#p1">x</a> <a href="SPEC.md">y</a> <a href="ADW-HANDBOOK.md">z</a>'
        ' <a href="../README.md">r</a> <a href="../examples/config.yaml">c</a>'
        ' <a href="#toc">t</a> <a href="https://example.com/a.md">e</a>'
    )
    out = build_docs.html_links(html)
    assert 'href="ADW-SPEC.de.html#p1"' in out
    assert 'href="ADW-HANDBOOK.html"' in out
    # Everything else relative was written for docs/ — the export lives one level deeper.
    assert 'href="../SPEC.md"' in out
    assert 'href="../../README.md"' in out
    assert 'href="../../examples/config.yaml"' in out
    assert 'href="#toc"' in out
    assert 'href="https://example.com/a.md"' in out


def test_inline_diagrams_replaces_img_with_svg_markup(tmp_path):
    svg_dir = tmp_path / "diagrams" / "wf"
    svg_dir.mkdir(parents=True)
    (svg_dir / "phases.svg").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg">'
        '<g data-step="1"/></svg>\n',
        encoding="utf-8",
    )
    html = '<figure>\n<img src="diagrams/wf/phases.svg" alt="Phases" />\n<figcaption>Phases'
    out = build_docs.inline_diagrams(html, tmp_path)
    assert "<img" not in out
    assert "<?xml" not in out
    assert '<div class="dia" data-diagram="wf/phases">' in out
    assert '<g data-step="1"/>' in out


def test_inline_diagrams_fails_fast_on_missing_svg(tmp_path):
    html = '<img src="diagrams/wf/missing.svg" alt="x" />'
    with pytest.raises(FileNotFoundError, match="missing.svg"):
        build_docs.inline_diagrams(html, tmp_path)


def test_check_svg_rejects_constructs_lost_in_emf():
    bad = '<svg><defs><marker id="a"/></defs><path marker-end="url(#a)"/></svg>'
    assert "marker" in " ".join(build_docs.emf_problems(bad))
    assert build_docs.emf_problems('<svg><foreignObject/></svg>')
    assert build_docs.emf_problems('<svg><style>.a{}</style></svg>')
    clean = '<svg><rect fill="#fff"/><polygon points="0,0 1,1"/></svg>'
    assert build_docs.emf_problems(clean) == []


def test_doc_css_keeps_visited_links_on_theme_accent():
    # pandoc's default template sets a:visited to a dark grey, unreadable in dark mode.
    css = (_SCRIPT.parent.parent / "docs" / "assets" / "doc.css").read_text(encoding="utf-8")
    assert "a:visited" in css
