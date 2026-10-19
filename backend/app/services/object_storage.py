"""Optional S3-compatible copy of the storage volume (Cloudflare R2), used to keep videos off the disk."""

import os
from pathlib import Path


class ObjectStorage:
    """Objects mirror the volume layout: `<prefix>/videos/<file>` holds `storage/videos/<file>`."""

    def __init__(self, client, bucket: str, prefix: str = ""):
        self.client = client
        self.bucket = bucket
        self.prefix = prefix.strip("/")

    @classmethod
    def from_env(cls) -> "ObjectStorage | None":
        names = ("R2_ENDPOINT", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
        values = [os.getenv(name, "").strip() for name in names]
        if not all(values):
            return None
        endpoint, bucket, access_key, secret_key = values
        import boto3
        from botocore.config import Config

        try:
            # Recent botocore adds checksum headers to every upload; only send them where S3 requires them.
            config = Config(signature_version="s3v4", request_checksum_calculation="when_required",
                            response_checksum_validation="when_required")
        except TypeError:
            config = Config(signature_version="s3v4")
        client = boto3.client(
            "s3", endpoint_url=endpoint, aws_access_key_id=access_key,
            aws_secret_access_key=secret_key, region_name="auto", config=config,
        )
        return cls(client, bucket, os.getenv("R2_PREFIX", "storage"))

    def key(self, relative: str) -> str:
        return f"{self.prefix}/{relative}" if self.prefix else relative

    def stat(self, relative: str) -> tuple[int, float] | None:
        """Size and upload time of an object, or None when it is not stored."""
        from botocore.exceptions import ClientError

        try:
            head = self.client.head_object(Bucket=self.bucket, Key=self.key(relative))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        return head["ContentLength"], head["LastModified"].timestamp()

    def size(self, relative: str) -> int | None:
        found = self.stat(relative)
        return found[0] if found else None

    def upload(self, path: Path, relative: str, content_type: str) -> None:
        self.client.upload_file(str(path), self.bucket, self.key(relative),
                                ExtraArgs={"ContentType": content_type})

    def download(self, relative: str, target: Path) -> None:
        temporary = target.with_name(target.name + ".r2part")
        try:
            self.client.download_file(self.bucket, self.key(relative), str(temporary))
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)

    def url(self, relative: str, content_type: str, filename: str | None = None, expires: int = 3600) -> str:
        params = {"Bucket": self.bucket, "Key": self.key(relative), "ResponseContentType": content_type}
        if filename:
            params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
        return self.client.generate_presigned_url("get_object", Params=params, ExpiresIn=expires)
