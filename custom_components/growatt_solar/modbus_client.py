"""Modbus/TCP client for a Growatt RS485 gateway.

The inverter speaks Modbus RTU. This client speaks Modbus TCP and expects a
gateway to bridge the two. Writes are single holding registers, used only for
mapped settings.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from typing import Any

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ConnectionException, ModbusIOException

from .errors import DeviceReadError, TransportError

_LOGGER = logging.getLogger(__name__)


class GrowattModbusClient:
    """One TCP session, with reads serialized and spaced apart."""

    def __init__(
        self,
        host: str,
        port: int,
        unit_id: int,
        *,
        address_offset: int = 0,
        timeout: float = 5.0,
        command_interval: float = 1.0,
    ) -> None:
        self._host = host
        self._port = port
        self._unit_id = unit_id
        self._address_offset = address_offset
        self._timeout = timeout
        self._command_interval = command_interval
        self._client: AsyncModbusTcpClient | None = None
        self._lock = asyncio.Lock()
        self._last_command_at: float | None = None

    async def read_registers(self, function: str, address: int, count: int) -> list[int]:
        """Read a contiguous input or holding block."""
        if function not in {"input", "holding"}:
            raise TransportError("unsupported Modbus function")
        if count < 1 or count > 125:
            raise TransportError("register count is outside 1..125")
        pdu_address = address + self._address_offset
        if pdu_address < 0 or pdu_address + count > 65536:
            raise TransportError("Modbus address is outside the legal range")

        async with self._lock:
            try:
                result = await self._read_once(function, pdu_address, count)
            except TransportError as err:
                _LOGGER.debug("Retrying %s @%s after: %s", function, pdu_address, err)
                result = await self._read_once(function, pdu_address, count)

        if getattr(result, "isError", lambda: True)():
            _LOGGER.debug("Modbus exception for %s @%s", function, pdu_address)
            raise DeviceReadError("the inverter returned a Modbus exception")
        registers = getattr(result, "registers", None)
        if (
            not isinstance(registers, list)
            or len(registers) != count
            or any(
                not isinstance(word, int) or isinstance(word, bool) or not 0 <= word <= 0xFFFF
                for word in registers
            )
        ):
            raise DeviceReadError("the inverter returned an incomplete register block")
        return registers

    async def write_register(self, address: int, value: int) -> None:
        """Write one holding register and wait for the inverter to accept it."""
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFF:
            raise TransportError("register value is outside 0..65535")
        pdu_address = address + self._address_offset
        if not 0 <= pdu_address <= 65535:
            raise TransportError("Modbus address is outside the legal range")

        async with self._lock:
            try:
                result = await self._write_once(pdu_address, value)
            except TransportError as err:
                _LOGGER.debug("Retrying write @%s after: %s", pdu_address, err)
                result = await self._write_once(pdu_address, value)

        if getattr(result, "isError", lambda: True)():
            _LOGGER.debug("Modbus exception for write @%s", pdu_address)
            raise DeviceReadError("the inverter returned a Modbus exception")

    async def write_registers(self, address: int, values: list[int]) -> None:
        """Write a contiguous holding block with function 16."""
        if not values or len(values) > 125:
            raise TransportError("register count is outside 1..125")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFF
            for value in values
        ):
            raise TransportError("register value is outside 0..65535")
        pdu_address = address + self._address_offset
        if pdu_address < 0 or pdu_address + len(values) > 65536:
            raise TransportError("Modbus address is outside the legal range")

        async with self._lock:
            try:
                result = await self._write_many_once(pdu_address, values)
            except TransportError as err:
                _LOGGER.debug("Retrying block write @%s after: %s", pdu_address, err)
                result = await self._write_many_once(pdu_address, values)

        if getattr(result, "isError", lambda: True)():
            _LOGGER.debug("Modbus exception for block write @%s", pdu_address)
            raise DeviceReadError("the inverter returned a Modbus exception")

    async def _write_many_once(self, pdu_address: int, values: list[int]) -> Any:
        await self._pause_for_bus()
        try:
            await self._ensure_connected()
        except TransportError:
            self._mark_command()
            raise
        client = self._client
        if client is None:
            raise TransportError("could not connect to the inverter")
        try:
            result = await asyncio.wait_for(
                client.write_registers(pdu_address, values, device_id=self._unit_id),
                timeout=self._timeout,
            )
        except TimeoutError as err:
            await self._discard()
            self._mark_command()
            raise TransportError("timed out waiting for the inverter") from err
        except (ConnectionException, ModbusIOException, OSError) as err:
            await self._discard()
            self._mark_command()
            _LOGGER.debug("Modbus connection failed: %s", type(err).__name__)
            raise TransportError("inverter connection failed") from err
        self._mark_command()
        return result

    async def _write_once(self, pdu_address: int, value: int) -> Any:
        await self._pause_for_bus()
        try:
            await self._ensure_connected()
        except TransportError:
            self._mark_command()
            raise
        client = self._client
        if client is None:
            raise TransportError("could not connect to the inverter")
        try:
            result = await asyncio.wait_for(
                client.write_register(pdu_address, value, device_id=self._unit_id),
                timeout=self._timeout,
            )
        except TimeoutError as err:
            await self._discard()
            self._mark_command()
            raise TransportError("timed out waiting for the inverter") from err
        except (ConnectionException, ModbusIOException, OSError) as err:
            await self._discard()
            self._mark_command()
            _LOGGER.debug("Modbus connection failed: %s", type(err).__name__)
            raise TransportError("inverter connection failed") from err
        self._mark_command()
        return result

    async def _read_once(self, function: str, pdu_address: int, count: int) -> Any:
        await self._pause_for_bus()
        try:
            await self._ensure_connected()
        except TransportError:
            self._mark_command()
            raise
        client = self._client
        if client is None:
            raise TransportError("could not connect to the inverter")
        reader = (
            client.read_input_registers if function == "input" else client.read_holding_registers
        )
        try:
            result = await asyncio.wait_for(
                reader(pdu_address, count=count, device_id=self._unit_id),
                timeout=self._timeout,
            )
        except TimeoutError as err:
            await self._discard()
            self._mark_command()
            raise TransportError("timed out waiting for the inverter") from err
        except (ConnectionException, ModbusIOException, OSError) as err:
            await self._discard()
            self._mark_command()
            _LOGGER.debug("Modbus connection failed: %s", type(err).__name__)
            raise TransportError("inverter connection failed") from err
        self._mark_command()
        return result

    async def close(self) -> None:
        """Close the socket if one is open."""
        async with self._lock:
            await self._discard()

    async def _ensure_connected(self) -> None:
        if self._client is not None and bool(getattr(self._client, "connected", False)):
            return
        await self._discard()
        client = AsyncModbusTcpClient(
            self._host,
            port=self._port,
            timeout=self._timeout,
            retries=0,
        )
        try:
            connected = await asyncio.wait_for(client.connect(), timeout=self._timeout)
        except TimeoutError as err:
            await _close_client(client)
            raise TransportError("timed out connecting to the inverter") from err
        except OSError as err:
            await _close_client(client)
            _LOGGER.debug("TCP connect failed: %s", type(err).__name__)
            raise TransportError("could not connect to the inverter") from err
        if not connected:
            await _close_client(client)
            raise TransportError("could not connect to the inverter")
        self._client = client

    async def _discard(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await _close_client(client)

    async def _pause_for_bus(self) -> None:
        if self._last_command_at is None or self._command_interval <= 0:
            return
        remaining = self._command_interval - (time.monotonic() - self._last_command_at)
        if remaining > 0:
            await asyncio.sleep(remaining)

    def _mark_command(self) -> None:
        self._last_command_at = time.monotonic()


def create_client(
    host: str,
    port: int,
    unit_id: int,
    *,
    address_offset: int = 0,
    timeout: float = 5.0,
    command_interval: float = 1.0,
) -> GrowattModbusClient:
    """Build the production client."""
    return GrowattModbusClient(
        host,
        port,
        unit_id,
        address_offset=address_offset,
        timeout=timeout,
        command_interval=command_interval,
    )


async def _close_client(client: Any) -> None:
    try:
        result = client.close()
    except Exception:  # noqa: BLE001 - shutdown must not raise a second error
        _LOGGER.debug("Closing the Modbus client failed", exc_info=True)
        return
    if inspect.isawaitable(result):
        await result
