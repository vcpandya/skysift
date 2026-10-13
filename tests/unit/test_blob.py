import hashlib
import io

from skysift.store.blob import LocalBlob, S3Blob, blob_from_url


def test_local_put_get_delete(tmp_path):
    b = LocalBlob(tmp_path)
    sha, loc = b.put(b"hello", "x.fits")
    assert sha == hashlib.sha256(b"hello").hexdigest()
    assert b.exists(loc) and b.get(loc) == b"hello" and b.size(loc) == 5
    b.delete(loc); assert not b.exists(loc)


class FakeS3:
    def __init__(self): self.objs = {}
    def put_object(self, Bucket, Key, Body): self.objs[(Bucket, Key)] = Body
    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objs[(Bucket, Key)])}
    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objs: raise KeyError
        return {"ContentLength": len(self.objs[(Bucket, Key)])}
    def delete_object(self, Bucket, Key): self.objs.pop((Bucket, Key), None)


def test_s3_blob_with_fake_client():
    b = S3Blob("bkt", "pre", client=FakeS3())
    _, loc = b.put(b"abc", "y.fits")
    assert loc.startswith("s3://bkt/pre/") and b.get(loc) == b"abc" and b.size(loc) == 3
    b.delete(loc); assert not b.exists(loc)


def test_blob_from_url(tmp_path):
    assert isinstance(blob_from_url(f"file://{tmp_path.as_posix()}"), LocalBlob)
