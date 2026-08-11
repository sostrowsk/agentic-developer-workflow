"""RED tests for Aufgabe B — the read-only artifact-content endpoint.

``GET /api/runs/{repo}/{run_id}/artifacts/{name}`` serves the content of one
whitelisted artifact of THIS run. ``{name}`` is a SINGLE path segment, never a
filesystem path; it is resolved through a deterministic lookup against the fixed
whitelist — a top-level name (``spec.md``) maps to the run directory, a flat draft
name ``<stem>.<author>.<ext>`` (author ∈ {claude, codex}) maps to
``drafts/<name>``. Every other name is unknown. Unknown / nested / encoded-
separator / traversal names and escaping symlinks yield 404, never 5xx, and never
read outside the run directory (B4/B5/B6).

Derived from .adw/spec.md (B4–B7), .adw/contract.yaml (getRunArtifact,
x-adw-artifact-mapping) and .adw/plan.md §2. Tested via FastAPI's TestClient
against a fixture run directory. RED until the route exists.
"""

import os
import pathlib

from fastapi.testclient import TestClient

from adw.gui.app import create_app
from adw.gui.registry import _slug
from tests.gui_app_helpers import (  # noqa: F401 — home used as a fixture
    LARGE_ARTIFACT_HEAD,
    LARGE_ARTIFACT_TAIL,
    artifact_body,
    home,
    large_artifact_body,
    write_artifacts_run,
    write_symlink_escape_artifact,
)

RUN_ID = "aaaa1111"
SECRET_OUTSIDE = "SECRETOUTSIDECONTENTMUSTNEVERBESERVED"


def _slug_for(repo):
    return _slug(os.path.normpath(str(repo.resolve())))


def _artifacts_client(tmp_path, **kw):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_artifacts_run(repo, RUN_ID, **kw)
    client = TestClient(create_app(repos=[str(repo)]))
    slug = _slug_for(repo)
    return client, f"/api/runs/{slug}/{RUN_ID}/artifacts"


def _read_spy(monkeypatch):
    """Record every path a ``pathlib.Path`` CONTENT read touches, so a rejection
    can be proven to never read the filesystem / never read outside the run dir."""
    reads = []
    for meth in ("read_bytes", "read_text", "open"):
        orig = getattr(pathlib.Path, meth)

        def make(orig):
            def wrapper(self, *a, **k):
                reads.append(str(self))
                return orig(self, *a, **k)
            return wrapper

        monkeypatch.setattr(pathlib.Path, meth, make(orig))
    return reads


def test_draft_name_serves_the_flat_drafts_file(home, tmp_path):  # noqa: F811
    """B4: ``spec.claude.md`` (a flat draft name) maps to ``drafts/spec.claude.md``
    and is served in full — its content is the DRAFT's, distinct from the synthesis
    ``spec.md``."""
    client, base = _artifacts_client(tmp_path)

    resp = client.get(f"{base}/spec.claude.md")
    assert resp.status_code == 200
    assert resp.text == artifact_body("spec.claude.md")
    assert resp.text != artifact_body("spec.md")  # the draft, not the synthesis


def test_top_level_name_serves_the_run_directory_file(home, tmp_path):  # noqa: F811
    """B4: a top-level whitelist name (``spec.md``) maps to that file in the run
    directory; the served content matches exactly."""
    client, base = _artifacts_client(tmp_path)

    resp = client.get(f"{base}/spec.md")
    assert resp.status_code == 200
    assert resp.text == artifact_body("spec.md")


def test_nested_encoded_and_traversal_names_are_404_without_a_read(home, tmp_path, monkeypatch):  # noqa: F811,E501
    """B5: a ``drafts/…``-prefixed name, its encoded-separator form (``drafts%2F…``)
    and traversal attempts — ``..`` sequences, an absolute path, a raw or encoded
    ``/`` or ``\\`` in the name — fail the single-segment / whitelist check and
    yield 404 WITHOUT touching the filesystem (a content-read spy proves it)."""
    client, base = _artifacts_client(tmp_path)
    reads = _read_spy(monkeypatch)

    for name in (
        "drafts/spec.claude.md",    # nested separator (real draft path)
        "drafts%2Fspec.claude.md",  # encoded separator
        "..%2F..%2Fetc%2Fpasswd",   # encoded traversal
        "..%2Fstate.json",          # encoded traversal to a sibling file
        "spec.claude%5Cmd",         # encoded backslash
        "spec.claude\\md",          # raw backslash (single segment)
        "%2Fetc%2Fpasswd",          # encoded absolute path
    ):
        assert client.get(f"{base}/{name}").status_code == 404, name

    # No mapped file was ever read for any rejected name.
    assert reads == []


