import hashlib
import io
from pathlib import Path

import pytest

from skysift.store.blob import LocalBlob, S3Blob, blob_from_url


def test_local_put_get_delete(tmp_path):
    b = LocalBlob(tmp_path)
    sha, loc = b.put(b"hello", "x.fits")
    assert sha == hashlib.sha256(b"hello").hexdigest()
    assert b.exists(loc) and b.get(loc) == b"hello" and b.size(loc) == 5
    b.delete(loc); assert not b.exists(loc)


def test_local_put_is_atomic_and_never_rewrites(tmp_path, monkeypatch):
    b = LocalBlob(tmp_path)
    sha, loc = b.put(b"hello", "x.fits")
    assert list(Path(loc).parent.glob("*.part")) == []
    mtime = Path(loc).stat().st_mtime_ns
    assert b.put(b"hello", "x.fits") == (sha, loc) and Path(loc).stat().st_mtime_ns == mtime

    def broken_replace(src, dst): raise OSError("disk full")
    monkeypatch.setattr("skysift.store.blob.os.replace", broken_replace)
    with pytest.raises(OSError):
        b.put(b"other", "z.fits")
    assert not any(p.name.startswith(hashlib.sha256(b"other").hexdigest()) and not p.name.endswith(".part")
                   for p in tmp_path.rglob("*"))         # a failed write never leaves a complete-looking file


class ClientError(Exception):
    """Shaped like botocore.exceptions.ClientError: carries a response dict."""
    def __init__(self, code):
        super().__init__(code); self.response = {"Error": {"Code": code}}


class FakeS3:
    def __init__(self): self.objs = {}; self.head_error = None
    def put_object(self, Bucket, Key, Body): self.objs[(Bucket, Key)] = Body
    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objs[(Bucket, Key)])}
    def head_object(self, Bucket, Key):
        if self.head_error: raise self.head_error
        if (Bucket, Key) not in self.objs: raise ClientError("404")
        return {"ContentLength": len(self.objs[(Bucket, Key)])}
    def delete_object(self, Bucket, Key): self.objs.pop((Bucket, Key), None)


def test_s3_blob_with_fake_client():
    b = S3Blob("bkt", "pre", client=FakeS3())
    _, loc = b.put(b"abc", "y.fits")
    assert loc.startswith("s3://bkt/pre/") and b.get(loc) == b"abc" and b.size(loc) == 3
    b.delete(loc); assert not b.exists(loc)


def test_s3_exists_reraises_errors_other_than_not_found():
    client = FakeS3(); b = S3Blob("bkt", "pre", client=client)
    for code in ("NoSuchKey", "NotFound"):
        client.head_error = ClientError(code); assert not b.exists("s3://bkt/pre/x")
    client.head_error = ClientError("403")
    with pytest.raises(ClientError):
        b.exists("s3://bkt/pre/x")
    client.head_error = ConnectionError("network down")
    with pytest.raises(ConnectionError):
        b.exists("s3://bkt/pre/x")


def test_blob_from_url(tmp_path):
    assert isinstance(blob_from_url(f"file://{tmp_path.as_posix()}"), LocalBlob)
