from __future__ import annotations

import math


def snap_point(x: float, y: float, kind: str, cell: float, width: float, height: float) -> tuple[float, float]:
    cell = max(12.0, float(cell))
    if kind == "Square":
        sx = round((x - cell / 2.0) / cell) * cell + cell / 2.0
        sy = round((y - cell / 2.0) / cell) * cell + cell / 2.0
    elif kind == "Hex":
        r = cell / 2.0
        hh = math.sqrt(3.0) * r
        approx_col = int(round((x - r) / max(1.0, 1.5 * r)))
        best: tuple[float, float, float] | None = None
        for col in range(max(0, approx_col - 2), approx_col + 3):
            cx = r + col * 1.5 * r
            offset = hh / 2.0 if col % 2 else 0.0
            approx_row = int(round((y - hh / 2.0 - offset) / max(1.0, hh)))
            for row in range(max(0, approx_row - 2), approx_row + 3):
                cy = hh / 2.0 + offset + row * hh
                distance = (cx - x) ** 2 + (cy - y) ** 2
                if best is None or distance < best[0]:
                    best = (distance, cx, cy)
        if best is None:
            sx, sy = x, y
        else:
            _, sx, sy = best
    else:
        sx, sy = x, y
    return max(0.0, min(float(width), sx)), max(0.0, min(float(height), sy))
