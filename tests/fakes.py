"""In-memory Modbus stand-in for integration tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from custom_components.growatt_solar.errors import DeviceReadError, TransportError


class ScriptedClient:
    """Returns canned register blocks and records how it was used."""

    def __init__(
        self,
        blocks: dict[tuple[str, int], list[int]] | None = None,
        *,
        failure: Callable[[str, int, int], Exception | None] | None = None,
        pause: float = 0,
    ) -> None:
        self.blocks = blocks or {}
        self.failure = failure
        self.pause = pause
        self.closed = False
        self.calls: list[tuple[str, int, int]] = []
        self.writes: list[tuple[int, int]] = []
        self.block_writes: list[tuple[int, list[int]]] = []
        self.confirm_writes = True
        self.in_flight = 0
        self.max_in_flight = 0

    async def read_registers(self, function: str, address: int, count: int) -> list[int]:
        self.calls.append((function, address, count))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(self.pause)
        try:
            if self.failure is not None:
                error = self.failure(function, address, count)
                if error is not None:
                    raise error
            return self._words(function, address, count)
        finally:
            self.in_flight -= 1

    async def write_register(self, address: int, value: int) -> None:
        self.writes.append((address, value))
        if self.confirm_writes:
            self._put(address, value)

    async def write_registers(self, address: int, values: list[int]) -> None:
        self.block_writes.append((address, list(values)))
        if not self.confirm_writes:
            return
        for offset, value in enumerate(values):
            self._put(address + offset, value)

    def _put(self, address: int, value: int) -> None:
        for (function, start), words in self.blocks.items():
            if function == "holding" and start <= address < start + len(words):
                words[address - start] = value
                return
        self.blocks[("holding", address)] = [value]

    def _words(self, function: str, address: int, count: int) -> list[int]:
        exact = self.blocks.get((function, address))
        if exact is not None:
            if len(exact) < count:
                raise DeviceReadError("the inverter returned an incomplete register block")
            return exact[:count]
        for (kind, start), words in self.blocks.items():
            if kind == function and start <= address and address + count <= start + len(words):
                offset = address - start
                return words[offset : offset + count]
        return [0] * count

    async def close(self) -> None:
        self.closed = True


class ClientFactory:
    """Patch target that builds a new scripted client per connection."""

    def __init__(
        self,
        blocks: dict[tuple[str, int], list[int]] | None = None,
        *,
        failure: Callable[[str, int, int], Exception | None] | None = None,
        pause: float = 0,
    ) -> None:
        self.blocks = blocks or {}
        self.failure = failure
        self.pause = pause
        self.clients: list[ScriptedClient] = []

    def __call__(self, *args: object, **kwargs: object) -> ScriptedClient:
        del args, kwargs
        client = ScriptedClient(self.blocks, failure=self.failure, pause=self.pause)
        self.clients.append(client)
        return client


def ascii_registers(text: str, width: int) -> list[int]:
    """Pack text into big-endian Modbus character registers."""
    padded = text.encode("ascii").ljust(width * 2, b"\x00")[: width * 2]
    return [(padded[index] << 8) | padded[index + 1] for index in range(0, len(padded), 2)]


def raise_transport(function: str, address: int, count: int) -> Exception | None:
    """Fail every read the way a dead gateway does."""
    del function, address, count
    return TransportError("timed out waiting for the inverter")
