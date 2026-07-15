import pytest

from adw.config import AdwConfig, ConfigError
from tests.conftest import DEFAULT_CONFIG, write_config


def test_valid_config_loads(target_repo):
    cfg = AdwConfig.load(target_repo)
    assert cfg.base_branch == "staging"
    assert list(cfg.lanes) == ["backend"]
    gate = cfg.lanes["backend"].gates[0]
    assert gate.name == "pass-gate"
    assert gate.cmd == "true"
    assert gate.timeout == 10
    assert cfg.ci.staging_job == "deploy-staging"


def test_missing_config_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="config.yaml"):
        AdwConfig.load(tmp_path)


def test_gate_without_timeout_raises(target_repo):
    write_config(
        target_repo,
        """\
base_branch: staging
lanes:
  backend:
    gates:
      - {name: pytest, cmd: "pytest"}
""",
    )
    with pytest.raises(ConfigError, match="timeout"):
        AdwConfig.load(target_repo)


def test_unknown_top_level_key_raises(target_repo):
    write_config(
        target_repo,
        """\
base_branch: staging
lanes:
  backend:
    gates:
      - {name: g, cmd: "true", timeout: 5}
tippfehler: 1
""",
    )
    with pytest.raises(ConfigError, match="tippfehler"):
        AdwConfig.load(target_repo)


def test_lane_without_gates_raises(target_repo):
    write_config(target_repo, "base_branch: staging\nlanes:\n  backend:\n    gates: []\n")
    with pytest.raises(ConfigError, match="gates"):
        AdwConfig.load(target_repo)


def test_ci_defaults_apply(target_repo):
    write_config(
        target_repo,
        """\
base_branch: staging
lanes:
  backend:
    gates:
      - {name: g, cmd: "true", timeout: 5}
""",
    )
    cfg = AdwConfig.load(target_repo)
    assert cfg.ci.poll_interval == 60
    assert cfg.ci.timeout == 2700
    assert cfg.ci.staging_job is None


def test_two_lanes_enable_parallel(target_repo):
    write_config(
        target_repo,
        """\
base_branch: staging
lanes:
  backend:
    gates:
      - {name: g, cmd: "true", timeout: 5}
  frontend:
    gates:
      - {name: lint, cmd: "true", timeout: 5}
e2e:
  cmd: "npx playwright test"
  timeout: 1800
""",
    )
    cfg = AdwConfig.load(target_repo)
    assert cfg.is_parallel_capable
    assert cfg.e2e.cmd == "npx playwright test"


def test_single_lane_is_not_parallel_capable(target_repo):
    cfg = AdwConfig.load(target_repo)
    assert not cfg.is_parallel_capable


def test_blank_gate_cmd_raises(target_repo):
    write_config(
        target_repo,
        """\
base_branch: staging
lanes:
  backend:
    gates:
      - {name: g, cmd: "   ", timeout: 5}
""",
    )
    with pytest.raises(ConfigError, match="cmd"):
        AdwConfig.load(target_repo)


def test_undecodable_config_raises_config_error(target_repo):
    (target_repo / ".adw" / "config.yaml").write_bytes(b"\xff\xfe kaputt \xff")
    with pytest.raises(ConfigError):
        AdwConfig.load(target_repo)


def test_duplicate_yaml_key_raises(target_repo):
    write_config(
        target_repo,
        """\
base_branch: staging
base_branch: production
lanes:
  backend:
    gates:
      - {name: g, cmd: "true", timeout: 5}
""",
    )
    with pytest.raises(ConfigError, match="[Dd]oppelt"):
        AdwConfig.load(target_repo)


def test_boolean_timeout_raises(target_repo):
    write_config(
        target_repo,
        """\
base_branch: staging
lanes:
  backend:
    gates:
      - {name: g, cmd: "true", timeout: true}
""",
    )
    with pytest.raises(ConfigError, match="timeout"):
        AdwConfig.load(target_repo)


def test_sequence_as_mapping_key_raises_config_error(target_repo):
    write_config(target_repo, "? [a, b]\n: 1\n")
    with pytest.raises(ConfigError):
        AdwConfig.load(target_repo)


def test_blank_staging_job_raises(target_repo):
    write_config(
        target_repo,
        """\
base_branch: staging
lanes:
  backend:
    gates:
      - {name: g, cmd: "true", timeout: 5}
ci:
  staging_job: "  "
""",
    )
    with pytest.raises(ConfigError, match="staging_job"):
        AdwConfig.load(target_repo)


def test_blank_base_branch_raises(target_repo):
    write_config(
        target_repo,
        """\
base_branch: "  "
lanes:
  backend:
    gates:
      - {name: g, cmd: "true", timeout: 5}
""",
    )
    with pytest.raises(ConfigError, match="base_branch"):
        AdwConfig.load(target_repo)


def test_oversized_yaml_integer_raises_config_error(target_repo):
    write_config(
        target_repo,
        "base_branch: staging\nlanes:\n  backend:\n    gates:\n"
        '      - {name: g, cmd: "true", timeout: ' + "9" * 5000 + "}\n",
    )
    with pytest.raises(ConfigError):
        AdwConfig.load(target_repo)


def test_deeply_nested_yaml_raises_config_error(target_repo):
    write_config(target_repo, "a: " + "[" * 10000 + "]" * 10000 + "\n")
    with pytest.raises(ConfigError):
        AdwConfig.load(target_repo)


def test_yaml_merge_keys_are_supported(target_repo):
    write_config(
        target_repo,
        """\
base_branch: staging
lanes:
  backend:
    gates:
      - &pytest_gate {name: pytest, cmd: "pytest -x", timeout: 600}
  frontend:
    gates:
      - <<: *pytest_gate
        name: vitest
        cmd: "npx vitest run"
""",
    )
    cfg = AdwConfig.load(target_repo)
    assert cfg.lanes["frontend"].gates[0].timeout == 600
    assert cfg.lanes["frontend"].gates[0].name == "vitest"


def test_repeated_merge_key_raises(target_repo):
    write_config(
        target_repo,
        """\
defaults1: &d1 {name: a, cmd: "true", timeout: 5}
defaults2: &d2 {name: b, cmd: "true", timeout: 5}
base_branch: staging
lanes:
  backend:
    gates:
      - <<: *d1
        <<: *d2
""",
    )
    with pytest.raises(ConfigError, match="doppelt"):
        AdwConfig.load(target_repo)


def test_invalid_yaml_raises(target_repo):
    write_config(target_repo, "base_branch: [kaputt\n")
    with pytest.raises(ConfigError, match="YAML"):
        AdwConfig.load(target_repo)


def test_unknown_lane_name_raises(target_repo):
    write_config(
        target_repo,
        """\
base_branch: staging
lanes:
  middleware:
    gates:
      - {name: g, cmd: "true", timeout: 5}
""",
    )
    with pytest.raises(ConfigError, match="middleware"):
        AdwConfig.load(target_repo)


def test_ci_provider_accepts_gitlab_and_github(target_repo):
    write_config(
        target_repo,
        DEFAULT_CONFIG.replace("provider: gitlab", "provider: github"),
    )
    cfg = AdwConfig.load(target_repo)
    assert cfg.ci.provider == "github"


def test_ci_provider_rejects_unknown_value(target_repo):
    write_config(
        target_repo,
        DEFAULT_CONFIG.replace("provider: gitlab", "provider: bitbucket"),
    )
    with pytest.raises(ConfigError, match="provider|bitbucket"):
        AdwConfig.load(target_repo)