def test_escaping_symlink_is_404_and_target_is_never_read(home, tmp_path, monkeypatch):  # noqa: F811,E501
    """B5: a whitelisted name whose file is a symlink pointing OUT of the run
    directory yields 404; the target is never read and never served."""
    outside = tmp_path / "outside" / "secret.md"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text(SECRET_OUTSIDE + "\n")

    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    write_symlink_escape_artifact(repo, RUN_ID, outside, name="followups.md")
    client = TestClient(create_app(repos=[str(repo)]))
    base = f"/api/runs/{_slug_for(repo)}/{RUN_ID}/artifacts"

    reads = _read_spy(monkeypatch)
    resp = client.get(f"{base}/followups.md")

    assert resp.status_code == 404
    assert SECRET_OUTSIDE not in resp.text
    assert not any(str(outside) in r for r in reads)  # the target is never read


def test_unknown_flat_names_are_404(home, tmp_path):  # noqa: F811
    """B5: an unknown flat name — a draft with an unknown author (``spec.gpt.md``)
    or a real-but-non-whitelisted file (``state.json``) — is 404."""
    client, base = _artifacts_client(tmp_path)

    assert client.get(f"{base}/spec.gpt.md").status_code == 404   # author not in {claude,codex}
    assert client.get(f"{base}/state.json").status_code == 404    # present, not whitelisted
    assert client.get(f"{base}/events.jsonl").status_code == 404  # present, not whitelisted


def test_failure_marker_has_no_external_name(home, tmp_path):  # noqa: F811
    """B6: a ``*.FAILED`` marker in ``drafts/`` has no external name and is never
    served — neither by its marker name nor as the missing draft it stands in for."""
    client, base = _artifacts_client(
        tmp_path, drafts=("spec.claude.md",), failed_markers=("spec.codex.FAILED",)
    )

    assert client.get(f"{base}/spec.codex.FAILED").status_code == 404  # the marker itself
    assert client.get(f"{base}/spec.codex.md").status_code == 404      # the absent draft


def test_large_artifact_is_served_in_full(home, tmp_path):  # noqa: F811
    """B7 (server side): the route returns the COMPLETE large artifact — both the
    head and tail sentinels — so the display's bounded initial render always has
    the full content to draw on. No 5xx on a big artifact."""
    body = large_artifact_body()
    client, base = _artifacts_client(tmp_path, large_name="plan.md", large_body=body)

    resp = client.get(f"{base}/plan.md")
    assert resp.status_code == 200
    assert resp.text.count(LARGE_ARTIFACT_HEAD) == 1
    assert resp.text.count(LARGE_ARTIFACT_TAIL) == 1
    assert len(resp.text) >= 500 * 1024


def test_absent_or_non_file_artifact_is_404_not_5xx(home, tmp_path):  # noqa: F811
    """B3: a resolvable-but-absent file (``escalation.md`` on a run that did not
    escalate) and a mapped name resolving to a NON-file (a directory) are 404 —
    a missing artifact is never a 5xx."""
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    # A run without escalation.md; contract.yaml is written as a DIRECTORY.
    write_artifacts_run(
        repo, RUN_ID,
        top_level=("issue.md", "spec.md", "plan.md", "followups.md"),
    )
    (repo / ".adw" / "runs" / RUN_ID / "contract.yaml").mkdir()
    client = TestClient(create_app(repos=[str(repo)]))
    base = f"/api/runs/{_slug_for(repo)}/{RUN_ID}/artifacts"

    assert client.get(f"{base}/escalation.md").status_code == 404  # resolvable but absent
    assert client.get(f"{base}/contract.yaml").status_code == 404  # maps to a directory


def test_artifact_route_keeps_containment_behavior(home, tmp_path):  # noqa: F811
    """B5 + existing containment: an unknown slug is 404, a run_id violating
    RUN_ID_RE is 400, a valid-format but absent run is 404 — never 5xx, even with a
    well-formed artifact name."""
    client, base = _artifacts_client(tmp_path)
    slug = _slug_for(tmp_path / "repo")

    assert client.get(f"/api/runs/no-such-slug/{RUN_ID}/artifacts/spec.md").status_code == 404
    assert client.get(f"/api/runs/{slug}/zzzzzzzz/artifacts/spec.md").status_code == 400
    assert client.get(f"/api/runs/{slug}/deadbeef/artifacts/spec.md").status_code == 404
