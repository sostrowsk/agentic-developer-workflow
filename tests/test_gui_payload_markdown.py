"""Multi-line payload strings render as Markdown, the rest stays a field list.

A run's `issue` and an agent's `prompt` ARE Markdown — headings, lists, fenced
code. Shown as source they are readable but not legible. The server-rendered pane
therefore splits the payload into blocks: the field-list scaffolding stays literal
text, and every multi-line string value is rendered.

Markdown is NOT run over the whole field list: CommonMark folds consecutive lines
into one paragraph (`tool: Read` + `tool_use_id: …` would become a single run-on
line) and treats four-space indentation as a code block, so the scaffolding would be
destroyed by the very pass meant to make it readable.

Safety: raw HTML in a payload is escaped, never executed, and a link renders as
literal text — payload text is agent-generated and must not become a click target.
"""

import os

from fastapi.testclient import TestClient

from adw.gui.app import _payload_blocks, _render_markdown, create_app
from adw.gui.registry import _slug
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    home,
    rec,
    run_start_payload,
    write_run,
)

RUN_ID = "aaaa1111"


def _kinds(blocks):
    return [b["kind"] for b in blocks]


def test_a_single_line_string_stays_in_the_text_block():
    blocks = _payload_blocks({"tool": "Read", "lane": "backend"})

    assert _kinds(blocks) == ["text"]
    assert blocks[0]["text"].splitlines() == ["tool: Read", "lane: backend"]


def test_a_multiline_string_becomes_its_own_markdown_block():
    blocks = _payload_blocks({"lane": "backend", "issue": "# Head\n\n- one\n- two"})

    assert _kinds(blocks) == ["text", "markdown"]
    assert blocks[0]["text"].splitlines() == ["lane: backend", "issue:"]
    assert "<h1>Head</h1>" in blocks[1]["html"]
    assert "<li>one</li>" in blocks[1]["html"]


def test_fields_after_a_markdown_block_return_to_text():
    blocks = _payload_blocks({"issue": "# H\n\ntext", "parallel": False})

    assert _kinds(blocks) == ["text", "markdown", "text"]
    assert blocks[2]["text"].splitlines() == ["parallel: false"]


def test_a_multiline_string_nested_in_an_object_renders_too():
    blocks = _payload_blocks({"input": {"content": "# Deep\n\nbody", "path": "/x"}})

    assert _kinds(blocks) == ["text", "markdown", "text"]
    assert "<h1>Deep</h1>" in blocks[1]["html"]
    assert blocks[2]["text"].splitlines() == ["  path: /x"]


def test_raw_html_in_a_payload_is_escaped_not_executed():
    html = _render_markdown("A <script>alert(1)</script> and <b>bold</b>.")

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_links_render_as_literal_text():
    """Payload text is agent-generated: it never becomes a click target."""
    html = _render_markdown("see [the docs](http://example.com/) and http://bare.example/")

    assert "<a " not in html and "href=" not in html
    assert "[the docs](http://example.com/)" in html


def test_code_blocks_and_tables_survive():
    html = _render_markdown("| a | b |\n|---|---|\n| 1 | 2 |\n\n```py\nx = 1\n```")

    assert "<table>" in html and "<td>1</td>" in html
    assert "<code" in html and "x = 1" in html


def _slug_for(repo):
    return _slug(os.path.normpath(str(repo.resolve())))


def test_the_run_pane_renders_its_issue_as_markdown(home, tmp_path):  # noqa: F811
    """End to end: the run node's pane shows the issue as a document, not as source."""
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    payload = run_start_payload("# Ziel\n\n- erstens\n- zweitens")
    lines = [
        rec(1, "run", "start", "R", None, sec=0, payload=payload),
        rec(2, "run", "end", "R", None, sec=1,
            payload={"status": "done", "totals": {"duration": 1.0}}),
    ]
    write_run(repo, RUN_ID, lines, phase="done")
    client = TestClient(create_app(repos=[str(repo)]))

    html = client.get(f"/runs/{_slug_for(repo)}/{RUN_ID}").text
    i, j = html.find('class="panes"'), html.find('class="problems"')
    panes = html[i:j]

    assert "<h1>Ziel</h1>" in panes
    assert "<li>erstens</li>" in panes
    # The source form is gone from the PANE (the Raw tab still shows the log
    # verbatim — that is its job).
    assert "# Ziel" not in panes


def _agent_run_lines(prompt, answer, message):
    return [
        rec(1, "run", "start", "R", None, sec=0, payload=run_start_payload("plain")),
        rec(2, "agent.run", "start", "A", "R", sec=1,
            payload={"agent": "spec_agent", "prompt": prompt, "system_append": ""}),
        rec(3, "agent.message", "point", "A", sec=2,
            payload={"role": "assistant", "text": message}),
        rec(4, "agent.run", "end", "A", "R", sec=3,
            payload={"result_text": answer, "is_error": False}),
        rec(5, "run", "end", "R", None, sec=4,
            payload={"status": "done", "totals": {"duration": 1.0}}),
    ]


