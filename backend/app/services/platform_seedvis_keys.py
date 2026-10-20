"""Seedvis key pool settings: several API keys, each with its own concurrent-generation limit."""

from sqlalchemy import func

from app.services.platform_shared import HTTPException, executions, httpx, select

POOL_PATH = "/internal/v1/video/seedvis/credentials"


class PlatformSeedvisKeysMixin:
    def _seedvis_pool_request(self, method: str, path: str = "", payload: dict | None = None) -> dict:
        try:
            response = self.http.request(method, POOL_PATH + path, json=payload, timeout=10)
            if response.status_code == 404:
                raise HTTPException(404, "Không tìm thấy key Seedvis này")
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError):
            raise HTTPException(503, "Không thể cấu hình key Seedvis") from None

    def seedvis_keys(self) -> dict:
        keys = self._seedvis_pool_request("GET").get("keys")
        if not isinstance(keys, list):
            raise HTTPException(503, "Không đọc được danh sách key Seedvis")
        active = self.runtime.slot_usage("seedvis", [{"id": key["id"], "slots": key["slots"]} for key in keys])
        spent = self.runtime.out_of_credit()
        with self.engine.connect() as db:
            waiting = db.execute(select(func.count()).select_from(executions).where(
                executions.c.provider == "seedvis", executions.c.status == "QUEUED",
            )).scalar_one()
        return {
            "keys": [key | {"active": active.get(key["id"], 0), "out_of_credit": key["id"] in spent} for key in keys],
            "total_slots": sum(key["slots"] for key in keys),
            "active": sum(active.get(key["id"], 0) for key in keys),
            "waiting": waiting,
        }

    def seedvis_capacity(self) -> dict:
        """Slot totals any signed-in user may see; never key ids, labels or hints."""
        try:
            pool = self.seedvis_keys()
        except HTTPException:
            return {"keys": 0, "total_slots": 0, "active": 0, "waiting": 0}
        return {"keys": len(pool["keys"])} | {key: pool[key] for key in ("total_slots", "active", "waiting")}

    def _seedvis_keys_changed(self) -> dict:
        self.runtime.forget_credential_slots()
        return self.seedvis_keys()

    def add_seedvis_key(self, api_key: str, label: str, slots: int) -> dict:
        self._seedvis_pool_request("POST", payload={"api_key": api_key, "label": label, "slots": slots})
        return self._seedvis_keys_changed()

    def update_seedvis_key(self, key_id: str, label: str | None, slots: int | None) -> dict:
        self._seedvis_pool_request("PATCH", "/" + key_id, {"label": label, "slots": slots})
        return self._seedvis_keys_changed()

    def delete_seedvis_key(self, key_id: str) -> dict:
        running = next((key["active"] for key in self.seedvis_keys()["keys"] if key["id"] == key_id), 0)
        if running:
            # Its tasks can only be polled and downloaded with this key.
            raise HTTPException(409, f"Key đang chạy {running} video. Đợi các video này xong rồi hãy xóa key.")
        self._seedvis_pool_request("DELETE", "/" + key_id)
        return self._seedvis_keys_changed()
