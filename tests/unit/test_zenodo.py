"""The Zenodo draft deposition client (dossiers plan Task 6): a draft is created, filled and given the pack's zip, on
the sandbox by default, and never published. A fake session records every request and answers canned JSON, so no test
touches the network."""
import pytest

from skysift import zenodo
from skysift.zenodo import KBONUS_DOI, LIVE, SANDBOX, ZenodoClient, ZenodoError, metadata_from

DEPOSITION_ID = 4242


class FakeResponse:
    def __init__(self, status_code: int, payload=None, text: str = ""):
        self.status_code, self._payload, self.text = status_code, payload, text

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON")
        return self._payload


class FakeSession:
    """Records (method, url, body) for every request; ``body`` is the JSON for a POST or a metadata PUT and the bytes
    read from the file object for an upload. Answers like the Zenodo deposit API, on the host it was asked."""

    def __init__(self, status: int = 200, fail: Exception | None = None, fail_on: str | None = None):
        self.calls: list[tuple[str, str, object]] = []
        self.headers: dict[str, str] = {}
        self.status, self.fail, self.fail_on = status, fail, fail_on      # fail_on: only "/files/" requests fail

    def _host(self, url: str) -> str:
        return url.split("/api/")[0]

    def _answer(self, method: str, url: str, body):
        self.calls.append((method, url, body))
        if self.fail is not None and (self.fail_on is None or self.fail_on in url):
            raise self.fail
        if self.status >= 400 and (self.fail_on is None or self.fail_on in url):
            return FakeResponse(self.status, {"status": self.status, "message": "Internal error"})
        host = self._host(url)
        prefix = "10.5072" if "sandbox" in host else "10.5281"         # sandbox DOIs never resolve
        if method == "POST":
            return FakeResponse(201, {
                "id": DEPOSITION_ID, "submitted": False, "state": "unsubmitted",
                "links": {"self": f"{host}/api/deposit/depositions/{DEPOSITION_ID}",
                          "bucket": f"{host}/api/files/bucket-{DEPOSITION_ID}",
                          "html": f"{host}/deposit/{DEPOSITION_ID}",
                          "publish": f"{host}/api/deposit/depositions/{DEPOSITION_ID}/actions/publish"},
                "metadata": {"prereserve_doi": {"doi": f"{prefix}/zenodo.{DEPOSITION_ID}", "recid": DEPOSITION_ID}}})
        if "/files/" in url:
            return FakeResponse(201, {"key": url.rsplit("/", 1)[1], "size": len(body)})
        return FakeResponse(200, {"id": DEPOSITION_ID, "metadata": body["metadata"]})

    def post(self, url, json=None, timeout=None, **_kw):
        return self._answer("POST", url, json)

    def put(self, url, json=None, data=None, timeout=None, headers=None, **_kw):
        return self._answer("PUT", url, json if data is None else data.read())


@pytest.fixture
def fake_session():
    return FakeSession()


@pytest.fixture
def tmp_file(tmp_path):
    path = tmp_path / "kic-006146676 pack.zip"
    path.write_bytes(b"PK zip bytes")
    return path


DATA = {"target": {"name": "KIC 6146676", "gaia_id": "2101234567890123462", "ra": 285.9, "dec": 40.9},
        "summary": "A dip of 0.22 percent repeats every 40.3 days on a star of magnitude 16.2.",
        "ephemeris": {"period_days": 40.2899, "t0_bjd": 2454969.3, "depth_ppm": 2200.0, "duration_h": 2.304},
        "fap": {"words": "1 in 440", "calibrated": True, "k": 0, "n": 440},
        "fpp": {"words": "under 1 percent", "value": 2.7e-06, "signed_off": False, "label": "False positive"},
        "caveats": [{"title": "This is a candidate until independent follow-up confirms it", "text": "Follow-up."}]}


