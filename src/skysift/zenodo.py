"""The Zenodo draft deposition of a submission pack (dossiers spec 5.9, step 1 of the pack's README).

``ZenodoClient`` creates a deposition, fills its metadata and uploads the pack's zip through the Zenodo deposit API,
on the sandbox (``SANDBOX``, a test site whose records are not kept) unless ``sandbox=False``. It has no publish
method by design: publishing is a click the person makes on Zenodo after reading the draft, and a published record
cannot be withdrawn. The token is a personal access token read only from the environment variable
``SKYSIFT_ZENODO_TOKEN`` (``token_from_env``); it travels in the Authorization header, is never written to disk,
never put in a URL and never appears in an error message or a repr. The HTTP session is injected, so tests use a
fake one and nothing reaches the network."""
from __future__ import annotations

import math
import os
import re
from pathlib import Path
from urllib.parse import quote

from skysift import crosscheck as cc
from skysift.pack_text import ephemeris_sentence

SANDBOX = "https://sandbox.zenodo.org/api"
LIVE = "https://zenodo.org/api"
TOKEN_ENV = "SKYSIFT_ZENODO_TOKEN"
KBONUS_DOI = "10.17909/7jbr-w430"         # the KBonus Kepler Background HLSP at MAST, the light curves' source
PLACEHOLDER_CREATOR = "SkySift user"
KEYWORDS = ("exoplanet candidate", "transit photometry", "Kepler", "KBonus Kepler Background", "SkySift")


class ZenodoError(RuntimeError):
    """Zenodo refused a request or could not be reached; the message says which, without the token."""


def token_from_env() -> str | None:
    """The personal access token from ``SKYSIFT_ZENODO_TOKEN``, or None when it is unset or blank."""
    token = (os.environ.get(TOKEN_ENV) or "").strip()
    return token or None


def new_session():
    """A requests session with the system trust store (``skysift.net`` injects it, for TLS behind an inspecting
    proxy). The CLI asks this for its session; tests replace it."""
    import requests

    from skysift import net  # noqa: F401  (the trust-store injection happens on import)
    return requests.Session()


class ZenodoClient:
    def __init__(self, token: str, *, sandbox: bool = True, session=None, timeout: float = 120.0):
        if not token:
            raise ValueError(f"a Zenodo token is needed; set {TOKEN_ENV}")
        self.sandbox = sandbox
        self.base = SANDBOX if sandbox else LIVE
        self.timeout = timeout
        self.session = session if session is not None else new_session()
        self.session.headers["Authorization"] = f"Bearer {token}"

    def __repr__(self) -> str:
        return f"ZenodoClient(base={self.base!r})"

    def _call(self, method: str, url: str, **kw) -> dict:
        try:
            r = getattr(self.session, method.lower())(url, timeout=self.timeout, **kw)
        except Exception as e:  # noqa: BLE001  (connection errors, timeouts: the session's own types)
            raise ZenodoError(f"Zenodo could not be reached ({method} {url}): {type(e).__name__}: {e}") from None
        if r.status_code >= 400:
            raise ZenodoError(f"Zenodo answered {r.status_code} to {method} {url}: {_message(r)}")
        try:
            return r.json()
        except ValueError:
            return {}

    def create_deposition(self) -> dict:
        """A new draft deposition that asks Zenodo to reserve a DOI; its JSON carries ``id``, ``links`` and the
        reserved DOI under ``metadata.prereserve_doi``."""
        return self._call("POST", f"{self.base}/deposit/depositions", json={"metadata": {"prereserve_doi": True}})

    def set_metadata(self, deposition: dict, metadata: dict) -> dict:
        return self._call("PUT", f"{self.base}/deposit/depositions/{deposition['id']}", json={"metadata": metadata})

    def upload(self, deposition: dict, path: Path) -> dict:
        """Stream ``path`` into the deposition's file bucket under its own name."""
        path = Path(path)
        bucket = (deposition.get("links") or {}).get("bucket")
        if not bucket:
            raise ZenodoError(f"Zenodo gave deposition {deposition.get('id')} no file bucket")
        with path.open("rb") as fh:
            return self._call("PUT", f"{bucket}/{quote(path.name)}", data=fh,
                              headers={"Content-Type": "application/octet-stream"})

    def draft_url(self, deposition: dict) -> str:
        """The page where the person reads the draft and publishes it."""
        html = (deposition.get("links") or {}).get("html")
        return html or f"{self.base.removesuffix('/api')}/deposit/{deposition['id']}"

    @staticmethod
    def reserved_doi(deposition: dict) -> str | None:
        return ((deposition.get("metadata") or {}).get("prereserve_doi") or {}).get("doi")


