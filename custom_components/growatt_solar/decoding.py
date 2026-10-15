"""Turn Modbus register words into measurement values.

Word order and scaling come from the active register mapping. This module
does not know which inverter model it is decoding.
"""

from __future__ import annotations

from dataclasses import dataclass

from .register_map import RegisterDefinition


@dataclass(frozen=True, slots=True)
class DecodedReading:
    """A value that passed range and enumeration checks."""

    value: float | str
    raw: int | str


def decode_register(
    register: RegisterDefinition,
    words: list[int],
    group_address: int,
) -> tuple[DecodedReading | None, str | None]:
    """Decode one mapped value from a group payload.

    Returns ``(None, reason)`` when the words are present but do not represent
    a usable measurement. Callers must not replace that with zero.
    """
    start = register.address - group_address
    end = start + register.width
    if start < 0 or end > len(words):
        return None, "incomplete"

    chunk = words[start:end]
    try:
        if register.data_type == "ascii":
            text = _decode_ascii(chunk, register.word_order)
        else:
            raw = _decode_integer(chunk, register.data_type, register.word_order)
    except ValueError:
        return None, "decode"

    if register.data_type == "ascii":
        if not text:
            return None, "empty"
        return DecodedReading(text, text), None

    if register.raw_min is not None and raw < register.raw_min:
        return None, "out_of_range"
    if register.raw_max is not None and raw > register.raw_max:
        return None, "out_of_range"

    if register.value_map is not None:
        label = register.value_map.get(raw)
        if label is None:
            return None, "unlisted_value"
        return DecodedReading(label, raw), None

    scaled = round(raw * register.scale + register.offset, 6)
    return DecodedReading(scaled, raw), None


def _decode_integer(words: list[int], data_type: str, word_order: str) -> int:
    ordered = list(reversed(words)) if word_order == "little" else list(words)
    combined = 0
    for word in ordered:
        if word < 0 or word > 0xFFFF:
            raise ValueError("register word is outside uint16")
        combined = (combined << 16) | word
    if data_type.startswith("int"):
        bits = 16 * len(ordered)
        sign_bit = 1 << (bits - 1)
        if combined & sign_bit:
            combined -= 1 << bits
    return combined


def _decode_ascii(words: list[int], word_order: str) -> str:
    ordered = list(reversed(words)) if word_order == "little" else list(words)
    characters: list[str] = []
    for word in ordered:
        if word < 0 or word > 0xFFFF:
            raise ValueError("register word is outside uint16")
        characters.append(chr((word >> 8) & 0xFF))
        characters.append(chr(word & 0xFF))
    return "".join(char for char in characters if 32 <= ord(char) <= 126).strip()
