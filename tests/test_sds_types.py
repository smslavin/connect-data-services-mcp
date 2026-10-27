"""Conversion from raw SDS JSON, using response shapes recorded from a live namespace."""

from sds_types import to_namespace, to_stream, to_stream_detail, to_values, value_property

# Recorded 2026-10-23; IDs and dates replaced.
LIVE_NAMESPACE = {
    "Id": "ns-1", "Name": "Example", "Description": "Example namespace", "Region": "northeurope",
    "RegionId": "r-1", "InstanceId": "i-1", "State": 1, "AllowCrossRegionProcessing": False,
    "Self": "https://example/ns-1",
}
LIVE_STREAM = {
    "Id": "pg2027.P-101.XS-101.PV", "Name": "P-101 run status",
    "Description": "Pump P-101 running (1) or stopped (0).", "TypeId": "pg2027.TimeValue.Int32",
    "InterpolationMode": 1, "CreatedDate": "2026-01-01T00:00:00Z", "ModifiedDate": "2026-01-01T00:00:00Z",
}
LIVE_TYPE_INT32 = {
    "Id": "pg2027.TimeValue.Int32", "SdsTypeCode": 1, "Properties": [
        {"Id": "Timestamp", "Name": "Timestamp", "IsKey": True, "SdsType": {"SdsTypeCode": 16}},
        {"Id": "Value", "Name": "Value", "SdsType": {"SdsTypeCode": 9}},
    ],
}
# GET .../Streams/{id}/Resolved, recorded 2026-10-27 through a community.
# Codes and modes come back as names; the stream has no TypeId.
LIVE_RESOLVED = {
    "Resolved": True, "Id": "pg2027.P-101.FIT-101.PV", "Name": "P-101 discharge flow",
    "Description": "Pump P-101 discharge flow, magnetic flow meter.",
    "CreatedDate": "2026-01-01T00:00:00Z", "ModifiedDate": "2026-01-01T00:00:00Z",
    "Type": {
        "Id": "pg2027.TimeValue.Double", "Name": "Timestamp and float64 value", "SdsTypeCode": "Object",
        "ExtrapolationMode": "All", "InterpolationMode": "Continuous", "Properties": [
            {"Id": "Timestamp", "Name": "Timestamp", "IsKey": True, "SdsType": {
                "Properties": [], "SdsTypeCode": "DateTime", "ExtrapolationMode": "All",
                "InterpolationMode": "Continuous"}},
            {"Id": "Value", "Name": "Value", "SdsType": {
                "Properties": [], "SdsTypeCode": "Double", "ExtrapolationMode": "All",
                "InterpolationMode": "Continuous"}},
        ],
    },
}
# Interpolated read across a pump stop: the zero values have no Value key.
LIVE_VALUES = [
    {"Timestamp": "2027-06-15T09:00:00Z", "Value": 2},
    {"Timestamp": "2027-06-16T13:00:00Z"},
    {"Timestamp": "2027-06-21T05:00:00Z", "Value": 9},
]


def _type(code: int | str) -> dict:
    return {"Properties": [{"Id": "Timestamp", "IsKey": True, "SdsType": {"SdsTypeCode": 16}},
                           {"Id": "Value", "SdsType": {"SdsTypeCode": code}}]}


def test_namespace_keeps_integer_state():
    assert to_namespace(LIVE_NAMESPACE) == {
        "Id": "ns-1", "Name": "Example", "Description": "Example namespace",
        "Region": "northeurope", "State": 1}


def test_stream_names_integer_interpolation_mode():
    out = to_stream(LIVE_STREAM)
    assert out["InterpolationMode"] == "StepwiseContinuousLeading"
    assert out["ExtrapolationMode"] == "All"  # SDS leaves out the default


def test_stream_detail_carries_tags_and_metadata():
    out = to_stream_detail({**LIVE_STREAM, "Type": LIVE_TYPE_INT32}, ["pg2027", "P-101"],
                           {"EngineeringUnits": "", "Equipment": "P-101"})
    assert out["Tags"] == ["pg2027", "P-101"]
    assert out["Metadata"]["Equipment"] == "P-101"


def test_stream_without_mode_inherits_its_type():
    raw = {k: v for k, v in LIVE_STREAM.items() if k != "InterpolationMode"}
    assert to_stream(raw)["InterpolationMode"] == "TypeDefault"
    assert to_stream_detail({**raw, "Type": {**LIVE_TYPE_INT32, "InterpolationMode": 3}}, [], {}
                            )["InterpolationMode"] == "Discrete"
    assert to_stream_detail({**raw, "Type": LIVE_TYPE_INT32}, [], {})["InterpolationMode"] == "Continuous"


def test_resolved_stream_takes_type_id_and_mode_from_its_type():
    out = to_stream_detail(LIVE_RESOLVED, [], {})
    assert out["TypeId"] == "pg2027.TimeValue.Double"
    assert out["InterpolationMode"] == "Continuous"
    assert value_property(LIVE_RESOLVED["Type"]) == ("Value", 0)


def test_omitted_value_defaults_by_type():
    assert value_property(LIVE_TYPE_INT32) == ("Value", 0)
    assert value_property(_type(14)) == ("Value", 0)  # Double
    assert value_property(_type(3)) == ("Value", False)  # Boolean
    assert value_property(_type(114)) == ("Value", None)  # NullableDouble
    assert value_property(_type(18)) == ("Value", None)  # String
    assert value_property(_type("Int32")) == ("Value", 0)  # /Resolved spells codes as names
    assert value_property(_type("Boolean")) == ("Value", False)
    assert value_property(_type("String")) == ("Value", None)


def test_values_fill_omitted_zero():
    assert [v["Value"] for v in to_values(LIVE_VALUES, "Value", 0)] == [2, 0, 9]


def test_values_drop_duplicate_timestamps_in_order():
    raw = [{"Timestamp": "t1", "Value": 1}, {"Timestamp": "t1", "Value": 1}, {"Timestamp": "t2", "Value": 3}]
    assert to_values(raw, "Value", 0) == [{"Timestamp": "t1", "Value": 1}, {"Timestamp": "t2", "Value": 3}]
