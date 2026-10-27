"""The two-candidate Research Note's own sentences (spec 2026-10-27, sections 4.2 and 5.1): the first two are the period
ratio with its alias statement and the count of coincident transits. They are plain text, with no markup, so the note,
the README and the abstract print them as they are. ``pack.coincident_transits`` makes the count they report."""
from __future__ import annotations

import math

ALIAS_RATIOS = ((2.0, "2"), (3.0, "3"), (0.5, "1/2"), (1 / 3, "1/3"))      # the ratios the search treats as aliases
ALIAS_PERCENT = 2                                                          # and the tolerance it allows them

NUMBER_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")


def _distance_text(percent: float) -> str:
    """The distance at one significant figure under 10 percent, else as a whole number."""
    if percent <= 0:
        return "0"
    one = float(f"{percent:.1g}")
    if one >= 10:
        return f"{round(percent)}"
    return f"{one:.{max(0, -math.floor(math.log10(one)))}f}"


def ratio_sentence(p_short: float, p_long: float) -> str:
    """The period ratio of two candidates, the longer over the shorter at three decimals, its distance from the
    nearest of the alias ratios in percent of that ratio, and the tolerance the search used (spec 4.2)."""
    low, high = sorted((float(p_short), float(p_long)))
    ratio = high / low
    nearest, word = min(ALIAS_RATIOS, key=lambda r: abs(ratio - r[0]) / r[0])
    distance = abs(ratio - nearest) / nearest * 100
    return (f"The period ratio is {ratio:.3f}, {_distance_text(distance)} percent from {word}, outside the "
            f"{ALIAS_PERCENT} percent alias tolerance of the search (ratios of 2, 3, 1/2 and 1/3), so neither is an "
            "alias of the other.")


def _count_word(n: int) -> str:
    return NUMBER_WORDS[n] if 0 <= n < len(NUMBER_WORDS) else str(n)


def coincidence_sentence(count: int, n_long: int, p_short: str, p_long: str, note: bool) -> str | None:
    """How many of the longer candidate's predicted transits fall within a duration of a transit of the shorter one.
    In the note (``note``) the count is in words up to nine and a count of none prints nothing, since the ratio
    sentence stands alone then; the README always states it, in figures. ``p_short`` and ``p_long`` are the periods as
    printed ("15.11")."""
    if not note:
        verb = "coincides" if count == 1 else "coincide"
        return (f"{count} of {n_long} predicted {p_long} d transits {verb} with a {p_short} d transit within a "
                "duration.")
    if count <= 0:
        return None
    first = _count_word(count)
    first = first[0].upper() + first[1:]
    if count == 1:
        return (f"{first} of the {n_long} predicted {p_long} d transits falls within a duration of a {p_short} d "
                "transit.")
    return (f"{first} of the {n_long} predicted {p_long} d transits fall within a duration of a {p_short} d "
            "transit.")
