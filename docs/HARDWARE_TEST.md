# SPH 5000TL BL-UP hardware test

## Setup

1. Confirm the nameplate is SPH 5000TL BL-UP.
2. Connect the Modbus gateway to RS485-1, not RS485-2 and not the meter port.
3. Set the gateway to the inverter's serial settings. Protocol II V1.24 specifies 9600 baud, 8 data bits, no parity, and 1 stop bit. If holding register 22 has been changed to 1, use 38400 baud instead.
4. Give the gateway an address that Home Assistant can reach. Note its TCP port and unit id. The mapping defaults are port 502 and unit id 1.
5. Stop other Modbus pollers on that RS485 bus for the duration of the test. The inverter expects at least 850 ms between commands.

## Configure Home Assistant

1. Add the GrowattSolar integration.
2. Select SPH 5000TL BL-UP.
3. Enter the gateway host, port, and unit id. Leave the address offset at 0.
4. If the probe fails with a Modbus exception, retry once with offset −1 and once with offset 1. Record which value works.

## Compare with the LCD

Read the inverter display and the matching Home Assistant sensor at the same time. Record both numbers, the raw register value from diagnostics, and the firmware string.

| LCD item | Sensor | Expectation from the documents |
| --- | --- | --- |
| PV1 voltage | PV1 voltage | Display volts, register scale 0.1 |
| PV1 current | PV1 current | Display amps, scale 0.1 |
| PV power | PV power | Combined PV watts, scale 0.1 |
| Battery voltage | Battery voltage | Display volts, scale 0.1 |
| Battery percent | Battery state of charge | Display percent |
| Pbat positive | Battery charge power | Charge watts. Discharge sensor should be near zero |
| Pbat negative | Battery discharge power | Discharge watts. Charge sensor should be near zero |
| Pm positive | Grid import power | Power toward the user |
| Pm negative | Grid export power | Power toward the grid |
| Po in W | AC output power | Active power |
| Po in VA | AC output apparent power | Apparent power, despite the watt label in one column |
| Grid voltage and frequency | Grid voltage, grid frequency | Scale 0.1 V and 0.01 Hz |
| Epv today and total | PV1 energy today, PV2 energy today, PV energy total | Scale 0.1 kWh. There is no single "PV energy today" sensor |
| Temperature | Inverter temperature, battery temperature | Confirm whether negative values need the signed decoding |

Also confirm:

- The device serial matches the nameplate.
- A disconnected gateway makes the device unavailable, and restoring it brings the sensors back without deleting the config entry.
- Unplugging only the battery communication does not zero the PV sensors.
- No register value on the inverter changes during the test. This integration must not write.

## Record the result

Add the date, firmware, gateway model, serial settings, address offset, and each LCD comparison to the register table notes.
