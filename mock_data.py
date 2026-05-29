"""
Mock data for the Aveva Water Authority (AWA) demo namespace.

Generates 48 hours of realistic process historian data at 5-minute intervals
using bounded random walks. Data is deterministic — the same server start
always produces the same value pattern — so demos and screenshots are
reproducible.

The simulated scenario:
  - Normal diurnal flow variation at the plant inlet (FIT-101)
  - Stable dissolved oxygen in the aeration basin (AIT-301)
  - Filter 1 head loss drifting toward the 8.0 ft backwash threshold (PDT-401)
  - Effluent turbidity spike ~6 hours ago, now recovered (AIT-501)
  - Chlorine dosing correlated with influent flow (FIC-601)
  - Transfer pump 201 tripped briefly during the turbidity event (P-201)

Return shapes mirror AVEVA Connect Data Services SDS API responses exactly,
so server.py works identically in demo mode and live mode.

RandomWalk and OscillatingBool are adapted from
graccess-mcp/simulator/mqtt_simulator.py.
"""

import hashlib
import math
import random
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Namespace definition
# ---------------------------------------------------------------------------

TENANT_ID = "aveva-water-authority"

NAMESPACE = {
    "Id": "AWA-Production",
    "Name": "AWA Production",
    "Description": "Aveva Water Authority — production process namespace",
    "Region": "westus",
    "State": "Active",
}

# ---------------------------------------------------------------------------
# Stream definitions
# ---------------------------------------------------------------------------

STREAMS = [
    {
        "Id": "FIT-101.PV",
        "Name": "Influent Flow Rate",
        "TypeId": "PI-Float32",
        "Description": "Raw influent flow rate at plant inlet. Normal range: 1800–3600 gpm.",
        "InterpolationMode": "Continuous",
        "ExtrapolationMode": "None",
        "Tags": ["Influent", "Flow", "gpm"],
    },
    {
        "Id": "AIT-301.PV",
        "Name": "Aeration Basin DO",
        "TypeId": "PI-Float32",
        "Description": "Dissolved oxygen in aeration basin 1. Target: 2.0–4.0 mg/L.",
        "InterpolationMode": "Continuous",
        "ExtrapolationMode": "None",
        "Tags": ["Secondary", "DO", "mg/L"],
    },
    {
        "Id": "PDT-401.PV",
        "Name": "Filter 1 Head Loss",
        "TypeId": "PI-Float32",
        "Description": (
            "Differential pressure across sand filter bed 1. "
            "Last backwash: 48 hours ago. Backwash triggered at 8.0 ft."
        ),
        "InterpolationMode": "Continuous",
        "ExtrapolationMode": "None",
        "Tags": ["Filtration", "Pressure", "ft"],
    },
    {
        "Id": "AIT-501.PV",
        "Name": "Effluent Turbidity",
        "TypeId": "PI-Float32",
        "Description": (
            "Treated effluent turbidity at plant outlet. "
            "Permit limit: 1.0 NTU. Normal: < 0.3 NTU."
        ),
        "InterpolationMode": "Continuous",
        "ExtrapolationMode": "None",
        "Tags": ["Effluent", "Turbidity", "NTU"],
    },
    {
        "Id": "FIC-601.PV",
        "Name": "Chlorine Dosing Rate",
        "TypeId": "PI-Float32",
        "Description": "Chlorine feed rate for final disinfection, scaled to influent flow. Units: lb/day.",
        "InterpolationMode": "Continuous",
        "ExtrapolationMode": "None",
        "Tags": ["Disinfection", "Chemical", "lb/day"],
    },
    {
        "Id": "P-201.RunStatus",
        "Name": "Transfer Pump 201 Run Status",
        "TypeId": "PI-Boolean",
        "Description": "Run/stop status of secondary transfer pump 201. True = running.",
        "InterpolationMode": "StepwiseContinuousLeading",
        "ExtrapolationMode": "None",
        "Tags": ["Pump", "Status"],
    },
]

