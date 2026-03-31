"""Pi-hole v6 REST API adapter — async httpx client with session auth."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)


class PiholeUnavailableError(Exception):
    """Raised when Pi-hole cannot be reached."""


@dataclass
class PiholeAdapter:
    base_url: str = "http://127.0.0.1:8081/api"
    password: str = ""
    _client: httpx.AsyncClient | None = field(default=None, repr=False)
    _sid: str | None = field(default=None, repr=False)

    async def connect(self) -> None:
        """Authenticate with Pi-hole and obtain a session ID."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10.0)
        try:
            resp = await self._client.post(
                f"{self.base_url}/auth",
                json={"password": self.password},
            )
            resp.raise_for_status()
            self._sid = resp.json()["session"]["sid"]
        except (httpx.HTTPError, KeyError, Exception) as exc:
            raise PiholeUnavailableError(f"Cannot connect to Pi-hole at {self.base_url}") from exc

    async def disconnect(self) -> None:
        """Release the Pi-hole session."""
        if self._client and self._sid:
            try:
                await self._client.delete(
                    f"{self.base_url}/auth",
                    headers={"X-FTL-SID": self._sid},
                )
            except Exception:
                pass
        self._sid = None
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Send an authenticated request, re-auth once on 401."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10.0)
        headers = kwargs.pop("headers", {})
        if self._sid:
            headers["X-FTL-SID"] = self._sid
        try:
            resp = await self._client.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise PiholeUnavailableError(str(exc)) from exc
        if resp.status_code == 401:
            await self.connect()
            headers["X-FTL-SID"] = self._sid or ""
            resp = await self._client.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)
        return resp

    # -- Groups ----------------------------------------------------------------

    async def get_groups(self) -> list[dict]:
        resp = await self._request("GET", "/groups")
        return resp.json().get("groups", [])

    async def create_group(self, name: str, enabled: bool = True) -> dict:
        resp = await self._request("POST", "/groups", json={"name": name, "enabled": enabled})
        return resp.json()

    async def delete_group(self, name: str) -> None:
        await self._request("DELETE", f"/groups/{quote(name, safe='')}")

    # -- Clients ---------------------------------------------------------------

    async def get_clients(self) -> list[dict]:
        resp = await self._request("GET", "/clients")
        return resp.json().get("clients", [])

    async def add_client(self, client: str, groups: list[int]) -> dict:
        resp = await self._request("POST", "/clients", json={"client": client, "groups": groups})
        return resp.json()

    async def update_client(self, client: str, groups: list[int]) -> dict:
        resp = await self._request("PUT", f"/clients/{quote(client, safe='')}", json={"groups": groups})
        return resp.json()

    # -- Domain rules ----------------------------------------------------------

    async def add_domain_rule(
        self,
        domain: str,
        rule_type: str,
        kind: str,
        groups: list[int],
        comment: str = "",
    ) -> dict:
        resp = await self._request(
            "POST",
            f"/domains/{rule_type}/{kind}",
            json={"domain": domain, "groups": groups, "comment": comment},
        )
        return resp.json()

    async def get_domain_rules(self, rule_type: str, kind: str) -> list[dict]:
        resp = await self._request("GET", f"/domains/{rule_type}/{kind}")
        return resp.json().get("domains", [])

    async def delete_domain_rule(self, domain: str, rule_type: str, kind: str) -> None:
        await self._request("DELETE", f"/domains/{rule_type}/{kind}/{quote(domain, safe='')}")

    # -- Blocking status -------------------------------------------------------

    async def get_blocking(self) -> dict:
        resp = await self._request("GET", "/dns/blocking")
        return resp.json()

    # -- Health ----------------------------------------------------------------

    async def flush_dns_cache(self) -> None:
        """Flush DNS cache by restarting the DNS resolver.

        Requires webserver.api.allow_destructive=true in Pi-hole config.
        """
        try:
            resp = await self._request("POST", "/action/restartdns")
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise PiholeUnavailableError(
                f"DNS cache flush failed: {exc.response.status_code}"
            ) from exc

    # -- Network devices ------------------------------------------------------

    async def get_network_devices(self) -> list[dict]:
        """Fetch network device info including lastQuery and lastSeen timestamps."""
        resp = await self._request("GET", "/network/devices")
        return resp.json().get("devices", [])

    # -- Health ----------------------------------------------------------------

    async def is_healthy(self) -> bool:
        try:
            resp = await self._request("GET", "/dns/blocking")
            return resp.status_code == 200
        except Exception:
            return False
