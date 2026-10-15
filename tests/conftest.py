"""Shared fixtures for Home Assistant integration tests."""

from __future__ import annotations

from collections.abc import Generator

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: Generator[None],
) -> Generator[None]:
    """Load custom components from this repository."""
    yield
    return
