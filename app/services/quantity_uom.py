"""Trimmed copy of the backend's quantity_uom.py - only the canonical-UOM
lookup used at order-creation time (validating/normalizing an already-
resolved line's unit). The spoken-text quantity/UOM parsing machinery
(parse_quantity_uom, etc.) is intake-specific and stays in the backend."""

# The business only orders in two units: "each" (single/individual items)
# and "packets".
UOM_SYNONYMS: dict[str, str] = {
    "each": "EACH", "eaches": "EACH", "ea": "EACH",
    "packet": "PKT", "packets": "PKT", "pkt": "PKT", "pkts": "PKT",
}

_uom_value_set_cache: dict[int, set[str]] = {}


def canonical_uom(word: str | None, table: dict[str, str] = UOM_SYNONYMS
                  ) -> str | None:
    """Case-normalize `word` to its canonical unit code via `table`."""
    if not word:
        return None
    low = word.strip().lower()
    values = _uom_value_set_cache.get(id(table))
    if values is None:
        values = set(table.values())
        _uom_value_set_cache[id(table)] = values
    if low.upper() in values:
        return low.upper()
    return table.get(low)
