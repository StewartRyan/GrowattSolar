"""Modbus/TCP transport behavior."""

from __future__ import annotations

from pathlib import Path

import pytest
from custom_components.growatt_solar.errors import DeviceReadError, TransportError
from custom_components.growatt_solar.modbus_client import GrowattModbusClient

ROOT = Path(__file__).resolve().parents[1]


class FakeResult:
    def __init__(self, registers: list[int] | None = None, *, error: bool = False) -> None:
        self.registers = registers or []
        self._error = error

    def isError(self) -> bool:
        return self._error


class FakeTcp:
    """Stand-in for pymodbus's async TCP client."""

    next_input: FakeResult | Exception = FakeResult([0])
    next_holding: FakeResult | Exception = FakeResult([0])
    connect_ok = True
    delay = 0.0
    queue: list[FakeResult | Exception] = []
    instances: list[FakeTcp] = []

    def __init__(self, host: str, *, port: int, timeout: float, retries: int) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.retries = retries
        self.connected = False
        self.connect_count = 0
        self.close_count = 0
        self.calls: list[tuple[str, int, int, int]] = []
        self.writes: list[tuple[int, int, int]] = []
        self.input_result = FakeTcp.next_input
        self.holding_result = FakeTcp.next_holding
        self.delay = FakeTcp.delay
        self.connect_ok = FakeTcp.connect_ok
        FakeTcp.instances.append(self)

    async def connect(self) -> bool:
        self.connect_count += 1
        if not self.connect_ok:
            return False
        self.connected = True
        return True

    def close(self) -> None:
        self.close_count += 1
        self.connected = False

    async def read_input_registers(self, address: int, *, count: int, device_id: int) -> FakeResult:
        return await self._read("input", address, count, device_id, self.input_result)

    async def read_holding_registers(
        self, address: int, *, count: int, device_id: int
    ) -> FakeResult:
        return await self._read("holding", address, count, device_id, self.holding_result)

    async def _read(
        self,
        function: str,
        address: int,
        count: int,
        device_id: int,
        result: FakeResult | Exception,
    ) -> FakeResult:
        import asyncio

        self.calls.append((function, address, count, device_id))
        if self.delay:
            await asyncio.sleep(self.delay)
        if FakeTcp.queue:
            result = FakeTcp.queue.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def write_register(self, address: int, value: int, *, device_id: int) -> FakeResult:
        self.writes.append((address, value, device_id))
        if FakeTcp.queue:
            result = FakeTcp.queue.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return FakeResult()


