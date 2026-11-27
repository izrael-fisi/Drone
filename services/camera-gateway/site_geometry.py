"""Planar predicates used for site boundaries and normalized image coordinates."""

EPSILON = 1e-12


def cross(a, b, p):
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def on_segment(a, b, p):
    return abs(cross(a, b, p)) <= EPSILON and all(min(u, v) - EPSILON <= c <= max(u, v) + EPSILON for u, v, c in zip(a, b, p))


def segments_intersect(a, b, c, d):
    ab = cross(a, b, c), cross(a, b, d)
    cd = cross(c, d, a), cross(c, d, b)
    return (ab[0] * ab[1] < 0 and cd[0] * cd[1] < 0
            or any(on_segment(x, y, point) for x, y, point in ((a, b, c), (a, b, d), (c, d, a), (c, d, b))))


def contains(vertices, point):
    inside = False
    for a, b in zip(vertices, vertices[1:] + vertices[:1]):
        if on_segment(a, b, point):
            return True
        if (a[1] > point[1]) != (b[1] > point[1]) and point[0] < (b[0] - a[0]) * (point[1] - a[1]) / (b[1] - a[1]) + a[0]:
            inside = not inside
    return inside
