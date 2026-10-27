"""Every tool end to end through an MCP client, against the demo backend.

The demo backend returns raw SDS shapes, so these tests also catch a tool
output schema that no longer fits what SDS sends.
"""

import pytest
from mcp.client import Client

import mock_data
import server

pytestmark = pytest.mark.anyio

NS = "AWA-Production"
WINDOW = {"start": mock_data._cache["FIT-101.PV"][0]["Timestamp"],
          "end": mock_data._cache["FIT-101.PV"][-1]["Timestamp"]}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def demo(monkeypatch):
    monkeypatch.setattr(server, "backend", mock_data.DemoSds())
    monkeypatch.setattr(server, "_value_props", {})


async def call(name: str, **args):
    async with Client(server.mcp) as client:
        result = await client.call_tool(name, args)
    assert not result.is_error, result.content
    content = result.structured_content
    return content["result"] if set(content) == {"result"} else content  # lists come wrapped


async def test_list_namespaces():
    assert [n["Id"] for n in await call("list_namespaces")] == [NS]


async def test_list_streams_query():
    streams = await call("list_streams", namespace_id=NS, query="turbidity")
    assert [s["Id"] for s in streams] == ["AIT-501.PV"]
    assert streams[0]["InterpolationMode"] == "Continuous"


async def test_get_stream_metadata_includes_units():
    detail = await call("get_stream_metadata", namespace_id=NS, stream_id="FIT-101.PV")
    assert detail["Metadata"]["EngineeringUnits"] == "gpm"
    assert detail["Tags"] == ["Influent", "Flow"]


async def test_get_stream_metadata_unknown_stream():
    async with Client(server.mcp) as client:
        result = await client.call_tool("get_stream_metadata", {"namespace_id": NS, "stream_id": "NOPE"})
    assert result.is_error
    assert "not found" in result.content[0].text


async def test_sampled_values_span_the_window():
    values = await call("get_values", namespace_id=NS, stream_id="FIT-101.PV", count=40, **WINDOW)
    assert len(values) <= 40
    assert values[0]["Timestamp"] == WINDOW["start"]
    assert values[-1]["Timestamp"] == WINDOW["end"]


async def test_sampled_values_keep_the_turbidity_spike():
    values = await call("get_values", namespace_id=NS, stream_id="AIT-501.PV", count=40, **WINDOW)
    assert max(v["Value"] for v in values) > 1.0  # the permit exceedance


async def test_interpolated_values_are_exact_count():
    values = await call("get_values", namespace_id=NS, stream_id="FIT-101.PV", count=25,
                        method="interpolated", **WINDOW)
    assert len(values) == 25


async def test_omitted_false_comes_back_false():
    values = await call("get_values", namespace_id=NS, stream_id="P-201.RunStatus", count=400, **WINDOW)
    assert {v["Value"] for v in values} == {True, False}  # the pump trip


@pytest.fixture
def community(monkeypatch):
    monkeypatch.setattr(server, "backend", mock_data.DemoSds(community_id="c-1"))


async def test_community_lists_the_sharing_namespace(community):
    [ns] = await call("list_namespaces")
    assert ns["Id"] == NS and ns["Name"] == mock_data.COMMUNITY_OWNER


async def test_community_lists_only_shared_streams(community):
    ids = [s["Id"] for s in await call("list_streams", namespace_id=NS)]
    assert "FIT-101.PV" in ids and "P-201.RunStatus" not in ids


async def test_community_metadata_and_values(community):
    detail = await call("get_stream_metadata", namespace_id=NS, stream_id="FIT-101.PV")
    assert detail["TypeId"] == "PI-Float32"
    assert detail["InterpolationMode"] == "Continuous"
    values = await call("get_values", namespace_id=NS, stream_id="AIT-501.PV", count=40, **WINDOW)
    assert max(v["Value"] for v in values) > 1.0


async def test_community_unshared_stream_says_why(community):
    async with Client(server.mcp) as client:
        result = await client.call_tool("get_values", {"namespace_id": NS, "stream_id": "P-201.RunStatus", **WINDOW})
    assert result.is_error
    assert "may not read it" in result.content[0].text