# ---------------------------------------------------------------------------
# Value generators (adapted from graccess-mcp/simulator/mqtt_simulator.py)
# ---------------------------------------------------------------------------

class RandomWalk:
    """Bounded random walk. Stays within [lo, hi] with steps of ±step."""

    def __init__(self, initial: float, lo: float, hi: float, step: float):
        self.value = float(initial)
        self.lo = lo
        self.hi = hi
        self.step = step

    def next(self) -> float:
        self.value += random.uniform(-self.step, self.step)
        self.value = max(self.lo, min(self.hi, self.value))
        return round(self.value, 3)


class OscillatingBool:
    """Boolean that flips occasionally at random."""

    def __init__(self, initial: bool = True, flip_chance: float = 0.005):
        self.value = initial
        self.flip_chance = flip_chance

    def next(self) -> bool:
        if random.random() < self.flip_chance:
            self.value = not self.value
        return self.value


# Hourly flow factor: index = hour of day, 0 = trough at 2am, 1 = peak at 7am
_DIURNAL = [
    0.30, 0.25, 0.20, 0.25, 0.35, 0.55,   # midnight–5am
    0.75, 1.00, 0.95, 0.90, 0.85, 0.82,   # 6am–11am
    0.80, 0.78, 0.75, 0.72, 0.70, 0.72,   # noon–5pm
    0.75, 0.70, 0.65, 0.55, 0.45, 0.35,   # 6pm–11pm
]


def _generate_series(stream_id: str, timestamps: list[datetime]) -> list[dict]:
    """
    Generate a deterministic time series for one stream.

    Seeding random from the stream ID ensures each stream has its own
    independent value history, and the same stream always produces the
    same pattern across server restarts.
    """
    # Use a stable hash (not Python's hash(), which is randomized per process)
    seed = int(hashlib.md5(stream_id.encode()).hexdigest()[:8], 16)
    random.seed(seed)

    n = len(timestamps)
    now = timestamps[-1]
    results = []

    if stream_id == "FIT-101.PV":
        # Influent flow: diurnal pattern, 1800–3600 gpm
        walk = RandomWalk(2600.0, 1800.0, 3600.0, 30.0)
        walk.value = random.uniform(2400.0, 2800.0)
        for ts in timestamps:
            base = 1800.0 + 1800.0 * _DIURNAL[ts.hour]
            walk.lo = base * 0.85
            walk.hi = base * 1.15
            results.append({"Timestamp": _fmt(ts), "Value": walk.next()})

    elif stream_id == "AIT-301.PV":
        # Dissolved oxygen: stable 2–4 mg/L, slight dip in afternoon
        walk = RandomWalk(3.0, 1.5, 4.5, 0.04)
        walk.value = random.uniform(2.8, 3.2)
        for ts in timestamps:
            afternoon_dip = 0.5 if 12 <= ts.hour <= 18 else 0.0
            walk.lo = 1.5 - afternoon_dip
            walk.hi = 4.5 - afternoon_dip
            results.append({"Timestamp": _fmt(ts), "Value": walk.next()})

    elif stream_id == "PDT-401.PV":
        # Filter head loss: linear drift from 1.5 ft → 7.2 ft over 48 hours
        # Last backwash 48 hours ago reset it to ~1.5 ft.
        walk = RandomWalk(1.5, 0.5, 8.5, 0.06)
        walk.value = 1.5
        for i, ts in enumerate(timestamps):
            drift = 5.9 * (i / n)
            walk.lo = max(0.5, drift + 0.8)
            walk.hi = min(8.5, drift + 2.2)
            walk.value = max(walk.value, walk.lo)
            results.append({"Timestamp": _fmt(ts), "Value": walk.next()})

    elif stream_id == "AIT-501.PV":
        # Effluent turbidity: clean (< 0.3 NTU) except for a permit-limit
        # exceedance ~6–7.5 hours ago that lasted ~90 minutes then recovered.
        spike_start = now - timedelta(hours=7)
        spike_end = now - timedelta(hours=5, minutes=30)
        walk = RandomWalk(0.15, 0.03, 0.35, 0.008)
        walk.value = random.uniform(0.10, 0.18)
        for ts in timestamps:
            if spike_start <= ts <= spike_end:
                progress = (ts - spike_start).total_seconds() / (spike_end - spike_start).total_seconds()
                # Peak of ~1.8 NTU at midpoint of spike window
                spike_val = 0.3 + 1.5 * math.sin(math.pi * progress)
                walk.value = round(spike_val + random.uniform(-0.04, 0.04), 3)
                walk.lo, walk.hi = 0.03, 2.0
            else:
                walk.lo, walk.hi = 0.03, 0.35
                walk.value = min(walk.value, walk.hi)
            results.append({"Timestamp": _fmt(ts), "Value": walk.next()})

    elif stream_id == "FIC-601.PV":
        # Chlorine dosing: tracks influent flow diurnal (same shape, different scale)
        walk = RandomWalk(130.0, 60.0, 220.0, 4.0)
        walk.value = random.uniform(110.0, 145.0)
        for ts in timestamps:
            base = 60.0 + 155.0 * _DIURNAL[ts.hour]
            walk.lo = base * 0.88
            walk.hi = base * 1.12
            results.append({"Timestamp": _fmt(ts), "Value": walk.next()})

    elif stream_id == "P-201.RunStatus":
        # Transfer pump: normally runs continuously.
        # Scripted stops: tripped during the turbidity event, plus one routine
        # maintenance window 24 hours prior.
        trip_start = now - timedelta(hours=7)
        trip_end   = now - timedelta(hours=6, minutes=30)
        maint_start = now - timedelta(hours=24)
        maint_end   = now - timedelta(hours=23, minutes=45)
        for ts in timestamps:
            running = not (trip_start <= ts <= trip_end or maint_start <= ts <= maint_end)
            results.append({"Timestamp": _fmt(ts), "Value": running})

    return results


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Module-level cache — generated once at import time
# ---------------------------------------------------------------------------

