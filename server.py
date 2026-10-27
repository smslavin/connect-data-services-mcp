"""
AVEVA Connect Data Services MCP Server

Exposes four tools over the Sequential Data Store (SDS) API:

  list_namespaces      — namespaces available in the tenant
  list_streams         — streams in a namespace, with optional search filter
  get_stream_metadata  — type, units, limits and tags for a named stream
  get_values           — process values for a stream over a time range

---

LIVE MODE
Set the four environment variables below (in .env or the shell) and the
server authenticates via OAuth 2.0 client credentials, then makes real calls
to the AVEVA Connect SDS REST API.

    CONNECT_TENANT_ID      Your tenant GUID from the Connect portal
    CONNECT_CLIENT_ID      Client ID from Security → Clients
    CONNECT_CLIENT_SECRET  Client secret from Security → Clients
    CONNECT_REGION         Cloud region slug, e.g. uswe or euno

COMMUNITY MODE
Also set CONNECT_COMMUNITY_ID to read streams another tenant shares through
a CONNECT community instead of your own namespaces. list_namespaces then
returns the namespaces that hold shared streams. The client must be added to
the community (it is per client, not per tenant).

DEMO MODE
Without credentials the server runs automatically in demo mode. It returns
pre-generated data for the Aveva Water Authority (AWA) namespace — a fictional
water treatment plant with six process streams and a built-in scenario
(drifting filter head loss, a past turbidity exceedance, a pump trip).

No AVEVA account is required for demo mode. The data is deterministic so the
LLM sees the same values every time, making demos and training reproducible.

Both modes return raw SDS-shaped JSON from a backend (sds.LiveSds or
mock_data.DemoSds) and share one conversion to tool output (sds_types).

---

Transport: stdio (default for MCPServer)
Python:    3.11+ recommended, 64-bit, no native extensions required
MCP:       spec 2026-07-28, mcp SDK 2.x
"""

import os
from typing import Literal

from dotenv import load_dotenv
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

import mock_data
from sds import LiveSds, SdsError
from sds_types import (Namespace, Stream, StreamDetail, StreamValue, to_namespace, to_stream,
                       to_stream_detail, to_values, value_property)

load_dotenv()

_VERSION = "0.4.0"

_READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=True)

_MAX_VALUES = 1000

# ---------------------------------------------------------------------------
# Backend selection
#
# All four variables are required. The server falls back to demo mode if any
# are missing rather than connecting to the wrong endpoint.
# ---------------------------------------------------------------------------

_CONFIG = {k: os.getenv(k, "") for k in
           ("CONNECT_TENANT_ID", "CONNECT_CLIENT_ID", "CONNECT_CLIENT_SECRET", "CONNECT_REGION")}

DEMO_MODE = not all(_CONFIG.values())

backend = mock_data.DemoSds() if DEMO_MODE else LiveSds(
    _CONFIG["CONNECT_TENANT_ID"], _CONFIG["CONNECT_CLIENT_ID"],
    _CONFIG["CONNECT_CLIENT_SECRET"], _CONFIG["CONNECT_REGION"],
    community_id=os.getenv("CONNECT_COMMUNITY_ID", ""))

# (namespace, stream) -> (value property, value SDS omits as default)
_value_props: dict[tuple[str, str], tuple[str, object]] = {}


async def _call(coro, missing: str):
    """Await a backend call, turning SDS failures into messages the model can act on."""
    try:
        return await coro
    except mock_data.NotFound:
        raise ToolError(missing)
    except SdsError as e:
        if e.status == 404:
            raise ToolError(missing)
        if e.status == 403:
            raise ToolError(f"{missing}, or this client may not read it")
        raise ToolError(str(e))


async def _resolved(namespace_id: str, stream_id: str) -> dict:
    """The stream with its type inlined; caches the value property get_values needs."""
    missing = f"Stream '{stream_id}' not found in namespace '{namespace_id}'"
    resolved = await _call(backend.resolved(namespace_id, stream_id), missing)
    _value_props[(namespace_id, stream_id)] = value_property(resolved.get("Type", {}))
    return resolved


async def _value_prop(namespace_id: str, stream_id: str) -> tuple[str, object]:
    key = (namespace_id, stream_id)
    if key not in _value_props:
        await _resolved(namespace_id, stream_id)
    return _value_props[key]

# ---------------------------------------------------------------------------
# MCP application
# ---------------------------------------------------------------------------

mcp = MCPServer(
    name="connect-data-services",
    title="AVEVA Connect Data Services",
    description=(
        "Read-only access to AVEVA Connect Data Services (SDS) namespaces, "
        "streams, and time-series values."
    ),
    instructions=(
        "Query the AVEVA Connect Sequential Data Store (SDS): list namespaces, "
        "then the streams within a namespace, then a stream's metadata for "
        "engineering context (units, limits, interpolation mode), then its "
        "time-series values. Runs in demo mode against a fictional water "
        "treatment plant (Aveva Water Authority) unless AVEVA Connect "
        "credentials are configured, in which case it queries a real tenant."
    ),
    version=_VERSION,
)

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool(annotations=_READ_ONLY, structured_output=True)
async def list_namespaces() -> list[Namespace]:
    """
    List all namespaces in the AVEVA Connect tenant.

    Namespaces partition data within a tenant — typically one per site or
    operational area. Each namespace is an independent time-series store.
    Use the returned Id to scope all subsequent calls.

    When the server reads a CONNECT community, this lists the namespaces that
    hold streams shared with it; Name is the tenant that shares them.

    Returns a list of namespace objects. Key fields:
      Id     — pass this as namespace_id in list_streams and get_values
      Name   — human-readable label
      Region — cloud region where the data lives
      State  — namespace state code as SDS reports it
    """
    raw = await _call(backend.namespaces(), "No namespaces found")
    return [to_namespace(n) for n in raw]


