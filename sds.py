"""
Live backend: OAuth 2.0 client credentials and SDS REST calls.

Every method returns raw SDS JSON. sds_types turns it into tool output, so
this module only knows about HTTP.

With a community ID the backend reads streams another tenant shares through a
CONNECT community instead of the client's own namespaces. Shared streams live
under the owning tenant and namespace, every call carries a Community-Id
header, and streams are found through the community search API. Each search
result's Self link gives the owner's path, which this module remembers per
namespace.
"""

from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx

_PAGE = 1000  # streams per list request


class SdsError(Exception):
    def __init__(self, status: int, detail):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status


class LiveSds:
    """AVEVA Connect uses a standard OAuth 2.0 client credentials grant.
    Tokens expire in 3600 s; we cache and refresh automatically."""

    def __init__(self, tenant_id: str, client_id: str, client_secret: str, region: str,
                 community_id: str = ""):
        self.tenant_id = tenant_id
        self.community_id = community_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._base = f"https://{region}.datahub.connect.aveva.com"
        self._token: str | None = None
        self._token_expiry: datetime | None = None
        self._token_url: str | None = None
        self._shared: dict[str, str] = {}  # namespace -> owner's namespace URL, community mode

    async def _get_token_url(self) -> str:
        # Resolved through OpenID Connect discovery rather than hardcoded. This
        # matches AVEVA's own samples and survives an endpoint move.
        if not self._token_url:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{self._base}/identity/.well-known/openid-configuration", timeout=10)
                resp.raise_for_status()
            self._token_url = resp.json()["token_endpoint"]
        return self._token_url

    async def _get_token(self) -> str:
        now = datetime.now(timezone.utc)
        if self._token and self._token_expiry and now < self._token_expiry:
            return self._token
        token_url = await self._get_token_url()
        async with httpx.AsyncClient() as client:
            resp = await client.post(token_url, data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            })
        if resp.is_error:
            raise SdsError(resp.status_code, "authentication failed; check CONNECT_CLIENT_ID and CONNECT_CLIENT_SECRET")
        payload = resp.json()
        self._token = payload["access_token"]
        # Subtract 30 s so we never use a token in its last seconds
        self._token_expiry = now + timedelta(seconds=payload["expires_in"] - 30)
        return self._token

    async def _get(self, url: str, params: dict | None = None):
        headers = {"Authorization": f"Bearer {await self._get_token()}"}
        if self.community_id:
            headers["Community-Id"] = self.community_id
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params, headers=headers, timeout=30)
        if resp.is_error:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text or resp.reason_phrase
            raise SdsError(resp.status_code, detail)
        return resp.json()

    async def _ns(self, namespace_id: str) -> str:
        if not self.community_id:
            return f"{self._base}/api/v1/Tenants/{self.tenant_id}/Namespaces/{quote(namespace_id, safe='')}"
        if namespace_id not in self._shared:
            await self._search()
        if namespace_id not in self._shared:
            raise SdsError(404, f"no streams in namespace '{namespace_id}' are shared with this community")
        return self._shared[namespace_id]

    async def _stream(self, namespace_id: str, stream_id: str) -> str:
        return f"{await self._ns(namespace_id)}/Streams/{quote(stream_id, safe='')}"

    async def _search(self, query: str = "", limit: int = _PAGE) -> list[dict]:
        """Community search; also records where each shared namespace lives."""
        out: list[dict] = []
        while len(out) < limit:
            params = {"skip": len(out), "count": min(_PAGE, limit - len(out))}
            if query:
                params["query"] = query
            page = await self._get(f"{self._base}/api/v1-preview/search/communities/"
                                   f"{quote(self.community_id, safe='')}/streams", params)
            out += page
            if len(page) < params["count"]:
                break
        for s in out:
            self_link = s.get("Self", "")
            cut = self_link.lower().rfind("/streams/")
            if s.get("NamespaceId") and cut > 0:
                self._shared[s["NamespaceId"]] = self_link[:cut]
        return out

    async def namespaces(self) -> list[dict]:
        if not self.community_id:
            return await self._get(f"{self._base}/api/v1/Tenants/{self.tenant_id}/Namespaces")
        found: dict[str, dict] = {}
        for s in await self._search():
            found.setdefault(s["NamespaceId"], {
                "Id": s["NamespaceId"],
                "Name": s.get("TenantName") or s["NamespaceId"],
                "Description": f"Streams shared by {s.get('TenantName') or s.get('TenantId')} "
                               f"through community {self.community_id}",
                "Region": self._shared.get(s["NamespaceId"], "").split("//")[-1].split(".")[0],
            })
        return list(found.values())

    async def streams(self, namespace_id: str, query: str = "", limit: int = _PAGE) -> list[dict]:
        if self.community_id:
            return [s for s in await self._search(query, limit) if s.get("NamespaceId") == namespace_id]
        out: list[dict] = []
        while len(out) < limit:
            params = {"skip": len(out), "count": min(_PAGE, limit - len(out))}
            if query:
                params["query"] = query
            page = await self._get(f"{await self._ns(namespace_id)}/Streams", params)
            out += page
            if len(page) < params["count"]:
                break
        return out

    async def resolved(self, namespace_id: str, stream_id: str) -> dict:
        """The stream with its type inlined. A community member can't read /Types directly."""
        return await self._get(await self._stream(namespace_id, stream_id) + "/Resolved")

    async def stream_tags(self, namespace_id: str, stream_id: str) -> list[str]:
        return await self._get(await self._stream(namespace_id, stream_id) + "/Tags")

    async def stream_metadata(self, namespace_id: str, stream_id: str) -> dict:
        return await self._get(await self._stream(namespace_id, stream_id) + "/Metadata")

    async def sampled(self, namespace_id: str, stream_id: str, start: str, end: str,
                      intervals: int, sample_by: str) -> list[dict]:
        return await self._get(await self._stream(namespace_id, stream_id) + "/Data/Transform/Sampled", {
            "startIndex": start, "endIndex": end, "intervals": intervals, "sampleBy": sample_by})

    async def interpolated(self, namespace_id: str, stream_id: str, start: str, end: str,
                           count: int) -> list[dict]:
        return await self._get(await self._stream(namespace_id, stream_id) + "/Data/Transform/Interpolated", {
            "startIndex": start, "endIndex": end, "count": count})