@pytest.fixture(autouse=True)
def _patch_tcp(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeTcp.instances = []
    FakeTcp.next_input = FakeResult([0])
    FakeTcp.next_holding = FakeResult([0])
    FakeTcp.connect_ok = True
    FakeTcp.delay = 0.0
    FakeTcp.queue = []
    monkeypatch.setattr(
        "custom_components.growatt_solar.modbus_client.AsyncModbusTcpClient",
        FakeTcp,
    )


def test_transport_writes_holding_registers_only() -> None:
    source = (ROOT / "custom_components/growatt_solar/modbus_client.py").read_text(encoding="utf-8")
    assert "def write_register" in source
    assert "def write_registers" in source
    assert "write_coil" not in source


async def test_reads_use_the_unit_id_and_address_offset() -> None:
    FakeTcp.next_input = FakeResult([4, 5, 6])
    FakeTcp.next_holding = FakeResult([9])
    client = GrowattModbusClient("192.0.2.20", 502, 7, address_offset=1, command_interval=0)
    assert await client.read_registers("input", 35, 3) == [4, 5, 6]
    tcp = FakeTcp.instances[-1]
    assert tcp.calls == [("input", 36, 3, 7)]
    assert tcp.connect_count == 1
    assert await client.read_registers("holding", 23, 1) == [9]
    assert tcp.connect_count == 1
    assert tcp.calls[-1] == ("holding", 24, 1, 7)
    await client.close()
    assert tcp.close_count == 1
    assert tcp.connected is False


async def test_exception_response_keeps_the_connection() -> None:
    FakeTcp.next_input = FakeResult(error=True)
    client = GrowattModbusClient("192.0.2.20", 502, 1, command_interval=0)
    with pytest.raises(DeviceReadError):
        await client.read_registers("input", 0, 1)
    tcp = FakeTcp.instances[-1]
    assert tcp.connected is True
    assert tcp.close_count == 0
    await client.close()


async def test_timeout_drops_the_connection_and_the_next_read_reconnects() -> None:
    FakeTcp.delay = 1
    client = GrowattModbusClient("192.0.2.20", 502, 1, timeout=0.05, command_interval=0)
    with pytest.raises(TransportError, match="timed out"):
        await client.read_registers("input", 0, 1)
    assert FakeTcp.instances[-1].close_count == 1

    FakeTcp.delay = 0
    FakeTcp.next_input = FakeResult([8])
    assert await client.read_registers("input", 0, 1) == [8]
    assert FakeTcp.instances[-1].connect_count == 1
    await client.close()


async def test_a_single_timeout_is_retried_on_a_new_connection() -> None:
    FakeTcp.queue = [TimeoutError(), FakeResult([5])]
    client = GrowattModbusClient("192.0.2.20", 502, 1, command_interval=0)
    assert await client.read_registers("input", 0, 1) == [5]
    assert len(FakeTcp.instances) == 2
    assert FakeTcp.instances[0].close_count == 1
    await client.close()


async def test_two_timeouts_in_a_row_raise() -> None:
    FakeTcp.queue = [TimeoutError(), TimeoutError()]
    client = GrowattModbusClient("192.0.2.20", 502, 1, command_interval=0)
    with pytest.raises(TransportError, match="timed out"):
        await client.read_registers("input", 0, 1)
    assert sum(len(tcp.calls) for tcp in FakeTcp.instances) == 2


async def test_exception_response_is_not_retried() -> None:
    FakeTcp.queue = [FakeResult(error=True), FakeResult([5])]
    client = GrowattModbusClient("192.0.2.20", 502, 1, command_interval=0)
    with pytest.raises(DeviceReadError):
        await client.read_registers("input", 0, 1)
    assert len(FakeTcp.instances[-1].calls) == 1
    await client.close()


async def test_retry_waits_for_the_command_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(
        "custom_components.growatt_solar.modbus_client.asyncio.sleep",
        record_sleep,
    )
    FakeTcp.queue = [TimeoutError(), FakeResult([1])]
    client = GrowattModbusClient("192.0.2.20", 502, 1, command_interval=1)
    assert await client.read_registers("input", 0, 1) == [1]
    assert len(sleeps) == 1
    assert sleeps[0] == pytest.approx(1, abs=0.05)
    await client.close()


async def test_short_payload_is_a_device_error() -> None:
    FakeTcp.next_input = FakeResult([1])
    client = GrowattModbusClient("192.0.2.20", 502, 1, command_interval=0)
    with pytest.raises(DeviceReadError):
        await client.read_registers("input", 0, 2)
    await client.close()


async def test_commands_wait_for_the_configured_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(
        "custom_components.growatt_solar.modbus_client.asyncio.sleep",
        record_sleep,
    )
    FakeTcp.next_input = FakeResult([1])
    client = GrowattModbusClient("192.0.2.20", 502, 1, command_interval=1)
    await client.read_registers("input", 0, 1)
    await client.read_registers("input", 0, 1)
    assert sleeps[0] == pytest.approx(1, abs=0.05)
    await client.close()


async def test_a_write_uses_the_unit_id_and_retries_a_timeout() -> None:
    FakeTcp.queue = [TimeoutError(), FakeResult()]
    client = GrowattModbusClient("192.0.2.20", 502, 7, address_offset=1, command_interval=0)
    await client.write_register(1100, 0x011E)
    assert FakeTcp.instances[-1].writes == [(1101, 0x011E, 7)]
    assert len(FakeTcp.instances) == 2
    await client.close()


async def test_a_write_exception_is_not_retried() -> None:
    FakeTcp.queue = [FakeResult(error=True), FakeResult()]
    client = GrowattModbusClient("192.0.2.20", 502, 1, command_interval=0)
    with pytest.raises(DeviceReadError):
        await client.write_register(1044, 1)
    assert len(FakeTcp.instances[-1].writes) == 1
    await client.close()


async def test_connect_failure_closes_the_attempt() -> None:
    FakeTcp.connect_ok = False
    client = GrowattModbusClient("192.0.2.20", 502, 1, command_interval=0)
    with pytest.raises(TransportError, match="could not connect"):
        await client.read_registers("input", 0, 1)
    assert FakeTcp.instances[-1].close_count == 1
