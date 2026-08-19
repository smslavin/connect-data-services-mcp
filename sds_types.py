"""
Typed response shapes for the AVEVA Connect Data Services (SDS) API.

These mirror the SDS REST response fields exactly, in both demo and live
mode, so each tool can advertise a real MCP `output_schema` instead of
returning unstructured text.
"""

from typing import TypedDict


class Namespace(TypedDict):
    Id: str
    Name: str
    Description: str
    Region: str
    State: str


class Stream(TypedDict):
    Id: str
    Name: str
    TypeId: str
    Description: str
    InterpolationMode: str
    ExtrapolationMode: str
    Tags: list[str]


class StreamValue(TypedDict):
    Timestamp: str
    Value: float | bool
