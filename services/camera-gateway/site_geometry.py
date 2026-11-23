"""Planar predicates used for site boundaries and normalized image coordinates."""

EPSILON = 1e-12


def cross(a, b, p):
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def on_segment(a, b, p):
    return abs(cross(a, b, p)) <= EPSILON and all(min(u, v) - EPSILON <= c <= max(u, v) + EPSILON for u, v, c in zip(a, b, p))


def contains(vertices, point):
    inside = False
    for a, b in zip(vertices, vertices[1:] + vertices[:1]):
        if on_segment(a, b, point):
            return True
        if (a[1] > point[1]) != (b[1] > point[1]) and point[0] < (b[0] - a[0]) * (point[1] - a[1]) / (b[1] - a[1]) + a[0]:
            inside = not inside
    return inside
