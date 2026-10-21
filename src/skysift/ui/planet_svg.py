"""The Dossiers tile's thumbnail (dossiers spec 5.5 and 7): the star's limb, the planet and a habitable-zone bar, drawn
as a small inline SVG from a dossier's ``look`` block, with the sentence that describes it as its accessible name.

The drawing sits on its own night sky (``SPACE``) in both themes: the planet colours are physical colours, too pale
to read against the light theme's white surface, and ``tools/contrast_check.py`` pins each of them against the sky.
Pure: no store, no templates."""
from __future__ import annotations

import math
from html import escape

SPACE = "#0b1020"                   # the thumbnail's sky, the stylesheet's --space
SPACE_FG = "#e8ecf4"                # marks on the sky, the stylesheet's --space-fg
HZ_COLOUR = "#2f8f5b"
UNKNOWN_PLANET = "#a7b0c3"
# (upper bound exclusive or lower bound, colour): the star by its effective temperature, hottest first
STAR_COLOURS = ((5500, "#fff3d6"), (4500, "#ffd9a0"), (3900, "#ffbe78"), (-math.inf, "#ff9d5c"))
# the planet by its equilibrium temperature, coolest first
PLANET_COLOURS = ((200, "#cfe6ff"), (320, "#7fb7a3"), (700, "#c9a96e"), (math.inf, "#e0784a"))
STAR_WORDS = ((3900, "cool red"), (5200, "orange"), (6000, "yellow"), (math.inf, "white"))
RP_MIN, RP_MAX = 1.0, 12.0          # Earth radii: the planet disc is log-scaled between these and held outside them
R_MIN, R_MAX = 3.0, 16.0            # the planet disc's radius in the 96-unit frame


def _median(v) -> float | None:
    """A number, or the median of a {"median", "minus", "plus"} value; None when missing or not finite."""
    if isinstance(v, dict):
        v = v.get("median")
    if v is None or isinstance(v, bool):
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def star_colour(teff: float | None) -> str:
    if teff is None:
        return STAR_COLOURS[1][1]
    return next(colour for edge, colour in STAR_COLOURS if teff > edge)


def planet_colour(teq: float | None) -> str:
    if teq is None:
        return UNKNOWN_PLANET
    return next(colour for edge, colour in PLANET_COLOURS if teq < edge)


def planet_radius(rp: float | None) -> float:
    """The disc radius in the 96-unit frame: log-scaled between 1 and 12 Earth radii, held at the ends outside."""
    if rp is None or rp <= 0:
        return R_MIN
    t = (math.log(min(max(rp, RP_MIN), RP_MAX)) - math.log(RP_MIN)) / (math.log(RP_MAX) - math.log(RP_MIN))
    return round(R_MIN + t * (R_MAX - R_MIN), 2)


def _star_words(teff: float | None) -> str:
    if teff is None:
        return "A star"
    word = next(w for edge, w in STAR_WORDS if teff < edge)
    return f"{'An' if word[0] in 'aeiou' else 'A'} {word} star"


def _size_words(rp: float | None) -> str:
    if rp is None:
        return "a planet whose size is not known"
    r = round(rp, 1)
    if r == 1.0:
        return "a planet about the size of Earth"
    return f"a planet about {r:g} times the size of Earth"


def look_sentence(star: dict, planet: dict) -> str:
    """"A cool red star with a planet about 2.6 times the size of Earth, in the habitable zone". The star word comes
    from the effective temperature (``teff_k``, or ``teff`` as a value with a median), the size from ``rp_rearth`` to
    one decimal, and the zone from the two flags: the conservative Kopparapu zone reads "in the habitable zone", the
    optimistic one alone "in the optimistic habitable zone", and anything else says nothing about the zone."""
    star, planet = star or {}, planet or {}
    teff = _median(star.get("teff_k", star.get("teff")))
    rp = _median(planet.get("rp_rearth"))
    sentence = f"{_star_words(teff)} with {_size_words(rp)}"
    if planet.get("in_habitable_zone") is True:
        sentence += ", in the habitable zone"
    elif planet.get("in_optimistic_habitable_zone") is True:
        sentence += ", in the optimistic habitable zone"
    return sentence


def _hz_bar(look: dict, colour: str) -> str:
    """The habitable-zone bar: the optimistic zone faint, the conservative zone strong, the orbit as a mark; empty
    when the orbit or the zone edges are unknown."""
    a = _median(look.get("a_au"))
    inner, outer = _median(look.get("hz_inner_au")), _median(look.get("hz_outer_au"))
    o_inner, o_outer = _median(look.get("hz_optimistic_inner_au")), _median(look.get("hz_optimistic_outer_au"))
    if a is None or inner is None or outer is None or outer <= 0:
        return ""
    span = 1.25 * max(a, outer, o_outer or 0.0)
    x0, width, y = 10.0, 76.0, 82.0

    def x(d: float) -> float:
        return round(x0 + width * min(max(d, 0.0), span) / span, 2)

    track = (f'<g data-part="hz"><line x1="{x0}" y1="{y}" x2="{x0 + width}" y2="{y}" stroke="{SPACE_FG}" '
             f'stroke-opacity="0.35" stroke-width="1"/>')
    parts = [track]
    if o_inner is not None and o_outer is not None and o_outer > o_inner:
        parts.append(f'<rect x="{x(o_inner)}" y="{y - 3}" width="{round(x(o_outer) - x(o_inner), 2)}" height="6" '
                     f'rx="3" fill="{HZ_COLOUR}" fill-opacity="0.45"/>')
    parts.append(f'<rect x="{x(inner)}" y="{y - 3}" width="{round(x(outer) - x(inner), 2)}" height="6" rx="3" '
                 f'fill="{HZ_COLOUR}"/>')
    parts.append(f'<line x1="{x(a)}" y1="{y - 7}" x2="{x(a)}" y2="{y + 7}" stroke="{SPACE_FG}" stroke-width="1.5"/>'
                 f'<circle cx="{x(a)}" cy="{y}" r="2.6" fill="{colour}" stroke="{SPACE}" stroke-width="1"/></g>')
    return "".join(parts)


def planet_svg(look: dict, size: int = 96) -> str:
    """The thumbnail as one ``<svg>`` element (valid XML): ``role="img"`` with ``look_sentence`` as its
    ``aria-label`` and ``<title>``, the star's limb coloured by Teff on the left, the planet disc sized by its radius
    and coloured by Teq, and the habitable-zone bar with the orbit mark below when the zone is known."""
    look = look or {}
    teff, rp, teq = _median(look.get("teff_k")), _median(look.get("rp_rearth")), _median(look.get("teq_k"))
    sentence = look_sentence(look, look)
    s_colour, p_colour = star_colour(teff), planet_colour(teq)
    r = planet_radius(rp)
    label = escape(sentence, quote=True)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" class="planet-thumb" viewBox="0 0 96 96" width="{int(size)}" '
            f'height="{int(size)}" role="img" aria-label="{label}" focusable="false"><title>{label}</title>'
            f'<rect width="96" height="96" rx="12" fill="{SPACE}"/>'
            f'<circle cx="-14" cy="40" r="52" fill="{s_colour}" fill-opacity="0.16"/>'
            f'<circle data-part="star" cx="-14" cy="40" r="44" fill="{s_colour}"/>'
            f'<circle data-part="planet" cx="66" cy="40" r="{r}" fill="{p_colour}"/>'
            f"{_hz_bar(look, p_colour)}</svg>")
