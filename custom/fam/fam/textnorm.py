"""Shared name/alias normalization helper.

Name and alias lookups across the fam glossary (people, places, seed diff)
need to treat "гуля-тате", "гуля тате" and "гуля_тате" as the same key: all
separator-ish characters are equivalent to a plain space, and comparison is
case-insensitive. Storage is untouched -- callers still store/display the
original string; only the *comparison key* goes through fold().

Cyrillic case-folding must happen in Python (str.casefold()), never via
SQLite's COLLATE NOCASE, which only folds ASCII -- see people.py/places.py
module docstrings for the same caveat.
"""

# Characters treated as separator-equivalent to a space when folding a name
# for comparison. Deliberately narrow: only characters that plausibly stand
# in for a word-separating space in a typed or pasted name. Does NOT include
# punctuation like apostrophes or periods, which carry meaning (e.g. "О'Нил").
_SEPARATOR_CHARS = {
    "-",       # hyphen-minus
    "‐",  # hyphen
    "‑",  # non-breaking hyphen
    "‒",  # figure dash
    "–",  # en dash
    "—",  # em dash
    "_",       # underscore
    " ",  # non-breaking space
    " ",  # figure space
    " ",  # narrow no-break space
}

_SEP_TABLE = {ord(c): " " for c in _SEPARATOR_CHARS}


def fold(text):
    """Return a comparison key for `text`: casefolded, with separator-ish
    characters (-, _, various dashes/nbsp) mapped to a plain space, runs of
    whitespace collapsed, and leading/trailing whitespace stripped.

    Used ONLY for comparison -- never for storage or display. Slugs
    (machine keys like 'taya', 'denis') are NOT run through this; they keep
    their own exact-match semantics.
    """
    s = str(text).casefold().translate(_SEP_TABLE)
    return " ".join(s.split())
