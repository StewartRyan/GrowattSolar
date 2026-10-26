# GrowattSolar

A Home Assistant custom integration that reads a compatible Growatt hybrid inverter over the local network. It does not use a Growatt cloud account.

The first mapping is the **SPH 5000TL BL-UP**. That inverter speaks Modbus RTU on RS485. This integration speaks Modbus/TCP, so the inverter is reached through an RTU-to-TCP gateway. Register behavior comes from official protocol documents.

A model is supported only to the extent of its mapping file. Each file records the register address, decoding, Home Assistant metadata, documentation source, and a verification status. Adding a valid file makes that model selectable without changing Python.

## Features

- UI configuration: model, gateway host, TCP port, unit identifier, and address offset.
- Model list built from the JSON files under `custom_components/growatt_solar/registers/`.
- Polling through one coordinator per inverter.
- Configuration entities for energy priority and the charge and grid-first schedules. A schedule time uses Home Assistant's clock control. The integration also serves the schedule card used to edit those windows on a dashboard.
- Adjacent registers read as one contiguous group. Groups are not merged across gaps.
- An optional group can fail while the other readings stay available.
- Diagnostics for mapping problems, group errors, and decoding errors. The serial number is redacted.

## Supported hardware

| Mapping | Model | Verification |
| --- | --- | --- |
| `sph5000_tl_bl_up.json` | SPH 5000TL BL-UP | 70 measurement registers from Protocol II and the 202212 user manual, plus schedule and priority settings. See [the register table](docs/sph5000-register-table.md). |

The manual lists the SPH 3000, 3600, 4000, 4600, 5000, and 6000 TL BL-UP as one family. Only the 5000 W model is mapped. Do not select this file for another rating until its registers have been checked.

## Requirements

- Home Assistant 2026.2 or newer.
- The inverter's RS485 port connected to a Modbus RTU-to-TCP gateway that Home Assistant can reach.
- Gateway defaults used by the SPH mapping: TCP port **502**, unit identifier **1**, address offset **0**.
- Serial settings from Protocol II V1.24: **9600 baud, 8 data bits, no parity, 1 stop bit**. Holding register 22 can select 38400 baud. Confirm the gateway matches the inverter.
- At least one second between Modbus commands. The mapping enforces that gap. Live measurements are two reads, and the next poll starts one second after those finish. Settings are read one group at a time on later polls.

RTU-over-TCP serial sticks are not supported. Shine WiFi-X is a Shine-server collector, not a Modbus/TCP gateway. Use RS485-1 for the gateway. The manual assigns RS485-2 to lithium-battery communication.

## Installation

### HACS

1. In HACS, open **Integrations** and search for **GrowattSolar**.
2. Install it and restart Home Assistant.

If the search does not list it yet, add the repository by hand:

1. Open **Custom repositories**.
2. Add `https://github.com/StewartRyan/GrowattSolar` with category **Integration**.
3. Install **GrowattSolar** and restart Home Assistant.

### Manual

Copy `custom_components/growatt_solar` into the Home Assistant `custom_components` directory and restart.

## Configuration

1. Go to **Settings → Devices & services → Add integration**.
2. Choose **GrowattSolar**.
3. Select the inverter model. The list is the valid mapping files found at startup.
4. Enter the gateway IP address or hostname, port, unit identifier, and address offset.
5. Submit. The flow reads one register from the first required group. Setup completes only when that read succeeds.

A mapping file added later is discovered when that config entry is reloaded or Home Assistant restarts. Reload the entry after replacing an existing file so the running sensors match the new map.

The options flow changes only the poll interval. Reconfigure the entry to change the host, port, unit identifier, offset, or model.

Duplicate prevention uses the serial number when the mapping defines one and the read succeeds. Otherwise it uses host, port, and unit identifier.

## Sensors

Each register becomes one sensor. The entity name is the mapping's `name`, not the official register label. `documented_name` keeps Growatt's wording so the two can be compared.

Power uses watts and `measurement`. Energy uses kilowatt-hours and `total` or `total_increasing`. Battery charge power and discharge power stay separate unsigned magnitudes. The LCD shows a signed battery power; the protocol does not expose that signed value as one register, so this integration does not invent one.

Unknown enum codes are unavailable. A failed read is unavailable. The integration does not substitute zero.

Disabled diagnostic entities include the eight fault words and the parallel-battery state of charge. Enable them from the entity registry if you need them.

## Register provenance

Technical findings, including connection parameters and conflicts between documents, are in [docs/TECHNICAL_FINDINGS.md](docs/TECHNICAL_FINDINGS.md). The SPH table is [docs/sph5000-register-table.md](docs/sph5000-register-table.md).

Verification statuses:

| Status | Meaning |
| --- | --- |
| `documented` | Address, width, scale, and meaning come from one official source. |
| `documented_crosscheck` | A second official or distributor copy of the protocol agrees. |
| `documented_label_conflict` | The label and the unit column disagree. The sensor follows the unit column and says so. |
| `documented_provisional` | The document is incomplete, for example a signed temperature stored in a register the preface calls unsigned. |

The PDFs were read on 11 October 2026 and are not stored in this repository. Redistribution rights were not established. URLs are in the mapping file and the findings document.

