"""Harmony CLI package entry point."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("harmony-cli")
except PackageNotFoundError:  # pragma: no cover - during local development without install
    __version__ = "0.0.dev0"

from .cli import main  # re-export for convenience

__all__ = ["main", "__version__"]
