"""Small text-cleaning helpers shared by every extractor."""
import re

LIGATURES = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st",
}
_LIGATURE_RE = re.compile("|".join(LIGATURES))
# Control characters, zero-width characters, byte-order marks and soft hyphens: invisible but they break matching.
_INVISIBLE_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​‌‍⁠﻿\xad]")
_SPACES_RE = re.compile(r"[ \t  -   　]+")


def clean_text(text: str) -> str:
    """Expand ligatures, drop invisible characters and collapse runs of spaces (newlines are kept)."""
    text = _LIGATURE_RE.sub(lambda m: LIGATURES[m.group()], text)
    text = _INVISIBLE_RE.sub("", text)
    return _SPACES_RE.sub(" ", text).strip()


def join_lines(lines) -> str:
    """Join the lines of one wrapped paragraph, undoing end-of-line hyphenation ("exam-" + "ple").
    A hyphen is only removed when a letter precedes it and the next line starts lowercase, so
    "state-of-the-" + "art" loses its hyphen: an accepted trade-off, far rarer than wrapped words."""
    out = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if not out:
            out = line
        elif len(out) > 1 and out.endswith("-") and out[-2].isalpha() and line[0].islower():
            out = out[:-1] + line
        else:
            out += " " + line
    return out


def word_count(text: str) -> int:
    return len(text.split())
