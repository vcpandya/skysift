"""The Zenodo draft deposition of a submission pack (dossiers spec 5.9, step 1 of the pack's README).

``ZenodoClient`` creates a deposition, fills its metadata and uploads the pack's zip through the Zenodo deposit API,
on the sandbox (``SANDBOX``, a test site whose records are not kept) unless ``sandbox=False``. It has no publish
method by design: publishing is a click the person makes on Zenodo after reading the draft, and a published record
cannot be withdrawn. The token is a personal access token read only from the environment variable
``SKYSIFT_ZENODO_TOKEN`` (``token_from_env``); it travels in the Authorization header, is never written to disk,
never put in a URL and never appears in an error message or a repr. The HTTP session is injected, so tests use a
fake one and nothing reaches the network."""
from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import quote

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


def probability_sentences(data: dict) -> tuple[str, str]:
    """The two probabilities, each in its own sentence; the false-positive one says "provisional" until its factor
    tables are signed off, and "not computed" without the Gaia neighbour list. Every pack text uses these, so the
    numbers never merge into one."""
    fap, fpp = data.get("fap") or {}, data.get("fpp") or {}
    fap_words = fap.get("words") or "not computed"
    if fap.get("calibrated") and fap.get("n") is not None:
        fap_text = (f"False-alarm probability: {fap_words} ({fap.get('k')} of {fap.get('n'):,} null searches of this "
                    "source reached the signal's strength).")
    else:
        fap_text = f"False-alarm probability: {fap_words}."
    if fpp.get("neighbours_known") is False:
        return fap_text, ("False-positive probability: not computed, since the Gaia neighbour list needed for the "
                          "background binary term is missing.")
    fpp_words = fpp.get("words") or "not computed"
    tail = (" It is provisional until its factor tables are reviewed and signed off." if not fpp.get("signed_off")
            else "")
    pixels = "" if fpp.get("pixels", True) else " (no pixel data yet)"
    fpp_text = f"False-positive probability{pixels}: {fpp_words}, from a scenario estimate.{tail}"
    return fap_text, fpp_text


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
    found = "found again in a blind sweep" if prior else "found"
    paragraphs = [data.get("summary") or "", f"Prior claim: {prior}" if prior else "",
                  f"Ephemeris: {ephemeris}." if ephemeris else "", f"{fap_text} {fpp_text}",
                  (f"This record is the submission pack of a transit candidate {found} by the SkySift engine in the "
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