## Known limitations

- The native port is RS485. A gateway is required, and gateway address offsets differ. Offset `-1`, `0`, or `1` can be set in the flow. The default `0` matches Protocol II's 0-based register numbers.
- Most of input registers 1082–1107 are omitted. Protocol II V1.20, V1.24, and V1.39 agree on cycle count at 1095 and state of health at 1096, and those two are mapped. The registers around them are not, because the revisions do not describe that block in the same order.
- Three-phase register columns are omitted. This model is single-phase.
- AC generation energy is marked provisional because "generate energy" on a hybrid inverter can mean PV, AC output, or both.
- Settings writes are limited to the mapped configuration entities: energy priority, three charge windows, three grid-first windows, charging from the grid, and the charge and discharge limits. Changing a priority, limit, or grid-charge switch writes that one holding register. Changing a schedule window writes its start, end, and enable together, because this inverter drops a single time write. Each change is read back. Polling does not write. Load-first time windows are left out because the protocol marks them reserved on this model.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Cannot connect | Gateway IP, port 502, unit id 1, and that the serial line is 9600 8N1. RS485-2 is the battery port. |
| Timeout | The inverter needs about one second between commands. A second poller on the same RS485 bus can starve this one. |
| Modbus exception | Wrong unit id, or a gateway that uses 1-based addresses. Try address offset `-1` or `1`. |
| One sensor unavailable | Diagnostics show the group or register error. Other groups are kept. |
| Ignored mapping file | The setup description names the file. Fix the JSON; the other models stay available. |
| New model missing | Reload the config entry or restart Home Assistant after adding the file. |

Download diagnostics from the device page. The host address is omitted and the serial number is redacted.

## Add a model

Create `custom_components/growatt_solar/registers/<model_id>.json`. The filename stem must equal `model_id`. No Python change is required. After a reload, the model appears in the selector, its registers are read in the declared groups, and its sensors use that file's names, units, and scaling.

Minimal valid file:

```json
{
  "model_id": "example_hybrid",
  "model_name": "Example Hybrid",
  "manufacturer": "Growatt",
  "family": "Example",
  "variants": ["Example Hybrid"],
  "documentation": [
    {
      "title": "Growatt Inverter Modbus RTU Protocol II",
      "revision": "V1.24",
      "url": "https://www.photovoltaikforum.com/core/attachment/525292-growatt-inverter-modbus-rtu-protocol-ii-v1-24-english-new-pdf/",
      "retrieved": "2026-10-11",
      "role": "Example only"
    }
  ],
  "connection": {
    "transport": "modbus_tcp",
    "default_port": 502,
    "default_unit_id": 1,
    "default_scan_interval": 30,
    "minimum_command_interval": 1.0,
    "response_timeout": 5.0,
    "notes": "RS485 via a Modbus/TCP gateway."
  },
  "read_groups": [
    {"id": "input_status", "function": "input", "address": 0, "count": 1, "optional": false}
  ],
  "registers": [
    {
      "entity_key": "inverter_run_state",
      "name": "Inverter run state",
      "address": 0,
      "function": "input",
      "read_group": "input_status",
      "documented_name": "Inverter Status",
      "meaning": "Coarse run state. Confirm the codes against the protocol page before trusting the labels.",
      "source": "Protocol II V1.24, input register 0",
      "verification": "documented",
      "data_type": "uint16",
      "width": 1,
      "scale": 1,
      "device_class": "enum",
      "value_map": {"0": "waiting", "1": "normal", "3": "fault"}
    }
  ]
}
```

Validate it:

```bash
python -c "from custom_components.growatt_solar.register_map import load_catalog; print(load_catalog().issues)"
pytest
```

A malformed file is skipped. The error includes the filename. Valid files in the same directory still load.

### How to decide what a register means

1. Read the official description, unit column, and scale, and keep the page reference in `source`.
2. Check width, signedness, and whether the value spans two registers. Multi-register values in Protocol II are high word first.
3. Compare related registers. Per-phase totals on a single-phase SPH often repeat the same quantity.
4. Compare a live reading with the inverter LCD or a meter. Until that happens, do not mark the register as more than documented.
5. If the label and the unit disagree, name the sensor for the quantity you believe is measured and set `verification` to `documented_label_conflict`.
6. If the physical meaning is still unclear, use a conservative name and `documented_provisional`.

Then set `device_class`, `state_class`, `unit`, and `precision` from that interpretation. Power is not energy. Do not copy a misleading label into `name`.

## Development

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/ruff format --check custom_components tests
.venv/bin/ruff check custom_components tests
.venv/bin/mypy
.venv/bin/pytest
```

The hardware procedure is [docs/HARDWARE_TEST.md](docs/HARDWARE_TEST.md). Automated tests use a scripted Modbus client and do not need an inverter or a network.

## Contributing

Add or correct a mapping only with a traceable source. Keep model behavior in JSON. If a model needs a Python branch, extend the schema instead of special-casing the model id. Do not add write operations without a separate review.

## License

Apache 2.0. See [LICENSE](LICENSE). Register addresses and scales are facts taken from Growatt documents. The mapping text and the integration code are original. The official PDFs are not included.
