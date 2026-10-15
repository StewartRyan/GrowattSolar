"""Failures that callers can distinguish without parsing log text."""

from __future__ import annotations


class MappingError(Exception):
    """A register-mapping file failed validation."""

    def __init__(self, messages: list[str]) -> None:
        self.messages = messages
        super().__init__("; ".join(messages))


class TransportError(Exception):
    """The TCP connection failed, timed out, or dropped mid-read."""


class DeviceReadError(Exception):
    """The inverter answered, but the payload cannot be used."""