@mcp.tool(annotations=_READ_ONLY, structured_output=True)
async def list_streams(namespace_id: str, query: str = "") -> list[Stream]:
    """
    List streams in a namespace, with an optional search filter.

    A stream is a time-ordered sequence of values for a single measurement —
    the SDS equivalent of a PI tag or historian point. Each stream has a
    TypeId that determines its value field types.

    Args:
      namespace_id  Namespace Id from list_namespaces
      query         SDS search, matched against Id, Name, Description, and
                    Tags. Use * as a wildcard, e.g. "FIT*" or "pg2027.P-101*".
                    Leave empty to return all streams (up to 1000).

    Returns a list of stream objects. Key fields:
      Id          — pass this as stream_id in get_stream_metadata and get_values
      Name        — human-readable label
      TypeId      — SDS type (e.g. PI-Float32, PI-Boolean)
      Description — engineering context
      InterpolationMode — TypeDefault when the stream uses its type's mode;
                    get_stream_metadata resolves it
    If 1000 streams come back, narrow the query; the list may be cut off.
    """
    raw = await _call(backend.streams(namespace_id, query),
                      f"Namespace '{namespace_id}' not found")
    return [to_stream(s) for s in raw]


@mcp.tool(annotations=_READ_ONLY, structured_output=True)
async def get_stream_metadata(namespace_id: str, stream_id: str) -> StreamDetail:
    """
    Get the full metadata record for a stream.

    Use this before querying values to understand what a stream represents:
    its engineering units, normal operating range, and any limits.

    Args:
      namespace_id  Namespace Id from list_namespaces
      stream_id     Stream Id from list_streams

    Returns a stream object. Key fields:
      Id                  — stream identifier
      Name                — human-readable label
      TypeId              — SDS type (determines value field names and types)
      Description         — engineering context
      Metadata            — key/value pairs set by whoever loaded the stream,
                            typically engineering units and limits (e.g.
                            EngineeringUnits, NormalLow, NormalHigh)
      Tags                — free-form labels
      InterpolationMode   — how values between stored points are interpreted:
                            Continuous = linear interpolation
                            StepwiseContinuousLeading = holds last value (discrete signals)
    """
    missing = f"Stream '{stream_id}' not found in namespace '{namespace_id}'"
    resolved = await _resolved(namespace_id, stream_id)
    tags = await _call(backend.stream_tags(namespace_id, stream_id), missing)
    metadata = await _call(backend.stream_metadata(namespace_id, stream_id), missing)
    return to_stream_detail(resolved, tags, metadata)


@mcp.tool(annotations=_READ_ONLY, structured_output=True)
async def get_values(
    namespace_id: str,
    stream_id: str,
    start: str,
    end: str,
    count: int = 100,
    method: Literal["sampled", "interpolated"] = "sampled",
    ctx: Context | None = None,
) -> list[StreamValue]:
    """
    Query process values from a stream over a time range.

    Returns at most `count` values in ascending time order, spread across the
    whole window, so a long range gives a trend rather than its first few
    points.

    Args:
      namespace_id  Namespace Id from list_namespaces
      stream_id     Stream Id from list_streams
      start         Range start, ISO 8601 UTC — e.g. 2026-05-29T00:00:00Z
      end           Range end,   ISO 8601 UTC — e.g. 2026-05-29T06:00:00Z
      count         Maximum values to return (default 100, max 1000)
      method        sampled (default): stored values that represent the window.
                      It splits the window into count/4 intervals and returns
                      the first, last, minimum and maximum of each, so spikes
                      and steps survive. If the window holds fewer stored values
                      than that, every stored value is returned.
                    interpolated: exactly `count` evenly spaced values. Easier
                      to compare across streams, but it can step over a short
                      spike.

    Returns a list of value objects.
    Numeric streams:  {"Timestamp": "2026-05-29T00:00:00Z", "Value": 2450.3}
    Boolean streams:  {"Timestamp": "2026-05-29T00:00:00Z", "Value": true}
    A value of 0 or false is reported as such. SDS omits default values on
    the wire; this tool fills them back in.
    """
    count = max(1, min(count, _MAX_VALUES))
    if ctx:
        await ctx.report_progress(0, 1, message=f"Querying {stream_id}")

    prop, default = await _value_prop(namespace_id, stream_id)
    missing = f"Stream '{stream_id}' not found in namespace '{namespace_id}'"
    if method == "interpolated":
        raw = await _call(backend.interpolated(namespace_id, stream_id, start, end, count), missing)
    else:
        raw = await _call(backend.sampled(namespace_id, stream_id, start, end,
                                          max(1, count // 4), prop), missing)
    values = to_values(raw, prop, default)[:count]

    if ctx:
        await ctx.report_progress(1, 1, message=f"Retrieved {len(values)} values")
    return values


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mode = "DEMO — Aveva Water Authority mock data" if DEMO_MODE else f"LIVE — tenant {backend.tenant_id}"
    if not DEMO_MODE and backend.community_id:
        mode += f", community {backend.community_id}"
    print(f"connect-data-services MCP server starting ({mode})")
    mcp.run()