def _message(r) -> str:
    try:
        body = r.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        parts = [str(body.get("message") or "")]
        parts += [f"{e.get('field')}: {e.get('message')}" for e in body.get("errors") or [] if isinstance(e, dict)]
        text = "; ".join(p for p in parts if p)
        if text:
            return text
    return (getattr(r, "text", "") or "no message")[:300]


def _creator(author: dict) -> dict | None:
    if author.get("family-names"):
        name = author["family-names"]
        if author.get("given-names"):
            name = f"{name}, {author['given-names']}"
    elif author.get("name"):
        name = author["name"]
    else:
        return None
    out = {"name": str(name)}
    orcid = re.search(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]", str(author.get("orcid") or ""))
    if orcid:
        out["orcid"] = orcid.group(0)
    if author.get("affiliation"):
        out["affiliation"] = str(author["affiliation"])
    return out


# spec 11.4: what the false-alarm probability was measured against, and what it means across the whole sweep
FAP_NULLS = ("{k} of {n:,} null searches (the inverted and scrambled light curves of {stars:,} other KBonus stars, "
             "searched without the masked pass and alias resolution) reached the signal's SDE")
FAP_SWEEP = ("; across the {s:,}-star sweep {expected} expected by chance, and the vetting ladder, not the SDE, "
             "separates the candidate from them")
FAP_NO_SWEEP = "; the vetting ladder, not the SDE, separates the candidate from such peaks"
# spec 11.2: the in-house estimate follows the TRICERATOPS one, named for the scenarios it leaves out
IN_HOUSE = "a four-scenario estimate without a bound-companion or diluted-planet scenario gives {value}, a lower bound"
IN_HOUSE_NO_NEIGHBOURS = ("the four-scenario estimate is not computed, since the Gaia neighbour list needed for the "
                          "background binary term is missing")
PROVISIONAL = ", provisional until its factor tables are reviewed"


def _expected(x: float) -> str:
    """How many peaks the sweep should hold by chance, as ``FAP_SWEEP`` prints it: "about 31 such peaks are",
    "about one such peak is", "fewer than one such peak is" below 0.5."""
    n = round(x)
    if n < 1:
        return "fewer than one such peak is"
    return "about one such peak is" if n == 1 else f"about {n:,} such peaks are"


def fap_sentence(data: dict) -> str:
    """"False-alarm probability: <words>." and, when calibrated, ``FAP_NULLS`` with ``FAP_SWEEP`` (the stored
    ``calibration.sweep_size`` times the FAP) or ``FAP_NO_SWEEP`` without a sweep size. A dossier stored before the
    calibration block says "other KBonus stars" without their count."""
    fap, calib = data.get("fap") or {}, data.get("calibration") or {}
    words = fap.get("words") or "not computed"
    if not (fap.get("calibrated") and fap.get("n") is not None):
        return f"False-alarm probability: {words}."
    stars = calib.get("null_stars")
    nulls = (FAP_NULLS.format(k=fap.get("k"), n=fap["n"], stars=stars) if isinstance(stars, int)
             else FAP_NULLS.replace("{stars:,} ", "").format(k=fap.get("k"), n=fap["n"]))
    size, value = calib.get("sweep_size"), fap.get("fap")
    if isinstance(size, int) and size > 0 and isinstance(value, (int, float)) and math.isfinite(value):
        sweep = FAP_SWEEP.format(s=size, expected=_expected(size * value))
    else:
        sweep = FAP_NO_SWEEP
    return f"False-alarm probability: {words}. {nulls}{sweep}."


