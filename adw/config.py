"""Target-repo configuration: .adw/config.yaml — fail fast on any error."""

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

NonBlankStr = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]
# strict=True: YAML-Booleans (true/false) sind keine gültigen Sekundenwerte.
PositiveSeconds = Annotated[int, Field(gt=0, strict=True)]
# strict=True: ein YAML-Boolean zählt NICHT als Ganzzahl (D1).
NonNegativeInt = Annotated[int, Field(ge=0, strict=True)]


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys instead of silently overwriting them."""


def _construct_mapping_rejecting_duplicates(loader: _StrictLoader, node, deep=False):
    seen = set()
    merge_seen = False
    for key_node, _ in node.value:
        if key_node.tag == "tag:yaml.org,2002:merge":
            # Merge-Keys (<<: *anchor) flattet SafeLoader.construct_mapping
            # selbst — geerbte Felder sind keine Duplikate. Aber ein ZWEITER
            # expliziter <<-Key im selben Mapping ist einer.
            if merge_seen:
                raise yaml.YAMLError("doppelter Merge-Key '<<'")
            merge_seen = True
            continue
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in seen
        except TypeError as exc:
            raise yaml.YAMLError(f"unhashbarer Mapping-Key: {key!r}") from exc
        if duplicate:
            raise yaml.YAMLError(f"doppelter Key: {key!r}")
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping_rejecting_duplicates
)

CONFIG_RELPATH = Path(".adw") / "config.yaml"

LaneName = Literal["backend", "frontend"]

# Die geschlossene, enumerierte Menge der Haltepunkte. Genau diese zwei Werte
# sind überall zulässig, wo ein Breakpoint benannt wird: die Config-Liste, das
# State-Feld pending_breakpoint und der approval-Event-`gate`.
BreakpointName = Literal["before_integration", "before_push"]


class ConfigError(Exception):
    """Config is missing or invalid — the run must not even start."""


class Gate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: NonBlankStr
    cmd: NonBlankStr
    timeout: PositiveSeconds
    # Markiert das Gate als TDD-Beweis: Fehlschlag NACH dem reinen Test-Lauf
    # belegt RED. Nur markierte Gates laufen im RED-Check des Initial-Builds.
    # strict=True: 1/"yes" sind keine gültigen Booleans (analog timeout).
    tdd: bool = Field(default=False, strict=True)


class LaneConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gates: list[Gate] = Field(min_length=1)


class E2eConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cmd: NonBlankStr
    timeout: PositiveSeconds


class CiConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    poll_interval: PositiveSeconds = 60
    timeout: PositiveSeconds = 2700
    staging_job: NonBlankStr | None = None
    # Explizites Hosting (gitlab | github). None = Auto-Erkennung aus der
    # origin-Remote-URL; bei nicht erkennbarem Host ist der Key Pflicht.
    provider: Literal["gitlab", "github"] | None = None


class CodexConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Effektives Zeitlimit der codex-exec-Subprozesse (Autor UND Review teilen
    # den Wert). Fehlt der Key, bleibt es beim bisherigen Default von 900s.
    timeout: PositiveSeconds = 900


class TraceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Ereignis-Logging (§4.5). Default true; false = gar kein Ereignis-Log.
    # strict=True: nur ein echter Boolean, kein 1/"yes".
    enabled: bool = Field(default=True, strict=True)
    # Automatisches Pruning: geschützte neueste Läufe. 0 = nie automatisch prunen.
    keep_runs: NonNegativeInt = 20


class AdwConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_branch: NonBlankStr
    lanes: dict[LaneName, LaneConfig] = Field(min_length=1)
    e2e: E2eConfig | None = None
    ci: CiConfig = Field(default_factory=CiConfig)
    codex: CodexConfig = Field(default_factory=CodexConfig)
    trace: TraceConfig = Field(default_factory=TraceConfig)
    # Aktive Haltepunkte (leer = heutiges Verhalten). Jedes Element ist ein
    # BreakpointName — ein unbekannter Wert/Typ ist über das Literal ein
    # ValidationError und wird von load() als ConfigError gehoben. Reihenfolge
    # und Mehrfachnennung sind bedeutungslos: die Haltepunkte wirken als Menge.
    breakpoints: list[BreakpointName] = Field(default_factory=list)

    @property
    def is_parallel_capable(self) -> bool:
        return len(self.lanes) > 1

    @classmethod
    def load(cls, repo: Path) -> "AdwConfig":
        path = repo / CONFIG_RELPATH
        if not path.is_file():
            raise ConfigError(f"{path}: config.yaml fehlt — das Ziel-Repo ist nicht ADW-fähig")
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ConfigError(f"{path}: nicht lesbar — {exc}") from exc
        try:
            raw = yaml.load(content, Loader=_StrictLoader)  # noqa: S506 — SafeLoader-Subklasse
        except (yaml.YAMLError, ValueError, RecursionError) as exc:
            # ValueError/RecursionError: PyYAML leakt sie bei Riesen-Integern
            # bzw. extremem Nesting — für den Aufrufer ist beides kaputte Config.
            raise ConfigError(f"{path}: ungültiges YAML — {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigError(f"{path}: Top-Level muss ein Mapping sein")
        try:
            return cls.model_validate(raw)
        except ValidationError as exc:
            raise ConfigError(f"{path}: {exc}") from exc
