"""Agentic Developer Workflow — orchestrator for the 7-phase ADW."""

from importlib.metadata import version

# Einzige Quelle ist pyproject.toml — eine zweite gepflegte Literal-Version
# drifted beim Release-Bump zwangsläufig.
__version__ = version("adw")
