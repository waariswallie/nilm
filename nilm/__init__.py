"""NILM pipeline package."""

from pathlib import Path

from . import loader, prep, events, signatures, assign, report

# Ensure legacy modules from src/nilm remain importable for existing code/tests.
_pkg_dir = Path(__file__).resolve().parent
_legacy_dir = _pkg_dir.parent / "src" / "nilm"
if _legacy_dir.exists():
	__path__.append(str(_legacy_dir))  # type: ignore[name-defined]

__all__ = ["loader", "prep", "events", "signatures", "assign", "report"]
