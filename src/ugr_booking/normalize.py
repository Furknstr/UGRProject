"""Text and value normalisation utilities.

S2: fold()
S6: parse_number(), parse_date(), parse_dims()  ← added later
"""

import re
import unicodedata


# ---------------------------------------------------------------------------
# fold() — accent/case normalisation for matching
# ---------------------------------------------------------------------------

def fold(s: str | None) -> str:
    """Normalise a string for comparison:

    1. NFKD decomposition
    2. Strip combining (accent) marks
    3. Map dotless-i (ı → i) and dotted-I (İ → i via casefold)
    4. casefold  (handles İ→i correctly)
    5. Collapse whitespace

    Examples:
        fold("İZMİR")  == "izmir"
        fold("SÃGUNTO") == "sagunto"
        fold("JEDAH")   == "jedah"
    """
    if s is None:
        return ""
    # NFKD + strip combining marks
    nfkd = unicodedata.normalize("NFKD", s)
    no_marks = "".join(
        ch for ch in nfkd
        if unicodedata.category(ch) != "Mn"
    )
    # ı (U+0131, LATIN SMALL LETTER DOTLESS I) → i
    no_marks = no_marks.replace("\u0131", "i")
    # casefold handles İ (U+0130) after NFKD decomposition
    result = no_marks.casefold()
    # collapse whitespace
    return re.sub(r"\s+", " ", result).strip()
