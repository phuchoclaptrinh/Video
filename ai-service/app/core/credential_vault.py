import base64
import contextlib
import hashlib
import json
import os
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class CredentialVault:
    def __init__(self, path: str, secret: str):
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
        self.fernet = Fernet(key)
        self.path = Path(path)
        self.lock = threading.Lock()

    def get(self, provider: str) -> str | None:
        with self.lock:
            values = self._read()
            encrypted = values.get(provider)
            if not encrypted:
                return None
            try:
                return self.fernet.decrypt(encrypted.encode()).decode()
            except (InvalidToken, ValueError):
                return None

    def set(self, provider: str, credential: str) -> None:
        with self.lock:
            values = self._read()
            values[provider] = self.fernet.encrypt(credential.encode()).decode()
            self._write(values)

    def delete(self, provider: str) -> None:
        with self.lock:
            values = self._read()
            values.pop(provider, None)
            self._write(values)

    def _read(self) -> dict[str, str]:
        if not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, values: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(values), encoding="utf-8")
        with contextlib.suppress(OSError):
            os.chmod(temporary, 0o600)
        temporary.replace(self.path)
