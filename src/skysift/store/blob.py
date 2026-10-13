from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse


class Blob(Protocol):
    def put(self, data: bytes, name_hint: str) -> tuple[str, str]: ...
    def get(self, location: str) -> bytes: ...
    def exists(self, location: str) -> bool: ...
    def delete(self, location: str) -> None: ...
    def size(self, location: str) -> int: ...


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class LocalBlob:
    def __init__(self, root: Path):
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, sha: str, name_hint: str) -> Path:
        d = self.root / sha[:2]; d.mkdir(exist_ok=True)
        return d / f"{sha}_{Path(name_hint).name}"

    def put(self, data, name_hint):
        sha = _sha(data); p = self._path(sha, name_hint)
        if not p.exists(): p.write_bytes(data)
        return sha, str(p)

    def get(self, location): return Path(location).read_bytes()
    def exists(self, location): return Path(location).exists()
    def delete(self, location): Path(location).unlink(missing_ok=True)
    def size(self, location): return Path(location).stat().st_size


class S3Blob:
    def __init__(self, bucket: str, prefix: str = "", client=None):
        if client is None:
            import boto3
            client = boto3.client("s3")
        self.bucket, self.prefix, self.client = bucket, prefix.strip("/"), client

    def _key(self, location: str) -> str:
        return urlparse(location).path.lstrip("/")

    def put(self, data, name_hint):
        sha = _sha(data); key = f"{self.prefix}/{sha[:2]}/{sha}_{Path(name_hint).name}".lstrip("/")
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)
        return sha, f"s3://{self.bucket}/{key}"

    def get(self, location): return self.client.get_object(Bucket=self.bucket, Key=self._key(location))["Body"].read()
    def exists(self, location):
        try: self.client.head_object(Bucket=self.bucket, Key=self._key(location)); return True
        except Exception: return False  # noqa: BLE001  (any client error means "not there")
    def delete(self, location): self.client.delete_object(Bucket=self.bucket, Key=self._key(location))
    def size(self, location): return int(self.client.head_object(Bucket=self.bucket, Key=self._key(location))["ContentLength"])


def blob_from_url(url: str) -> Blob:
    u = urlparse(url)
    if u.scheme == "file":
        return LocalBlob(Path((u.netloc + u.path) if u.netloc else u.path))
    if u.scheme == "s3":
        return S3Blob(u.netloc, u.path)
    raise ValueError(f"unsupported blob url: {url}")
