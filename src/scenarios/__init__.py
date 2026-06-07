"""Scenario plugins — each module exposes build_scenario() -> Scenario.

Submodules are imported on demand (e.g. ``from src.scenarios import mystery_roots``)
rather than eagerly here, so the registry the scenarios delegate to can import
``src.scenarios.base`` without a circular import.
"""

__all__ = ["thousand_token_wood", "mystery_roots"]
