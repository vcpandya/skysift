from .base import Source, SourceInfo, load_registry  # noqa: F401


def _parse_kbonus(data: bytes):
    from skysift.lc.kbonus import parse_kbonus_fits     # astropy loads only when a raw file is parsed
    return parse_kbonus_fits(data)


# raw-file parser per source: turns a stored raw file back into a light curve (the dashboard's loader and calibrate's
# cache hits read kept files with it instead of downloading them again)
PARSERS = {"kbonus_bkg": _parse_kbonus}
