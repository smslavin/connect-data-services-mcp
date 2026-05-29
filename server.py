"""
AVEVA Connect Data Services MCP Server

Exposes four tools over the Sequential Data Store (SDS) API:

  list_namespaces      — namespaces available in the tenant
  list_streams         — streams in a namespace, with optional search filter
  get_stream_metadata  — type and engineering context for a named stream
  get_values           — process values for a stream over a time range

---

LIVE MODE
Set the three environment variables below (in .env or the shell) and the
server authenticates via OAuth 2.0 client credentials, then makes real calls
to the AVEVA Connect SDS REST API.

    CONNECT_TENANT_ID      Your tenant GUID from the Connect portal
    CONNECT_CLIENT_ID      Client ID from Security → Clients
    CONNECT_CLIENT_SECRET  Client secret from Security → Clients
    CONNECT_REGION         Cloud region slug (default: uswe)

DEMO MODE
Without credentials the server runs automatically in demo mode. It returns
pre-generated data for the Aveva Water Authority (AWA) namespace — a fictional
water treatment plant with six process streams and a built-in scenario
(drifting filter head loss, a past turbidity exceedance, a pump trip).

No AVEVA account is required for demo mode. The data is deterministic so the
LLM sees the same values every time, making demos and training reproducible.

---

Transport: stdio (default for FastMCP)
Python:    3.11+ recommended, 64-bit, no native extensions required
"""

import os
from datetime import datetime, timedelta, timezone

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

import mock_data

load_dotenv()

# ---------------------------------------------------------------------------
# Mode detection
# ---------------------------------------------------------------------------

_TENANT_ID     = os.getenv("CONNECT_TENANT_ID", "")
_CLIENT_ID     = os.getenv("CONNECT_CLIENT_ID", "")
_CLIENT_SECRET = os.getenv("CONNECT_CLIENT_SECRET", "")
_REGION        = os.getenv("CONNECT_REGION", "")

DEMO_MODE = not all([_TENANT_ID, _CLIENT_ID, _CLIENT_SECRET, _REGION])

# ---------------------------------------------------------------------------
# Live mode: OAuth 2.0 client credentials + SDS REST calls
#
# AVEVA Connect uses a standard OAuth 2.0 client credentials grant.
# Tokens expire in 3600 s; we cache and refresh automatically.
# ---------------------------------------------------------------------------

_BASE_URL = f"https://{_REGION}.datahub.connect.aveva.com"

# Token endpoint is resolved at runtime via OpenID Connect discovery rather
# than hardcoded. This matches AVEVA's own authentication samples and stays
# correct if the endpoint path changes in a future release.
_DISCOVERY_URL = f"{_BASE_URL}/identity/.well-known/openid-configuration"

_token: str | None = None
_token_expiry: datetime | None = None
_token_url: str | None = None


async def _get_token_url() -> str:
    global _token_url
    if _token_url:
        return _token_url
    async with httpx.AsyncClient() as client:
        resp = await client.get(_DISCOVERY_URL, timeout=10)
        resp.raise_for_status()
    _token_url = resp.json()["token_endpoint"]
    return _token_url


async def _get_token() -> str:
    global _token, _token_expiry
    now = datetime.now(timezone.utc)
    if _token and _token_expiry and now < _token_expiry:
        return _token
    token_url = await _get_token_url()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": _CLIENT_ID,
                "client_secret": _CLIENT_SECRET,
            },
        )
        resp.raise_for_status()
        payload = resp.json()
    _token = payload["access_token"]
    # Subtract 30 s so we never use a token in its last seconds
    _token_expiry = now + timedelta(seconds=payload["expires_in"] - 30)
    return _token


async def _get(path: str) -> dict | list:
    """Authenticated GET against the SDS REST API."""
    token = await _get_token()
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{_BASE_URL}{path}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
    if resp.is_error:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text or resp.reason_phrase
        raise RuntimeError(f"HTTP {resp.status_code}: {detail}")
    return resp.json()

# ---------------------------------------------------------------------------
# MCP application
# ---------------------------------------------------------------------------

