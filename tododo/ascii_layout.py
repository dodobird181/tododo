from __future__ import annotations
import re
from dataclasses import dataclass
from fractions import Fraction


@dataclass
class RowCol:
    row: int
    col: int
    field: str
    width_ratio: int
    row_span: int = 1
    col_span: int = 1


def parse_ascii_grid(layout: str) -> list[RowCol]:
    """
    Parse an ASCII grid layout into RowCol dataclasses.

    Digits embedded in horizontal fence segments (e.g. +-----1------+---4---+)
    define column width ratios. All rows are normalised to the same total width,
    which is the sum of ratios on the most-annotated fence line.

    Unannotated fence lines (closing fences with no digits) have their column
    ratios inferred by mapping their '+' x-positions onto the coordinate space
    established by the annotated reference fence.

    Example input:
        +-----1------+---4-----------------+
        | created_by | title               |
      +-----1----------------------------+
      | description                      |
      +-----1----+-----1---+------1------+
      | due_date |         | assigned_to |
      +--------------------+-------------+
    """
    lines = layout.split("\n")

    def is_fence(line: str) -> bool:
        s = line.strip()
        return bool(s) and s[0] == "+" and all(c in "+- 0123456789" for c in s)

    fence_ys = [i for i, ln in enumerate(lines) if is_fence(ln)]
    if len(fence_ys) < 2:
        raise ValueError("Need at least 2 fence lines to define a row.")

    def plus_positions(line: str) -> list[int]:
        return [x for x, c in enumerate(line) if c == "+"]

    def parse_fence_ratios(line: str) -> list[int]:
        xs = plus_positions(line)
        ratios = []
        for i in range(len(xs) - 1):
            seg = line[xs[i]:xs[i + 1]]
            m = re.search(r"\d+", seg)
            ratios.append(int(m.group()) if m else 1)
        return ratios

    # Pick the annotated fence with the highest ratio sum as the reference.
    fence_lines = [lines[y] for y in fence_ys]
    annotated = [fl for fl in fence_lines if re.search(r"\d", fl)]
    if not annotated:
        raise ValueError("No fence lines with digit annotations found.")

    ref_fence = max(annotated, key=lambda fl: sum(parse_fence_ratios(fl)))
    ref_xs = plus_positions(ref_fence)
    ref_ratios = parse_fence_ratios(ref_fence)
    total_width = sum(ref_ratios)

    all_left_edges = [plus_positions(fl)[0] for fl in fence_lines if plus_positions(fl)]
    global_left = min(all_left_edges)
    all_right_edges = [plus_positions(fl)[-1] for fl in fence_lines if plus_positions(fl)]
    global_right = max(all_right_edges)

    cum: dict[int, Fraction] = {}
    acc = Fraction(0)
    for i, r in enumerate(ref_ratios):
        cum[ref_xs[i]] = acc
        acc += Fraction(r)
    cum[ref_xs[-1]] = acc

    ref_left = ref_xs[0]
    ref_right = ref_xs[-1]

    anchors: list[tuple[int, Fraction]] = sorted(cum.items())
    if global_left < ref_left:
        anchors = [(global_left, Fraction(0))] + anchors
    if global_right > ref_right:
        anchors = anchors + [(global_right, Fraction(total_width))]

    anchor_xs = [a[0] for a in anchors]
    anchor_rs = [a[1] for a in anchors]

    def x_to_ratio(x: int) -> Fraction:
        if x in cum:
            return cum[x]
        for i in range(len(anchor_xs) - 1):
            x1, x2 = anchor_xs[i], anchor_xs[i + 1]
            if x1 <= x <= x2:
                t = Fraction(x - x1, x2 - x1)
                return anchor_rs[i] + t * (anchor_rs[i + 1] - anchor_rs[i])
        raise ValueError(f"x={x} is outside known grid boundaries [{anchor_xs[0]}, {anchor_xs[-1]}]")

    results: list[RowCol] = []

    for fi in range(len(fence_ys) - 1):
        y1 = fence_ys[fi]
        top_fence = lines[y1]

        col_xs = plus_positions(top_fence)
        if len(col_xs) < 2:
            continue

        content_line: str | None = None
        for y in range(y1 + 1, fence_ys[fi + 1]):
            if "|" in lines[y]:
                content_line = lines[y]
                break
        if content_line is None:
            continue

        visited: set[int] = set()
        col_index = 0

        for ci in range(len(col_xs) - 1):
            if ci in visited:
                col_index += 1
                continue

            x1 = col_xs[ci]

            colspan = 1
            while ci + colspan < len(col_xs) - 1:
                bx = col_xs[ci + colspan]
                ch = content_line[bx] if bx < len(content_line) else " "
                if ch == "|":
                    break
                colspan += 1

            x_end = col_xs[ci + colspan]

            r_start = x_to_ratio(x1)
            r_end = x_to_ratio(x_end)
            width_frac = r_end - r_start
            width_ratio = (
                width_frac.numerator
                if width_frac.denominator == 1
                else round(float(width_frac))
            )

            label = content_line[x1 + 1: x_end].strip()

            results.append(RowCol(
                row=fi,
                col=col_index,
                field=label,
                width_ratio=width_ratio,
                row_span=1,
                col_span=colspan,
            ))

            for dc in range(colspan):
                visited.add(ci + dc)
            col_index += 1

    return results
