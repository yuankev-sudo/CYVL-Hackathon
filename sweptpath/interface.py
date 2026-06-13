"""
Shared interface for swept-path backends.
Both shapely_backend and autodesk_backend implement `compute_swept_path`.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from geometry.extractor import IntersectionGeometry


class Verdict(str, Enum):
    PASS = "pass"
    TIGHT = "tight"
    FAIL = "fail"


@dataclass
class SweptPathResult:
    intersection_id: str
    vehicle_id: str
    verdict: Verdict
    reason: str | None
    swept_polygon: list[tuple[float, float]] | None  # GeoJSON-ready coords
    clearance_margin_ft: float | None                # positive = clear, negative = overlap


class SweptPathBackend(Protocol):
    def compute_swept_path(
        self,
        geometry: IntersectionGeometry,
        vehicle: dict,
        turn_angle_deg: float = 90.0,
    ) -> SweptPathResult: ...
