# SPH 5000TL BL-UP technical findings

Retrieved 11 October 2026. Official Growatt web pages did not return the protocol PDF directly; the copies below are the files that were actually downloaded and read.

## Sources

| Document | Revision | Retrieved from | Status |
| --- | --- | --- | --- |
| Growatt Inverter Modbus RTU Protocol II | V1.24, 85 pages | [photovoltaikforum attachment 525292](https://www.photovoltaikforum.com/core/attachment/525292-growatt-inverter-modbus-rtu-protocol-ii-v1-24-english-new-pdf/). A second copy was at community.symcon.de. | Primary register map. Not an official Growatt host. |
| Growatt Inverter Modbus RTU Protocol II | V1.39, dated 2024-04-16 | [Frankensolar distributor PDF](https://shop.frankensolar.ca/content/documentation/Growatt/AppNote_Growatt_WIT-Modbus-RTU-Protocol-II-V1.39-English-20240416_(frankensolar).pdf) | Cross-check of the SPH storage input block. Distributor mirror. |
| Installation Manual of SPH-UP series | SPH 3-6KTL BL-UP User Manual EN 202212 | [maxsel.de](https://www.maxsel.de/wp-content/uploads/2024/02/SPH_3-6KTL_BL-UP_User_Manual_EN_202212_1.pdf) and [midsummerwholesale.co.uk](https://midsummerwholesale.co.uk/pdfs/sph-3-6ktl-bl-up-user-manual-en-202212.pdf) | Model list, ports, and LCD names. Distributor copies of the same manual. |
| Product page | SPH 3000-6000TL BL-UP | [en.growatt.com](https://en.growatt.com/products/sph-3000-6000tl-bl-up) | Confirms the model name. The fetched page did not include a manual file URL. |
| Growatt VPP Communication Protocol of Inverter | V2.01 | [lampopumput.info attachment](https://lampopumput.info/foorumi/attachments/2-1-growatt-vpp-communication-protocol-of-inverter_v2-01-pdf.110413/) | DTC table only. Those registers are not mapped. |

The PDFs were not committed. Redistribution rights were not established.

## Connection

The SPH user manual lists RS485, USB, and CAN. It does not list an Ethernet port. Shine WiFi-X is documented as a collector for Growatt's Shine server, not as a Modbus/TCP endpoint.

Protocol II V1.24, framing section, page 8:

- Modbus RTU.
- 9600 bps default. Holding register 22: 0 = 9600, 1 = 38400.
- 1 start bit, 8 data bits, no parity, 1 stop bit, CRC.
- Unit identifier 1–254. Holding register 30 starts at 1.
- At least 850 ms between commands. The text suggests 1 second. The mapping uses 1.0 second.
- At most 125 registers in one read.
- Function 03 reads holding registers. Function 04 reads input registers.

This integration opens a Modbus/TCP (MBAP) socket to the configured host. The expected wiring is an RTU-to-TCP gateway on RS485-1. The manual's connector section and section 5.4.8 describe RS485-2 as the lithium-battery port. The meter has its own port. That port choice is from the manual, not from a wiring test.

TCP defaults in the mapping are port 502 and unit id 1. Protocol register numbers are 0-based PDU addresses, so the address offset default is 0. The flow accepts −1, 0, or 1 because some gateways present 1-based addresses.

Response timeout default is 5 seconds. `pymodbus` is not declared in `manifest.json`. Home Assistant 2026.2 constrains it (3.11.2 in that release's package constraints). The client uses `AsyncModbusTcpClient`, `device_id`, and a keyword `count`, which both 3.11 and the development environment's 3.13.1 provide.

## SPH register windows

Protocol II V1.24 assigns the SPH these windows:

- Holding 0–124 and 1000–1124.
- Input 0–124, 1000–1124, and 1125–1249.

Reads stay inside one declared group. Groups are not joined across a gap. The shipped groups are:

| Group | Function | Address | Count | Required |
| --- | --- | --- | --- | --- |
| `input_pv` | input | 0 | 11 | yes |
| `input_ac` | input | 35 | 32 | yes |
| `input_yield_temperature` | input | 91 | 17 | yes |
| `input_storage` | input | 1000 | 64 | yes |
| `input_eps` | input | 1067 | 14 | no |
| `input_bms` | input | 1095 | 2 | no |
| `input_cells` | input | 1108 | 13 | no |
| `holding_firmware` | holding | 9 | 3 | no |
| `holding_serial` | holding | 23 | 5 | no |
| `holding_priority` | holding | 1044 | 1 | no |
| `holding_grid_limits` | holding | 1070 | 2 | no |
| `holding_grid_periods` | holding | 1080 | 9 | no |
| `holding_charge_limits` | holding | 1090 | 3 | no |
| `holding_charge_periods` | holding | 1100 | 9 | no |

Input registers 0–107 are read as one block. That read, plus the battery block at input 1000, is the live poll. On 14 October 2026 this inverter answered input 0 for 108 registers in 0.18 seconds and input 1000 for 64 registers in 0.16 seconds. Input 1000 for 121 registers was not answered. Settings and the remaining input groups are read one per later poll. Home Assistant starts the next poll one second after the current one finishes. The settings groups are optional, so a rejected settings read does not drop the measurement sensors. Those reads stop before the reserved gaps at 1072–1079, 1089, and 1093–1099.

## Settings encoding

Configuration entities write a holding register, then read it back. Nothing is written during startup or polling. A schedule window is the exception: this SPH firmware acknowledges a single-register write to a start or end time and then keeps the old time. Each window is written as one block of start, end, and enable with function 16. The other two values are read immediately before that write so they are not replaced with a stale copy. The schedule editor uses the `set_window` service, which changes the start, end, and enable in that one write.

- Holding 1044 is energy priority: 0 Load first, 1 Battery first, 2 Grid first.
- A schedule time is one register. The high byte is the hour (0–23) and the low byte is the minute (0–59). Home Assistant's time control supplies the clock value; seconds are dropped.
- Window enables and the AC-charge switch are 0 or 1.
- Charge power limit (1090), stop-charge state of charge (1091), discharge power limit (1070), and stop-discharge state of charge (1071) are whole percentages from 0 to 100.
- Battery-first charge windows are holding 1100–1108, three periods of start, end, and enable. Grid-first windows are holding 1080–1088 in the same shape. Load-first holding 1110–1118 is marked SPA/reserve and is not mapped.

## Decoding

- One register is 16 bits.
- The preface says registers are unsigned. Several ranges, including string current and temperatures, are specified as negative. Those temperatures are decoded as signed 16-bit and marked `documented_provisional`.
- A 32-bit value uses the lower address as the high word (`word_order: big`).
- ASCII uses the high byte of each register first.
- Engineering value is `raw * scale + offset`.
- Documented scales used here: 0.1 V, 0.1 A, 0.1 W, 0.1 VA, 0.01 Hz, 0.1 kWh, 1 °C, 1 %. The protocol's EPS examples (5000 and 6000 for 50 and 60 Hz, 2300 for 230.0 V) support the 0.1 scale. They were not compared with an LCD.

Enum codes that are not in `value_map` become unavailable. Incomplete replies become unavailable. Zero is not used as a placeholder.

## Sensor meanings that do not copy the label

- Input 35–36 `Pac` is AC active power in watts.
- Input 40–41 is labeled in watts in one column and 0.1 VA in the unit column. The sensor is AC output apparent power, status `documented_label_conflict`.
- Input 1021–1022 `Pactouser total` is grid import. Input 1029–1030 `Pactogrid total` is grid export. The LCD name `Pm` is positive toward the user and negative toward the grid; the protocol splits those directions.
- Input 1037–1038 `PLocalLoad total` is the local load. Per-phase copies are omitted because this inverter is single-phase.
- Battery charge power and discharge power stay separate. No signed battery-power sensor is synthesized.
- Input 97, a second DSP battery voltage, is omitted as a likely duplicate of input 1013.
- Input 1082–1094 and 1097–1107 are omitted because V1.20, V1.24, and V1.39 do not describe that BMS block in the same order. Input 1095 `BMS_CycleCnt` and input 1096 `BMS_SOH` keep the same address and name in all three, so those two are mapped. The SPH rows do not print a unit; the scale of 1 is the scale V1.20 prints for the same names at input registers 3221 and 3222.
- VPP V2.01 says SPH 3000-6000TL BL device type code 3502. Other third-party pages split 3501 and 3502 between older BL and BL-UP. The code is not mapped.

## Polling and failures

One coordinator polls one inverter. A lock prevents overlapping polls, and the client has its own lock. Retries inside pymodbus are disabled so a timeout is one timeout.

A transport failure stops the cycle. A Modbus exception on one group marks that group unavailable and the cycle continues. If every required group fails, the update fails and the device becomes unavailable. Optional groups do not do that. The next interval tries again, including after the config entry has entered a retry state.

The client is closed on unload and when setup does not finish.

## What is still open

- No reading has been compared with the SPH 5000TL BL-UP display or a meter.
- Signedness of the temperature registers is an interpretation of ranges that contradict the unsigned preface.
- AC energy registers are provisional because "generate energy" is ambiguous on a hybrid.
- Firmware differences inside the BL-UP family were not tested.
- The gateway brand, baud, and address base on a particular installation are unknown.
