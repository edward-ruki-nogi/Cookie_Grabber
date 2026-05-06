from __future__ import annotations

import random


def bezier_curve_points(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    steps: int = 25,
) -> list[tuple[float, float]]:
    cx1 = x0 + (x1 - x0) * random.uniform(0.2, 0.8) + random.uniform(-40, 40)
    cy1 = y0 + (y1 - y0) * random.uniform(0.1, 0.6) + random.uniform(-40, 40)
    cx2 = x0 + (x1 - x0) * random.uniform(0.2, 0.8) + random.uniform(-40, 40)
    cy2 = y0 + (y1 - y0) * random.uniform(0.4, 0.9) + random.uniform(-40, 40)
    pts: list[tuple[float, float]] = []
    for i in range(steps + 1):
        t = i / steps
        omt = 1 - t
        x = (
            omt**3 * x0
            + 3 * omt**2 * t * cx1
            + 3 * omt * t**2 * cx2
            + t**3 * x1
        )
        y = (
            omt**3 * y0
            + 3 * omt**2 * t * cy1
            + 3 * omt * t**2 * cy2
            + t**3 * y1
        )
        pts.append((x, y))
    return pts
