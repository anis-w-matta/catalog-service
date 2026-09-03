import re
import unicodedata

# Centralized text normalization shared by every downstream comparison
# (extracted product text, resolver queries) so the spoken side and the
# catalogue side are normalized identically. Callers keep the original
# text alongside whatever this produces - normalize_text never replaces
# raw_text, it only derives a comparison-friendly copy.

# Matches a bare int/decimal token (e.g. "12", "0.6") - shared by every
# module that pulls "the numbers actually present in this text" out for a
# safety/agreement check.
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def normalize_text(s: str) -> str:
    """Lowercase, NFC-normalize, collapse whitespace, strip punctuation.

    Never strips digits: Arabizi letters (3=ع, 7=ح, 2=ء/ق, 5=خ, 8/6=ط,
    9=ص) and spoken quantities both depend on them.
    """
    if not s:
        return ""
    text_val = unicodedata.normalize("NFC", s).lower().strip()
    # Strip punctuation (anything that's not alphanumeric/whitespace, in a
    # Unicode-aware way so Arabic letters are never touched), then collapse
    # runs of whitespace left behind.
    text_val = "".join(ch if ch.isalnum() or ch.isspace() else " "
                       for ch in text_val)
    return re.sub(r"\s+", " ", text_val).strip()


# Distributor catalogues almost always abbreviate size in Latin letters
# (SML/MED/LRG/XLG) that a customer never actually says - "kbir"/"كبير"
# has zero character overlap with "LRG", so a raw trigram/fuzzy comparison
# has nothing to match the size word against even though a human reads it
# instantly. Sourced from abbreviations actually seen in a real catalogue
# import; extend as new conventions turn up rather than assuming this list
# is exhaustive.
SIZE_SYNONYMS: dict[str, str] = {
    "small": "SML", "sghir": "SML", "zghir": "SML", "zghire": "SML",
    "sghire": "SML", "صغير": "SML", "صغيرة": "SML", "صغار": "SML",
    "medium": "MED", "wasat": "MED", "متوسط": "MED", "وسط": "MED",
    "large": "LRG", "kbir": "LRG", "kbeer": "LRG", "kbire": "LRG",
    "kabir": "LRG", "كبير": "LRG", "كبيرة": "LRG", "كبار": "LRG",
    "extra large": "XLG", "xlarge": "XLG", "x-large": "XLG",
}

# Same idea as SIZE_SYNONYMS but for color: a spoken/Arabizi color word
# mapped to the canonical English word a catalogue item_desc is expected to
# contain, so attribute-conflict checking can compare like with like.
COLOR_SYNONYMS: dict[str, str] = {
    "red": "RED", "ahmar": "RED", "a7mar": "RED", "7amar": "RED",
    "احمر": "RED", "أحمر": "RED",
    "blue": "BLUE", "azraq": "BLUE", "azra2": "BLUE", "أزرق": "BLUE",
    "white": "WHITE", "abyad": "WHITE", "أبيض": "WHITE",
    "black": "BLACK", "aswad": "BLACK", "أسود": "BLACK",
    "green": "GREEN", "akhdar": "GREEN", "أخضر": "GREEN",
    "yellow": "YELLOW", "asfar": "YELLOW", "أصفر": "YELLOW",
}

SIZE_WORDS = {"SML", "MED", "LRG", "XLG"}
COLOR_WORDS = set(COLOR_SYNONYMS.values())


def expand_size_synonyms(q: str) -> str:
    low = q.lower()
    extra = [abbrev for word, abbrev in SIZE_SYNONYMS.items() if word in low]
    extra = list(dict.fromkeys(extra))  # dedupe, keep first-seen order
    return q if not extra else f"{q} {' '.join(extra)}"


def normalize_size(word: str | None) -> str | None:
    if not word:
        return None
    low = word.strip().lower()
    if low.upper() in SIZE_WORDS:
        return low.upper()
    return SIZE_SYNONYMS.get(low)


def normalize_color(word: str | None) -> str | None:
    if not word:
        return None
    low = word.strip().lower()
    if low.upper() in COLOR_WORDS:
        return low.upper()
    return COLOR_SYNONYMS.get(low)


# Lebanese/French business-account naming conventions seen in a real
# customer catalogue import (~40k rows) - unlike SIZE_SYNONYMS/
# COLOR_SYNONYMS (a spoken shorthand mapped to the catalogue's canonical
# form), this noise lives in the catalogue's customer_name itself, not in
# what a salesman says: "Mem."/"Emp." (Membre/Employe - an individual
# account under a company) alone prefix roughly a quarter of every
# customer in the table, and nobody ever says "Mem" out loud when naming
# a customer. "S.A.L"/"SARL" are Lebanese/French legal-entity suffixes,
# same story. "St"/"Saint" is the one real synonym pair here - collapsed
# to a single token so it stops being pure noise in the fuzzy score
# either direction. Sourced from what's actually in the data; extend as
# new conventions turn up, same as SIZE_SYNONYMS.
CUSTOMER_NOISE_PREFIXES = ("mem", "emp", "pat")
CUSTOMER_LEGAL_SUFFIXES = ("sal", "sarl")
CUSTOMER_WORD_EQUIVALENTS = {"saint": "st"}


def normalize_customer_name(name: str) -> str:
    """normalize_text() plus stripping the account-type/legal noise above
    and canonicalizing st/saint - applied to BOTH the spoken query and
    every candidate's customer_name in match_customer.py, since (unlike
    item matching) the noise this strips lives in the catalogue data
    itself, not just in spoken input."""
    norm = normalize_text(name)
    # "S.A.L"/"SARL" written with periods survive normalize_text's
    # punctuation stripping as separate single-letter tokens ("s a l") -
    # collapse them back before treating the whole thing as one suffix.
    norm = re.sub(r"\bs\s*a\s*l\b", "sal", norm)
    norm = re.sub(r"\bs\s*a\s*r\s*l\b", "sarl", norm)

    tokens = [CUSTOMER_WORD_EQUIVALENTS.get(t, t) for t in norm.split()]
    while tokens and tokens[0] in CUSTOMER_NOISE_PREFIXES:
        tokens.pop(0)
    while tokens and tokens[-1] in CUSTOMER_LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)
