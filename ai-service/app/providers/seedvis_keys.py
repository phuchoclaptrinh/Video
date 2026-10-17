"""Several Seedvis API keys, each with its own limit of concurrent generations.

The backend picks a key with a free slot for every new task and keeps using that key to poll or cancel it,
because a Seedvis task and its idempotency key belong to the account that created them.
"""

import json
import re
import secrets
import threading

from app.core.credential_vault import CredentialVault

POOL_NAME = "seedvis_pool"
LEGACY_NAME = "seedvis"
PRIMARY_ID = "primary"
DEFAULT_SLOTS = 4
MAX_SLOTS = 20
KEY_ID_RE = re.compile(r"[a-z0-9]{4,16}")


class SeedvisKeyPool:
    def __init__(self, vault: CredentialVault, fallback_key: str | None = None):
        self.vault = vault
        self.fallback_key = fallback_key
        self.lock = threading.Lock()

    def _entries(self) -> list[dict]:
        raw = self.vault.get(POOL_NAME)
        try:
            data = json.loads(raw) if raw else None
        except ValueError:
            data = None
        if isinstance(data, list):
            return [
                entry for entry in data
                if isinstance(entry, dict) and isinstance(entry.get("api_key"), str)
                and KEY_ID_RE.fullmatch(str(entry.get("id", "")))
            ]
        # A key saved before the pool existed keeps working as the primary key.
        legacy = self.vault.get(LEGACY_NAME) or self.fallback_key
        return [{"id": PRIMARY_ID, "label": "Key chính", "api_key": legacy, "slots": DEFAULT_SLOTS}] if legacy else []

    def _save(self, entries: list[dict]) -> None:
        self.vault.set(POOL_NAME, json.dumps(entries))
        # The first key also serves model listing and reference images, which read the legacy name.
        if entries:
            self.vault.set(LEGACY_NAME, entries[0]["api_key"])
        else:
            self.vault.delete(LEGACY_NAME)

    @staticmethod
    def _public(entry: dict) -> dict:
        return {"id": entry["id"], "label": entry.get("label") or "", "slots": entry["slots"],
                "hint": "…" + entry["api_key"][-4:]}

    @staticmethod
    def _slots(value: int | None) -> int:
        return max(1, min(MAX_SLOTS, int(value or DEFAULT_SLOTS)))

    def public(self) -> list[dict]:
        with self.lock:
            return [self._public(entry) for entry in self._entries()]

    def key(self, key_id: str) -> str | None:
        with self.lock:
            return next((entry["api_key"] for entry in self._entries() if entry["id"] == key_id), None)

    def primary_key(self) -> str | None:
        with self.lock:
            entries = self._entries()
            return entries[0]["api_key"] if entries else None

    def add(self, api_key: str, label: str = "", slots: int | None = None) -> dict:
        with self.lock:
            entries = self._entries()
            existing = next((entry for entry in entries if entry["api_key"] == api_key), None)
            if existing:
                existing.update(label=label or existing.get("label", ""), slots=self._slots(slots or existing["slots"]))
                entry = existing
            else:
                entry = {"id": secrets.token_hex(4), "label": label, "api_key": api_key, "slots": self._slots(slots)}
                entries.append(entry)
            self._save(entries)
            return self._public(entry)

    def update(self, key_id: str, *, label: str | None = None, slots: int | None = None) -> dict | None:
        with self.lock:
            entries = self._entries()
            entry = next((item for item in entries if item["id"] == key_id), None)
            if entry is None:
                return None
            if label is not None:
                entry["label"] = label
            if slots is not None:
                entry["slots"] = self._slots(slots)
            self._save(entries)
            return self._public(entry)

    def remove(self, key_id: str) -> bool:
        with self.lock:
            entries = self._entries()
            remaining = [entry for entry in entries if entry["id"] != key_id]
            if len(remaining) == len(entries):
                return False
            self._save(remaining)
            return True

    def set_primary(self, api_key: str) -> None:
        """Single-key settings replace the first key and keep any additional keys."""
        with self.lock:
            entries = self._entries()
            if entries:
                entries[0]["api_key"] = api_key
            else:
                entries = [{"id": PRIMARY_ID, "label": "Key chính", "api_key": api_key, "slots": DEFAULT_SLOTS}]
            self._save(entries)

    def remove_primary(self) -> None:
        with self.lock:
            self._save(self._entries()[1:])
