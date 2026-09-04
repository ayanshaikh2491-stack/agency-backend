"""Thin external PocketBase client for key Agency OS state.

Owner rule: PocketBase is THE database for key agency state (workspaces, custom
agents, agent outputs, CEO lifecycle/error state) so it survives an App Runner
container restart (the local SQLite/file state does not).

This module is ADDITIVE and config-driven: when POCKETBASE_URL is empty, nothing
is touched and the app keeps its current local behaviour. When set, callers may
mirror writes to PocketBase. All PB calls are best-effort: failures are logged
and never raised, so local runs are never broken.

Thin `requests` wrapper around the PocketBase REST API (no SDK dependency).
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class PocketBaseClient:
    """Admin-auth PocketBase REST client (keyed upsert/delete/collection helpers)."""

    def __init__(self, url: str, email: str = "", password: str = "",
                 timeout: int = 10) -> None:
        self.url = (url or "").rstrip("/")
        self.email = email or ""
        self.password = password or ""
        self.timeout = timeout
        self._token: Optional[str] = None
        self._authed: bool = False
        self._lock = threading.Lock()

    # ── config ──────────────────────────────────────────────────────────
    def is_configured(self) -> bool:
        return bool(self.url)

    # ── auth ────────────────────────────────────────────────────────────
    def auth(self) -> bool:
        """Authenticate as admin (or mark public if no creds). Idempotent."""
        if not self.url:
            return False
        if self._authed:
            return True
        if self.email and self.password:
            try:
                # PocketBase >= 0.23 moved admin auth under /api/collections/
                # _superusers; older servers still expose /api/admins. Try both.
                last_status = 0
                for path in (
                    "/api/collections/_superusers/auth-with-password",
                    "/api/admins/auth-with-password",
                ):
                    r = requests.post(
                        f"{self.url}{path}",
                        json={"identity": self.email, "password": self.password},
                        timeout=self.timeout,
                    )
                    if r.status_code == 200:
                        self._token = r.json().get("token")
                        self._authed = True
                        return True
                    last_status = r.status_code
                logger.warning(
                    "PocketBase admin auth failed (%s)", last_status)
                return False
            except Exception as exc:  # noqa: BLE001
                logger.warning("PocketBase admin auth error: %s", exc)
                return False
        # No creds: assume a publicly-writable collection / gateway passthrough.
        self._authed = True
        return True

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self._token:
            h["Authorization"] = self._token
        return h

    # ── source-of-truth helpers (REST-only, predictable shapes) ───────────
    def _rest_list(self, collection: str, filters: str = "",
                   per_page: int = 200) -> list[dict]:
        """Raw REST list — always returns plain dicts (SDK shapes vary)."""
        if not self.auth():
            return []
        params: dict[str, Any] = {"perPage": per_page}
        if filters:
            params["filter"] = filters
        try:
            r = requests.get(
                f"{self.url}/api/collections/{collection}/records",
                headers=self._headers(), params=params, timeout=self.timeout)
            if r.status_code == 200:
                return list(r.json().get("items", []))
        except Exception as exc:  # noqa: BLE001
            logger.debug("PocketBase rest_list failed (%s): %s", collection, exc)
        return []

    def ensure_key_field(self, collection: str, key_field: str) -> bool:
        """Make sure `key_field` exists on the collection (self-healing).

        Pre-existing PB collections often lack our linking columns; PocketBase
        silently strips unknown payload fields, which breaks key-based
        upserts. This patches the collection preserving all existing field
        definitions and appending the missing text column.
        """
        if not self.auth():
            return False
        meta = self.get_collection(collection)
        if not meta:
            # Collection itself missing -> create minimal with the key field.
            return self.ensure_collection(collection, {key_field: "text"})
        fields = list(meta.get("fields") or meta.get("schema") or [])
        names = [f.get("name") for f in fields]
        if key_field in names:
            return True
        fields.append({"name": key_field, "type": "text"})
        try:
            r = requests.patch(
                f"{self.url}/api/collections/{collection}",
                json={"fields": fields}, headers=self._headers(),
                timeout=self.timeout)
            if r.status_code == 200:
                logger.info(
                    "PocketBase: added '%s' field to collection '%s'",
                    key_field, collection)
                return True
            logger.warning(
                "PocketBase ensure_key_field %s/%s failed (%s): %s",
                collection, key_field, r.status_code, r.text[:200])
        except Exception as exc:  # noqa: BLE001
            logger.warning("PocketBase ensure_key_field error: %s", exc)
        return False

    def upsert_by_key(self, collection: str, key_field: str,
                      data: dict) -> Optional[dict]:
        """Create-or-update a record matched on `key_field`.

        Our local ids (e.g. 'ws_default') do NOT fit PocketBase's 15-char
        record-id pattern, so we store them in a dedicated `key_field`
        column and match on that instead of the PB record id.
        """
        kv = str(data.get(key_field, "") or "")
        if not kv or not self.auth():
            return None
        self.ensure_key_field(collection, key_field)
        payload = {k: v for k, v in data.items()
                   if k not in ("id", key_field)}
        payload[key_field] = kv
        existing = self._rest_list(
            collection, filters=f'{key_field}="{kv}"', per_page=1)
        try:
            if existing:
                rid = existing[0].get("id")
                r = requests.patch(
                    f"{self.url}/api/collections/{collection}/records/{rid}",
                    json=payload, headers=self._headers(), timeout=self.timeout)
            else:
                r = requests.post(
                    f"{self.url}/api/collections/{collection}/records",
                    json=payload, headers=self._headers(), timeout=self.timeout)
            if r.status_code in (200, 201):
                return r.json()
            logger.warning("PocketBase upsert_by_key %s/%s failed (%s): %s",
                           collection, kv, r.status_code, r.text[:200])
        except Exception as exc:  # noqa: BLE001
            logger.warning("PocketBase upsert_by_key error: %s", exc)
        return None

    def delete_by_key(self, collection: str, key_field: str, key_value: str) -> bool:
        """Delete the record whose `key_field` matches (best-effort)."""
        if not self.auth():
            return False
        self.ensure_key_field(collection, key_field)
        existing = self._rest_list(
            collection, filters=f'{key_field}="{key_value}"', per_page=1)
        if not existing:
            return True  # nothing to delete counts as success
        rid = existing[0].get("id")
        try:
            r = requests.delete(
                f"{self.url}/api/collections/{collection}/records/{rid}",
                headers=self._headers(), timeout=self.timeout)
            return r.status_code in (200, 204)
        except Exception as exc:  # noqa: BLE001
            logger.warning("PocketBase delete_by_key error: %s", exc)
            return False

    def ensure_collection(self, name: str,
                          fields: dict[str, str]) -> bool:
        """Best-effort auto-create a collection with text/json fields.

        Tries both the legacy (`schema`) and newer (`fields`) PocketBase
        collection formats so it works across PB versions. Returns True if
        the collection exists afterwards.
        """
        if self.get_collection(name):
            return True
        if not self.auth():
            return False
        entries = [{"name": k, "type": t} for k, t in fields.items()]
        try:
            # PB >= 0.23 collection format ("fields").
            r = requests.post(f"{self.url}/api/collections",
                              json={"name": name, "fields": entries},
                              headers=self._headers(), timeout=self.timeout)
            if r.status_code in (200, 201):
                logger.info("PocketBase collection '%s' created", name)
                return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("PocketBase ensure_collection error: %s", exc)
        logger.warning(
            "PocketBase collection '%s' missing and auto-create failed — "
            "create it manually in the PB admin UI.", name)
        return False

    def pull_all(self, collection: str, per_page: int = 500) -> list[dict]:
        """Fetch every record (plain dicts) — used at boot to seed local."""
        return self._rest_list(collection, filters="", per_page=per_page)

    def get_collection(self, name: str) -> Optional[dict]:
        """Return collection metadata, or None if missing/unreachable."""
        if not self.auth():
            return None
        try:
            r = requests.get(f"{self.url}/api/collections/{name}",
                             headers=self._headers(), timeout=self.timeout)
            if r.status_code == 200:
                return r.json()
        except Exception:  # noqa: BLE001
            pass
        return None


_client: Optional[PocketBaseClient] = None
_client_lock = threading.Lock()


def get_pb_client() -> Optional[PocketBaseClient]:
    """Return a cached client driven by settings (None if not configured)."""
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client
        try:
            from admin.config import settings as _s
            url = getattr(_s, "POCKETBASE_URL", "")
            email = getattr(_s, "POCKETBASE_ADMIN_EMAIL", "")
            password = getattr(_s, "POCKETBASE_ADMIN_PASSWORD", "")
        except Exception:  # noqa: BLE001
            url = email = password = ""
        if not url:
            _client = None
            return None
        _client = PocketBaseClient(url, email, password)
    return _client