def test_zenodo_client_never_publishes(fake_session, tmp_file):
    c = ZenodoClient("tok", session=fake_session)
    d = c.create_deposition()
    c.set_metadata(d, {"title": "x"})
    c.upload(d, tmp_file)
    assert not hasattr(c, "publish") and all("publish" not in url for _m, url, _b in fake_session.calls)
    assert fake_session.calls[0] == ("POST", "https://sandbox.zenodo.org/api/deposit/depositions",
                                     {"metadata": {"prereserve_doi": True}})          # ask for a reserved DOI
    assert ZenodoClient.reserved_doi(d) == f"10.5072/zenodo.{DEPOSITION_ID}"
    assert fake_session.headers["Authorization"] == "Bearer tok"
    assert fake_session.calls[1] == ("PUT", f"https://sandbox.zenodo.org/api/deposit/depositions/{DEPOSITION_ID}",
                                     {"metadata": {"title": "x"}})
    method, url, body = fake_session.calls[2]
    assert method == "PUT" and url == f"https://sandbox.zenodo.org/api/files/bucket-{DEPOSITION_ID}/kic-006146676%20pack.zip"
    assert body == b"PK zip bytes"                                      # the file object itself, streamed
    assert c.draft_url(d) == f"https://sandbox.zenodo.org/deposit/{DEPOSITION_ID}"


def test_zenodo_live_flag_switches_the_host(fake_session):
    assert SANDBOX == "https://sandbox.zenodo.org/api" and LIVE == "https://zenodo.org/api"
    c = ZenodoClient("tok", sandbox=False, session=fake_session)
    d = c.create_deposition()
    assert fake_session.calls[0][1] == "https://zenodo.org/api/deposit/depositions" and not c.sandbox
    assert c.draft_url(d).startswith("https://zenodo.org/deposit/")


def test_zenodo_errors_are_plain(tmp_file):
    c = ZenodoClient("secret-token", session=FakeSession(status=500))
    with pytest.raises(ZenodoError, match="Zenodo answered 500") as e:
        c.create_deposition()
    assert "Internal error" in str(e.value) and "secret-token" not in str(e.value)
    down = ZenodoClient("secret-token", session=FakeSession(fail=ConnectionError("no route to host")))
    with pytest.raises(ZenodoError, match="Zenodo could not be reached") as e:
        down.create_deposition()
    assert "secret-token" not in str(e.value) and "secret-token" not in repr(down)


def test_draft_url_without_an_html_link_falls_back_to_the_deposit_page():
    c = ZenodoClient("tok", session=FakeSession())
    assert c.draft_url({"id": 7, "links": {}}) == "https://sandbox.zenodo.org/deposit/7"


def test_the_token_comes_only_from_the_environment(monkeypatch):
    monkeypatch.delenv("SKYSIFT_ZENODO_TOKEN", raising=False)
    assert zenodo.token_from_env() is None
    monkeypatch.setenv("SKYSIFT_ZENODO_TOKEN", "  ")
    assert zenodo.token_from_env() is None
    monkeypatch.setenv("SKYSIFT_ZENODO_TOKEN", "abc")
    assert zenodo.token_from_env() == "abc"
    with pytest.raises(ValueError):
        ZenodoClient("", session=FakeSession())


def test_metadata_from_citation():
    m = metadata_from(DATA, {"authors": [{"family-names": "Pandya", "given-names": "Vikram"}]})
    assert m["creators"] == [{"name": "Pandya, Vikram"}] and m["upload_type"] == "dataset"
    assert m["title"].startswith("SkySift candidate KIC 6146676: ") and "40.290 d" in m["title"]
    assert m["access_right"] == "open" and m["license"] == "cc-by-4.0" and m["prereserve_doi"] is True
    assert m["related_identifiers"] == [{"identifier": KBONUS_DOI, "relation": "isDerivedFrom", "scheme": "doi",
                                         "resource_type": "dataset"}]
    assert KBONUS_DOI == "10.17909/7jbr-w430"
    text = m["description"]
    assert DATA["summary"] in text and "1 in 440" in text and "under 1 percent" in text and "provisional" in text
    assert "This is a candidate until independent follow-up confirms it" in text
    assert "candidate" in m["keywords"][0] and "discover" not in str(m).lower() and chr(0x2014) not in str(m)


def test_metadata_without_a_citation_file_names_a_placeholder_creator():
    m = metadata_from(DATA, None)
    assert m["creators"] == [{"name": "SkySift user"}]
    m = metadata_from(DATA, {"authors": [{"name": "The SkySift team"}, {"family-names": "Pandya",
                                                                         "given-names": "Vikram",
                                                                         "orcid": "https://orcid.org/0000-0002-1825-0097"}]})
    assert m["creators"] == [{"name": "The SkySift team"}, {"name": "Pandya, Vikram", "orcid": "0000-0002-1825-0097"}]