def in_house_text(data: dict) -> str:
    """The in-house false-positive estimate as the texts print it after the TRICERATOPS one (spec 11.2): ``IN_HOUSE``
    with the value in prose (``crosscheck.probability_text``) and, until its factor tables are signed off, the
    provisional clause; "not computed" wording without the Gaia neighbour list or without a value."""
    fpp = data.get("fpp") or {}
    if fpp.get("neighbours_known") is False:
        return IN_HOUSE_NO_NEIGHBOURS
    value = fpp.get("value")
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    text = IN_HOUSE.format(value=cc.probability_text(value) if ok else "no value (not computed)")
    return text + ("" if fpp.get("signed_off") else PROVISIONAL)


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def probability_sentences(data: dict) -> tuple[str, str]:
    """The two probabilities, each in its own sentence (``fap_sentence``; the false-positive one leads with the
    TRICERATOPS headline, ``crosscheck.headline``, when a current cross-check exists, then ``in_house_text``). The
    in-house estimate says "provisional" until its factor tables are signed off, and "not computed" without the Gaia
    neighbour list. Every pack text uses these, so the numbers never merge into one."""
    fpp = data.get("fpp") or {}
    fap_text = fap_sentence(data)
    pixels = "" if fpp.get("pixels", True) else " (no pixel data yet)"
    head = cc.headline(data)
    if head is not None:
        return fap_text, f"False-positive probability{pixels}: {head}; {in_house_text(data)}."
    if fpp.get("neighbours_known") is False:
        return fap_text, ("False-positive probability: not computed, since the Gaia neighbour list needed for the "
                          "background binary term is missing.")
    return fap_text, f"False-positive probability{pixels}: {_capital(in_house_text(data))}."


def metadata_from(data: dict, citation: dict | None, prior: str | None = None) -> dict:
    """The deposition metadata from a dossier's JSON and the parsed ``CITATION.cff`` (None when there is none):
    a dataset titled "SkySift candidate <name>: ...", creators from the citation's authors, the summary, the prior
    claim when there is one (``prior``: the README's sentence, ``pack.prior_claim_sentence``, with the claim's DOI and
    date), the ephemeris with its errors (``pack_text.ephemeris_sentence``, the sentence the ExoFOP note and the archive
    email quote), both probabilities and the caveats as the description, keywords, open access under CC BY 4.0, and
    the KBonus HLSP DOI as the data it is derived from."""
    target, eph = data.get("target") or {}, data.get("ephemeris") or {}
    name = target.get("name") or target.get("target_id") or "unknown target"
    period = eph.get("period_days")
    what = f"a {period:.3f} d transit signal" if period else "a transit signal"
    what += " in Kepler photometry"
    creators = [c for a in (citation or {}).get("authors") or [] if isinstance(a, dict) and (c := _creator(a))]
    fap_text, fpp_text = probability_sentences(data)
    ephemeris = ephemeris_sentence(data)
    paragraphs = [data.get("summary") or "", f"Prior claim: {prior}" if prior else "",
                  f"Ephemeris: {ephemeris}." if ephemeris else "", f"{fap_text} {fpp_text}",
                  ("This record is the submission pack of a transit candidate found by the SkySift engine in the "
                   "KBonus Kepler Background light curves: the detrended light curve, the ephemeris and parameters "
                   "with their errors, the per-event depths, the vetting metrics, the figures, the standalone "
                   "dossier, a Research Note draft and the provenance of every query.")]
    caveats = [c.get("title") for c in data.get("caveats") or [] if isinstance(c, dict) and c.get("title")]
    if caveats:
        paragraphs.append("What is not yet known: " + "; ".join(caveats) + ".")
    description = "".join(f"<p>{_html(p)}</p>" for p in paragraphs if p)
    return {
        "upload_type": "dataset",
        "title": f"SkySift candidate {name}: {what}",
        "creators": creators or [{"name": PLACEHOLDER_CREATOR}],
        "description": description,
        "keywords": [*KEYWORDS, str(name)],
        "access_right": "open",
        "license": "cc-by-4.0",
        "related_identifiers": [{"identifier": KBONUS_DOI, "relation": "isDerivedFrom", "scheme": "doi",
                                 "resource_type": "dataset"}],
        "prereserve_doi": True,
    }


def _html(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
