from enum import Enum


class MatchMethod(str, Enum):
    exact = "exact"
    fuzzy = "fuzzy"
    substring = "substring"


class MatchStatus(str, Enum):
    matched = "matched"
    ambiguous = "ambiguous"
    not_found = "not_found"
