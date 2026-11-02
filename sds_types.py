"""
Tool output shapes, and the functions that build them from raw SDS responses.

Raw SDS JSON is not stable enough to hand straight to an MCP output schema:
enums arrive as integers, and SDS leaves out any property that holds its
default value. Both backends (live and demo) return raw SDS-shaped JSON, and
every tool passes it through the functions here, so demo mode exercises the
same conversion that live mode depends on.
"""

from typing import Any, Literal, TypedDict

# SDS enums, by integer value.
INTERPOLATION_MODES = {
    0: "Continuous",
    1: "StepwiseContinuousLeading",
    2: "StepwiseContinuousTrailing",
    3: "Discrete",
    4: "ContinuousNullableLeading",
    5: "ContinuousNullableTrailing",
}
EXTRAPOLATION_MODES = {0: "All", 1: "None", 2: "Forward", 3: "Backward"}

# SdsTypeCode values whose default, when SDS leaves the property out, is 0 or
# False. Nullable codes (100 and up) and strings default to null instead.
# /Types reports codes as integers; /Resolved spells them as names.
_BOOLEAN = (3, "Boolean")
_NUMERIC = (*range(4, 16), "Char", "SByte", "Byte", "Int16", "UInt16", "Int32", "UInt32",
            "Int64", "UInt64", "Single", "Double", "Decimal")

# What get_values can do with a stream. SDS samples and interpolates numbers and
# booleans only; everything else (strings above all: alarm messages, work
# orders, shift notes) is read as the events stored, and reported as text.
ValueType = Literal["number", "boolean", "text"]


class Namespace(TypedDict):
    Id: str
    Name: str
    Description: str
    Region: str
    State: int | str


class Stream(TypedDict):
    Id: str
    Name: str
    TypeId: str
    Description: str
    InterpolationMode: str
    ExtrapolationMode: str


class StreamDetail(Stream):
    ValueType: ValueType
    Tags: list[str]
    Metadata: dict[str, str]


class StreamValue(TypedDict):
    Timestamp: str
    Value: float | int | bool | str | None


def _enum(value: Any, names: dict[int, str]) -> str:
    return names.get(value, str(value)) if isinstance(value, int) else str(value)


def to_namespace(raw: dict) -> Namespace:
    return {
        "Id": raw["Id"],
        "Name": raw.get("Name", raw["Id"]),
        "Description": raw.get("Description", ""),
        "Region": raw.get("Region", ""),
        "State": raw.get("State", ""),
    }


TYPE_DEFAULT = "TypeDefault"


def to_stream(raw: dict) -> Stream:
    # Every field is always present: an MCP output schema has no room for a
    # missing key. SDS omits ExtrapolationMode when it is All (the default),
    # and InterpolationMode when the stream inherits its type's mode. A
    # resolved stream carries its type inline instead of a TypeId.
    return {
        "Id": raw["Id"],
        "Name": raw.get("Name", raw["Id"]),
        "TypeId": raw.get("TypeId") or raw.get("Type", {}).get("Id", ""),
        "Description": raw.get("Description", ""),
        "InterpolationMode": _enum(raw.get("InterpolationMode", TYPE_DEFAULT), INTERPOLATION_MODES),
        "ExtrapolationMode": _enum(raw.get("ExtrapolationMode", 0), EXTRAPOLATION_MODES),
    }


def to_stream_detail(resolved: dict, tags: list, metadata: dict) -> StreamDetail:
    """From a resolved stream (GET .../Streams/{id}/Resolved), which inlines its type."""
    out: StreamDetail = {**to_stream(resolved), "ValueType": value_property(resolved.get("Type", {}))[2],
                         "Tags": list(tags), "Metadata": {k: str(v) for k, v in metadata.items()}}
    if out["InterpolationMode"] == TYPE_DEFAULT:
        type_mode = resolved.get("Type", {}).get("InterpolationMode", 0)
        out["InterpolationMode"] = _enum(type_mode, INTERPOLATION_MODES)
    return out


def value_property(raw_type: dict) -> tuple[str, Any, ValueType]:
    """The property `get_values` reports as Value, what SDS means when it leaves it out,
    and its value type."""
    props = [p for p in raw_type.get("Properties", []) if not p.get("IsKey")]
    prop = next((p for p in props if p["Id"] == "Value"), props[0] if props else {"Id": "Value"})
    code = prop.get("SdsType", {}).get("SdsTypeCode")
    if code in _BOOLEAN:
        return prop["Id"], False, "boolean"
    if code in _NUMERIC:
        return prop["Id"], 0, "number"
    # A nullable code is its base code plus 100 (NullableDouble is 114); null is its default.
    base = code - 100 if isinstance(code, int) else code.removeprefix("Nullable") if isinstance(code, str) else None
    if base in _BOOLEAN:
        return prop["Id"], None, "boolean"
    if base in _NUMERIC:
        return prop["Id"], None, "number"
    return prop["Id"], None, "text"


def to_values(raw: list[dict], prop: str, default: Any) -> list[StreamValue]:
    """Fill in omitted defaults and drop duplicate timestamps, keeping SDS's time order."""
    seen: dict[str, StreamValue] = {}
    for event in raw:
        ts = event["Timestamp"]
        seen.setdefault(ts, {"Timestamp": ts, "Value": event.get(prop, default)})
    return list(seen.values())
