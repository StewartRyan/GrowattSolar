"""Decoding rules for mapped Modbus words."""

from __future__ import annotations

from custom_components.growatt_solar.decoding import decode_register
from custom_components.growatt_solar.register_map import RegisterDefinition


def _register(**overrides: object) -> RegisterDefinition:
    values: dict[str, object] = {
        "entity_key": "sample",
        "name": "Sample",
        "address": 0,
        "function": "input",
        "width": 1,
        "data_type": "uint16",
        "word_order": "big",
        "scale": 1,
        "offset": 0,
        "device_class": None,
        "state_class": None,
        "unit": None,
        "precision": None,
        "entity_category": None,
        "enabled_default": True,
        "read_group": "group",
        "documented_name": "Sample",
        "meaning": "Sample",
        "source": "test",
        "verification": "documented",
        "value_map": None,
        "raw_min": None,
        "raw_max": None,
        "notes": None,
        "identity": None,
    }
    values.update(overrides)
    return RegisterDefinition(**values)  # type: ignore[arg-type]


def test_unsigned_and_signed_boundaries() -> None:
    unsigned = _register(data_type="uint16", scale=0.1)
    assert decode_register(unsigned, [0], 0)[0].value == 0
    assert decode_register(unsigned, [65535], 0)[0].value == 6553.5

    signed = _register(data_type="int16", scale=0.1)
    assert decode_register(signed, [0xFFFF], 0)[0].value == -0.1
    assert decode_register(signed, [0x8000], 0)[0].value == -3276.8
    assert decode_register(signed, [32767], 0)[0].value == 3276.7


def test_multi_register_word_order_and_offset() -> None:
    big = _register(width=2, data_type="uint32", scale=0.1, word_order="big")
    assert decode_register(big, [0x0001, 0x86A0], 0)[0].value == 10000

    little = _register(width=2, data_type="int32", scale=1, offset=-5, word_order="little")
    # Low word 0xFFFF, high word 0xFFFF -> -1, then offset -5.
    assert decode_register(little, [0xFFFF, 0xFFFF], 0)[0].value == -6

    negative = _register(width=2, data_type="int32", scale=0.1, word_order="big")
    assert decode_register(negative, [0xFFFF, 0xFFFF], 0)[0].value == -0.1


def test_value_is_taken_from_its_place_in_the_group() -> None:
    register = _register(address=35, width=2, data_type="uint32", scale=0.1)
    reading, error = decode_register(register, [9, 9, 0x0000, 0x0064], 33)
    assert error is None
    assert reading is not None
    assert reading.value == 10
    assert reading.raw == 100


def test_incomplete_out_of_range_and_unknown_enum_are_not_zero() -> None:
    wide = _register(width=2, data_type="uint32")
    assert decode_register(wide, [1], 0) == (None, "incomplete")

    limited = _register(raw_min=0, raw_max=100, scale=1)
    assert decode_register(limited, [101], 0) == (None, "out_of_range")
    assert decode_register(limited, [0], 0)[0].value == 0

    enumerated = _register(device_class="enum", value_map={0: "waiting", 1: "normal"})
    assert decode_register(enumerated, [1], 0)[0].value == "normal"
    assert decode_register(enumerated, [2], 0) == (None, "unlisted_value")


def test_ascii_strips_nulls_and_honours_word_order() -> None:
    text = _register(width=2, data_type="ascii", scale=1)
    reading, error = decode_register(text, [0x4142, 0x4300], 0)
    assert error is None
    assert reading is not None
    assert reading.value == "ABC"

    reversed_words = _register(width=2, data_type="ascii", word_order="little", scale=1)
    assert decode_register(reversed_words, [0x4300, 0x4142], 0)[0].value == "ABC"
    assert decode_register(text, [0x0000, 0x0000], 0) == (None, "empty")
