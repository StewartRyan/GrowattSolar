"""Config flow for a local Growatt Modbus gateway."""

from __future__ import annotations

import ipaddress
import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers import selector

from . import modbus_client
from .const import (
    CONF_ADDRESS_OFFSET,
    CONF_MODEL,
    CONF_SCAN_INTERVAL,
    CONF_UNIT_ID,
    DOMAIN,
    MAX_SCAN_INTERVAL,
)
from .decoding import decode_register
from .errors import DeviceReadError, TransportError
from .register_map import InverterModel, ModelCatalog, load_catalog

_LOGGER = logging.getLogger(__name__)
_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))*$",
    re.IGNORECASE,
)


class GrowattSolarConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up an inverter through the UI."""

    VERSION = 1
    MINOR_VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Build the options flow."""
        return GrowattOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose a discovered model and connect to its gateway."""
        catalog = await self.hass.async_add_executor_job(load_catalog)
        if not catalog.models:
            return self.async_abort(reason="no_models")
        return await self._connection_step(
            step_id="user",
            catalog=catalog,
            user_input=user_input,
            current_entry=None,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Update the gateway address or the selected mapping."""
        catalog = await self.hass.async_add_executor_job(load_catalog)
        if not catalog.models:
            return self.async_abort(reason="no_models")
        return await self._connection_step(
            step_id="reconfigure",
            catalog=catalog,
            user_input=user_input,
            current_entry=self._get_reconfigure_entry(),
        )

    async def _connection_step(
        self,
        *,
        step_id: str,
        catalog: ModelCatalog,
        user_input: dict[str, Any] | None,
        current_entry: ConfigEntry | None,
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        defaults = _defaults(catalog, current_entry)
        if user_input is not None:
            model = catalog.models.get(str(user_input.get(CONF_MODEL, "")))
            host = str(user_input.get(CONF_HOST, "")).strip()
            port = int(user_input[CONF_PORT])
            unit_id = int(user_input[CONF_UNIT_ID])
            offset = int(user_input[CONF_ADDRESS_OFFSET])
            if model is None:
                errors["base"] = "invalid_model"
            elif not valid_host(host):
                errors[CONF_HOST] = "invalid_host"
            elif _duplicate_endpoint(self, host, port, unit_id, current_entry):
                return self.async_abort(reason="already_configured")
            else:
                assert model is not None
                result = await self._try_connect(model, host, port, unit_id, offset, current_entry)
                if isinstance(result, str):
                    errors["base"] = result
                elif current_entry is not None:
                    return self.async_update_reload_and_abort(
                        current_entry,
                        data_updates=result,
                    )
                else:
                    return self.async_create_entry(
                        title=f"{model.model_name} ({host})",
                        data=result,
                        options={CONF_SCAN_INTERVAL: model.connection.default_scan_interval},
                    )

        description = (
            "The inverter is read through a Modbus/TCP gateway on its RS485 port. "
            "No Growatt cloud account is used."
        )
        if catalog.issues:
            ignored = ", ".join(sorted({issue.filename for issue in catalog.issues}))
            description += f" Ignored mapping files: {ignored}."
        return self.async_show_form(
            step_id=step_id,
            data_schema=_connection_schema(catalog, defaults),
            errors=errors,
            description_placeholders={"details": description},
        )

    async def _try_connect(
        self,
        model: InverterModel,
        host: str,
        port: int,
        unit_id: int,
        offset: int,
        current_entry: ConfigEntry | None,
    ) -> dict[str, Any] | str:
        client = modbus_client.create_client(
            host,
            port,
            unit_id,
            address_offset=offset,
            timeout=model.connection.response_timeout,
            command_interval=model.connection.minimum_command_interval,
        )
        try:
            await _probe(client, model)
            serial = await _read_identity(client, model, "serial")
        except DeviceReadError:
            _LOGGER.debug("Probe received a Modbus exception from %s", host)
            return "modbus_exception"
        except TransportError as err:
            _LOGGER.debug("Probe failed for %s: %s", host, err)
            return _transport_error(err)
        finally:
            await client.close()

        unique_id = serial if serial else f"{host}:{port}:{unit_id}"
        await self.async_set_unique_id(unique_id)
        matched = self.hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, unique_id)
        if matched is not None and (
            current_entry is None or matched.entry_id != current_entry.entry_id
        ):
            self._abort_if_unique_id_configured()
        return {
            CONF_MODEL: model.model_id,
            CONF_HOST: host,
            CONF_PORT: port,
            CONF_UNIT_ID: unit_id,
            CONF_ADDRESS_OFFSET: offset,
        }


