"""Backward compatibility shim for legacy /kg routes.

The new implementation lives in :mod:`routers.graph`. This module re-exports the
symbols so existing imports keep working while the REST prefix migrates to
``/graph``.
"""

from __future__ import annotations

from .graph import router

__all__ = ["router"]
