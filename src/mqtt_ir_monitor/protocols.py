"""IR Protocol mapping definitions."""

# Map IRremote protocol IDs to human-readable names
PROTOCOL_NAMES: dict[int, str] = {
    0: "UNKNOWN",
    1: "PULSE_WIDTH",
    2: "PULSE_DISTANCE (NOISE)",
    3: "APPLE",
    4: "DENON",
    5: "JVC",
    6: "LG",
    7: "LG2",
    8: "NEC",
    9: "NEC_REPEAT",
    10: "ONKYO",
    11: "PANASONIC",
    12: "KASEIKYO",
    13: "KASEIKYO_DENON",
    14: "KASEIKYO_SHARP",
    15: "KASEIKYO_JVC",
    16: "KASEIKYO_MITSUBISHI",
    17: "RC5",
    18: "RC6",
    19: "SAMSUNG",
    20: "SAMSUNG_LG",
    21: "SAMSUNG48",
    22: "SHARP",
    23: "SONY",
}


def get_protocol_label(prot: int | None) -> str:
    """Return formatted label for protocol code, e.g. NEC (8)."""
    if prot is None:
        return "-"
    name = PROTOCOL_NAMES.get(prot, f"PROTOCOL_{prot}")
    return f"{name} ({prot})"