class GrowattOptionsFlow(OptionsFlow):
    """Change the polling interval."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Edit the scan interval."""
        entry = self.config_entry
        catalog = await self.hass.async_add_executor_job(load_catalog)
        model = catalog.models.get(entry.data[CONF_MODEL]) if entry is not None else None
        floor = model.minimum_scan_interval() if model is not None else 10
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL])}
            )
        current = floor
        if entry is not None:
            current = int(entry.options.get(CONF_SCAN_INTERVAL, floor))
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=floor,
                            max=MAX_SCAN_INTERVAL,
                            mode=selector.NumberSelectorMode.BOX,
                            unit_of_measurement="s",
                        )
                    )
                }
            ),
        )


def valid_host(host: str) -> bool:
    """Accept an IP address or a normal DNS hostname."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return _HOSTNAME.match(host) is not None
    return True


def _duplicate_endpoint(
    flow: GrowattSolarConfigFlow,
    host: str,
    port: int,
    unit_id: int,
    current_entry: ConfigEntry | None,
) -> bool:
    for entry in flow._async_current_entries():
        if current_entry is not None and entry.entry_id == current_entry.entry_id:
            continue
        data = entry.data
        if (
            data.get(CONF_HOST) == host
            and data.get(CONF_PORT) == port
            and data.get(CONF_UNIT_ID) == unit_id
        ):
            return True
    return False


def _defaults(catalog: ModelCatalog, current_entry: ConfigEntry | None) -> dict[str, Any]:
    models = catalog.sorted_models()
    first = models[0]
    if current_entry is None:
        return {
            CONF_MODEL: first.model_id,
            CONF_HOST: "",
            CONF_PORT: first.connection.default_port,
            CONF_UNIT_ID: first.connection.default_unit_id,
            CONF_ADDRESS_OFFSET: 0,
        }
    data = current_entry.data
    model = catalog.models.get(data[CONF_MODEL], first)
    return {
        CONF_MODEL: model.model_id,
        CONF_HOST: data[CONF_HOST],
        CONF_PORT: data[CONF_PORT],
        CONF_UNIT_ID: data[CONF_UNIT_ID],
        CONF_ADDRESS_OFFSET: data[CONF_ADDRESS_OFFSET],
    }


def _connection_schema(catalog: ModelCatalog, defaults: dict[str, Any]) -> vol.Schema:
    options: list[selector.SelectOptionDict] = [
        {"label": model.model_name, "value": model.model_id} for model in catalog.sorted_models()
    ]
    return vol.Schema(
        {
            vol.Required(CONF_MODEL, default=defaults[CONF_MODEL]): selector.SelectSelector(
                selector.SelectSelectorConfig(options=options)
            ),
            vol.Required(CONF_HOST, default=defaults[CONF_HOST]): str,
            vol.Required(CONF_PORT, default=defaults[CONF_PORT]): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=65535,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(CONF_UNIT_ID, default=defaults[CONF_UNIT_ID]): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=254,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_ADDRESS_OFFSET, default=defaults[CONF_ADDRESS_OFFSET]
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=-1,
                    max=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
        }
    )


async def _probe(client: modbus_client.GrowattModbusClient, model: InverterModel) -> None:
    for group in model.read_groups.values():
        if group.optional:
            continue
        await client.read_registers(group.function, group.address, 1)
        return
    raise TransportError("the mapping has no required read group")


async def _read_identity(
    client: modbus_client.GrowattModbusClient,
    model: InverterModel,
    role: str,
) -> str | None:
    for register in model.registers:
        if register.identity != role:
            continue
        group = model.read_groups[register.read_group]
        try:
            words = await client.read_registers(group.function, group.address, group.count)
        except (TransportError, DeviceReadError):
            return None
        reading, _reason = decode_register(register, words, group.address)
        if reading is None or not isinstance(reading.value, str):
            return None
        return reading.value
    return None


def _transport_error(err: TransportError) -> str:
    text = str(err).lower()
    if "timed out" in text:
        return "timeout"
    if "address" in text:
        return "invalid_response"
    return "cannot_connect"
