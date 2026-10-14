import pytest

from skysift.net import ServiceUnavailable, fetch, looks_like_error_document
from skysift.store.db import Store


class Resp:
    def __init__(self, status, content): self.status_code, self.content = status, content


class FlakySession:
    def __init__(self, fails, final=None): self.fails, self.final, self.calls = fails, final or Resp(200, b"ok"), 0
    def get(self, url, params=None, timeout=None):
        self.calls += 1
        if self.calls <= self.fails: raise ConnectionError("boom")
        return self.final


def test_fetch_retries_then_succeeds(tmp_path):
    st = Store(f"sqlite:///{tmp_path.as_posix()}/t.db"); st.init_schema()
    s = FlakySession(2)
    assert fetch("https://x/y", service="t", store=st, session=s, backoff=0.0) == b"ok"
    assert s.calls == 3 and st.counts()["queries"] == 3


def test_fetch_gives_up(tmp_path):
    st = Store(f"sqlite:///{tmp_path.as_posix()}/t.db"); st.init_schema()
    sess = FlakySession(99)
    with pytest.raises(ServiceUnavailable, match="after 3 attempts"):
        fetch("https://x/y", service="t", store=st, session=sess, attempts=3, backoff=0.0)
    assert sess.calls == 3


def test_fetch_default_is_five_attempts_in_total():
    sess = FlakySession(99)
    with pytest.raises(ServiceUnavailable, match="after 5 attempts"):
        fetch("https://x/y", service="t", session=sess, backoff=0.0)
    assert sess.calls == 5


def test_error_document_detection():
    vot = b'<?xml version="1.0"?><VOTABLE><RESOURCE><INFO name="QUERY_STATUS" value="ERROR">UsageFault: bad</INFO></RESOURCE></VOTABLE>'
    assert "UsageFault" in looks_like_error_document(vot)
    assert looks_like_error_document(b"a,b\n1,2\n") is None


def test_fetch_logs_the_target_on_success_and_on_error(tmp_path):
    from skysift.store.db import QueryRow
    st = Store(f"sqlite:///{tmp_path.as_posix()}/t.db"); st.init_schema()
    fetch("https://x/y", service="t", store=st, session=FlakySession(1), backoff=0.0, target_id="kic-1")
    fetch("https://x/z", service="t", store=st, session=FlakySession(0))
    with st.session() as s:
        rows = [(q.url, q.status, q.target_id) for q in s.query(QueryRow).order_by(QueryRow.id)]
    assert rows == [("https://x/y", None, "kic-1"), ("https://x/y", 200, "kic-1"), ("https://x/z", 200, None)]