mcp = FastMCP("connect-data-services")

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_namespaces() -> list[dict]:
    """
    List all namespaces in the AVEVA Connect tenant.

    Namespaces partition data within a tenant — typically one per site or
    operational area. Each namespace is an independent time-series store.
    Use the returned Id to scope all subsequent calls.

    Returns a list of namespace objects. Key fields:
      Id     — pass this as namespace_id in list_streams and get_values
      Name   — human-readable label
      Region — cloud region where the data lives
      State  — Active or Inactive
    """
    if DEMO_MODE:
        return mock_data.get_namespaces()
    return await _get(f"/api/v1/Tenants/{_TENANT_ID}/Namespaces")


@mcp.tool()
async def list_streams(namespace_id: str, query: str = "") -> list[dict]:
    """
    List streams in a namespace, with an optional search filter.

    A stream is a time-ordered sequence of values for a single measurement —
    the SDS equivalent of a PI tag or historian point. Each stream has a
    TypeId that determines its value field types.

    Args:
      namespace_id  Namespace Id from list_namespaces
      query         Case-insensitive filter matched against Id, Name,
                    Description, and Tags. Leave empty to return all streams.

    Returns a list of stream objects. Key fields:
      Id          — pass this as stream_id in get_stream_metadata and get_values
      Name        — human-readable label
      TypeId      — SDS type (e.g. PI-Float32, PI-Boolean)
      Description — engineering context including units and limits
    """
    if DEMO_MODE:
        return mock_data.get_streams(query)
    params = f"?query={query}" if query else ""
    return await _get(
        f"/api/v1/Tenants/{_TENANT_ID}/Namespaces/{namespace_id}/Streams{params}"
    )


@mcp.tool()
async def get_stream_metadata(namespace_id: str, stream_id: str) -> dict:
    """
    Get the full metadata record for a stream.

    Use this before querying values to understand what a stream represents:
    its engineering units, normal operating range, and any limits noted in
    the description.

    Args:
      namespace_id  Namespace Id from list_namespaces
      stream_id     Stream Id from list_streams

    Returns a stream object. Key fields:
      Id                  — stream identifier
      Name                — human-readable label
      TypeId              — SDS type (determines value field names and types)
      Description         — engineering context, units, limits
      InterpolationMode   — how values between stored points are interpreted:
                            Continuous = linear interpolation
                            StepwiseContinuousLeading = holds last value (discrete signals)
    """
    if DEMO_MODE:
        result = mock_data.get_stream(stream_id)
        if result is None:
            raise ValueError(f"Stream '{stream_id}' not found in namespace '{namespace_id}'")
        return result
    return await _get(
        f"/api/v1/Tenants/{_TENANT_ID}/Namespaces/{namespace_id}/Streams/{stream_id}"
    )


@mcp.tool()
async def get_values(
    namespace_id: str,
    stream_id: str,
    start: str,
    end: str,
    count: int = 100,
) -> list[dict]:
    """
    Query process values from a stream over a time range.

    Returns up to `count` values in ascending time order. For long ranges,
    values are evenly sampled across the window — suitable for trend analysis
    and anomaly detection. For a fine-grained look at a short event, use a
    narrow time window with a higher count.

    Args:
      namespace_id  Namespace Id from list_namespaces
      stream_id     Stream Id from list_streams
      start         Range start, ISO 8601 UTC — e.g. 2026-05-29T00:00:00Z
      end           Range end,   ISO 8601 UTC — e.g. 2026-05-29T06:00:00Z
      count         Maximum values to return (default 100, max 1000)

    Returns a list of value objects.
    Numeric streams:  {"Timestamp": "2026-05-29T00:00:00Z", "Value": 2450.3}
    Boolean streams:  {"Timestamp": "2026-05-29T00:00:00Z", "Value": true}
    """
    count = min(count, 1000)
    if DEMO_MODE:
        return mock_data.get_values(stream_id, start, end, count)
    params = f"?startIndex={start}&endIndex={end}&count={count}"
    return await _get(
        f"/api/v1/Tenants/{_TENANT_ID}/Namespaces/{namespace_id}"
        f"/Streams/{stream_id}/Data{params}"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mode = "DEMO — Aveva Water Authority mock data" if DEMO_MODE else f"LIVE — tenant {_TENANT_ID}"
    print(f"connect-data-services MCP server starting ({mode})")
    mcp.run()