_INTERVAL_MINUTES = 5
_HISTORY_HOURS = 48
_N_POINTS = (_HISTORY_HOURS * 60) // _INTERVAL_MINUTES  # 576 points

_cache: dict[str, list[dict]] = {}


def _build_cache() -> None:
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    now -= timedelta(minutes=now.minute % _INTERVAL_MINUTES)
    timestamps = [now - timedelta(minutes=_INTERVAL_MINUTES * i) for i in range(_N_POINTS, 0, -1)]
    for stream in STREAMS:
        _cache[stream["Id"]] = _generate_series(stream["Id"], timestamps)


_build_cache()

# ---------------------------------------------------------------------------
# Public API — shapes match SDS REST responses
# ---------------------------------------------------------------------------

def get_namespaces() -> list[dict]:
    return [NAMESPACE]


def get_streams(query: str = "") -> list[dict]:
    if not query:
        return STREAMS
    q = query.lower()
    return [
        s for s in STREAMS
        if q in s["Id"].lower()
        or q in s["Name"].lower()
        or q in s["Description"].lower()
        or any(q in tag.lower() for tag in s["Tags"])
    ]


def get_stream(stream_id: str) -> dict | None:
    return next((s for s in STREAMS if s["Id"] == stream_id), None)


def get_values(stream_id: str, start: str, end: str, count: int = 100) -> list[dict]:
    """Return up to `count` evenly-sampled values within [start, end]."""
    series = _cache.get(stream_id, [])
    window = [pt for pt in series if start <= pt["Timestamp"] <= end]
    if len(window) <= count:
        return window
    # Evenly downsample to the requested count
    step = len(window) / count
    return [window[int(i * step)] for i in range(count)]