def _panes(client, slug):
    html = client.get(f"/runs/{slug}/{RUN_ID}").text
    i, j = html.find('class="panes"'), html.find('class="problems"')
    return html[i:j]


def test_agent_run_prompt_answer_and_messages_render_as_markdown(home, tmp_path):  # noqa: F811
    """The prompt and answer tabs are the fields most worth rendering — a task string
    and an agent's reply are Markdown. They must not stay source text while the
    generic payload block renders."""
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, RUN_ID, _agent_run_lines(
        "# Aufgabe\n\n- eins", "## Ergebnis\n\n- fertig", "### Zwischenstand\n\n- laeuft",
    ), phase="done")
    client = TestClient(create_app(repos=[str(repo)]))

    panes = _panes(client, _slug_for(repo))

    assert "<h1>Aufgabe</h1>" in panes and "<li>eins</li>" in panes       # prompt
    assert "<h2>Ergebnis</h2>" in panes and "<li>fertig</li>" in panes    # answer
    assert "<h3>Zwischenstand</h3>" in panes                              # assistant message
    assert "# Aufgabe" not in panes and "## Ergebnis" not in panes


def test_the_prompt_diff_stays_literal(home, tmp_path):  # noqa: F811
    """A unified diff is NOT Markdown: its leading `-`/`+` would become list items and
    `---`/`+++` a heading rule. It stays verbatim."""
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, RUN_ID, _agent_run_lines("# A\n\nx", "done", "msg"), phase="done")
    client = TestClient(create_app(repos=[str(repo)]))

    html = client.get(f"/runs/{_slug_for(repo)}/{RUN_ID}").text

    assert 'class="prompt-diff"' not in html or "<pre class=\"prompt-diff\">" in html


def test_a_top_level_empty_payload_still_shows_something():
    """Regression: an empty mapping or list is a VALID payload. It must render as
    `{}` / `[]` — the same as a nested empty container — never as a blank pane."""
    assert _payload_blocks({}) == [{"kind": "text", "text": "{}"}]
    assert _payload_blocks([]) == [{"kind": "text", "text": "[]"}]


def test_rendered_markdown_contains_its_own_wide_content():
    """The page is globally `overflow-x: hidden`, so a wide rendered table or code
    line must scroll INSIDE its block instead of being clipped at the pane edge."""
    import re

    css = TestClient(create_app(repos=[])).get("/static/app.css").text
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    contained = False
    for sel, body in re.findall(r"([^{}]*)\{([^}]*)\}", css):
        # The containment must sit on the BLOCK itself, not only on a `pre` inside
        # it — a wide table is not a `pre` and would still be clipped.
        if sel.strip() != ".raw-md":
            continue
        if "overflow-x" in body and "auto" in body:
            contained = True
    assert contained, "app.css lets rendered Markdown overflow the pane"


def _single_line_run_lines():
    return [
        rec(1, "run", "start", "R", None, sec=0, payload=run_start_payload("plain")),
        rec(2, "agent.run", "start", "A", "R", sec=1,
            payload={"agent": "a", "prompt": "einzeilig", "system_append": ""}),
        rec(4, "agent.run", "end", "A", "R", sec=3,
            payload={"result_text": "einzeilige Antwort", "is_error": False}),
        rec(5, "run", "end", "R", None, sec=4,
            payload={"status": "done", "totals": {"duration": 1.0}}),
    ]


def test_single_line_blocks_keep_their_semantic_classes(home, tmp_path):  # noqa: F811
    """Regression: a class attribute built inside a Jinja expression is autoescaped,
    so `class="final"` arrives as `class=&#34;final&#34;` and the browser reads the
    quotes as part of the class name — every existing `.final` / `.assistant` rule
    silently stops matching."""
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, RUN_ID, _single_line_run_lines(), phase="done")
    client = TestClient(create_app(repos=[str(repo)]))

    html = client.get(f"/runs/{_slug_for(repo)}/{RUN_ID}").text

    assert "class=&#34;" not in html
    assert '<pre class="final">' in html


def test_an_empty_system_append_renders_no_block(home, tmp_path):  # noqa: F811
    """Regression: prompt and system append used to share one <pre>. Split into two
    blocks, an empty append leaves a styled, visibly blank panel — it is omitted."""
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_run(repo, RUN_ID, _single_line_run_lines(), phase="done")
    client = TestClient(create_app(repos=[str(repo)]))

    html = client.get(f"/runs/{_slug_for(repo)}/{RUN_ID}").text

    assert "system-append" not in html
