from __future__ import annotations


REQUIRED_FIELD_CONDITIONS = [
    "good_texture",
    "low_texture",
    "blur",
    "seasonal_change",
    "lighting_change",
    "altitude_scale_change",
    "repeated_patterns",
    "wrong_map",
]


OPTIONAL_FIELD_CONDITIONS = [
    "quad_vibration",
    "thermal_or_eo_future",
    "long_fix_interval",
    "map_age_or_terrain_change",
]


FIELD_CONDITION_EXPECTED_BEHAVIOR = {
    "good_texture": "good_map",
    "low_texture": "degraded",
    "blur": "degraded",
    "seasonal_change": "degraded",
    "lighting_change": "degraded",
    "altitude_scale_change": "degraded",
    "repeated_patterns": "degraded",
    "wrong_map": "wrong_map",
    "quad_vibration": "degraded",
    "thermal_or_eo_future": "degraded",
    "long_fix_interval": "degraded",
    "map_age_or_terrain_change": "degraded",
}


FIELD_CONDITION_LABELS = {
    "good_texture": "Good texture, matching map",
    "low_texture": "Low texture",
    "blur": "Motion blur or defocus",
    "seasonal_change": "Seasonal or map-age change",
    "lighting_change": "Lighting, shadow, or exposure change",
    "altitude_scale_change": "Altitude or visual-scale change",
    "repeated_patterns": "Repeated terrain or structure patterns",
    "wrong_map": "Wrong-map rejection",
    "quad_vibration": "Quad vibration or mount blur",
    "thermal_or_eo_future": "Thermal or EO future sensor profile",
    "long_fix_interval": "Long interval between accepted vision fixes",
    "map_age_or_terrain_change": "Map age or terrain change",
}


FIELD_CONDITION_NOTES = {
    "good_texture": "Matching map with clear terrain texture and nominal lighting.",
    "low_texture": "Matching map with weak texture; weak fixes should be rejected or carry inflated covariance.",
    "blur": "Matching map with motion blur, defocus, or rolling exposure; unsafe fixes should be rejected.",
    "seasonal_change": "Matching map with seasonal, vegetation, construction, or map-age differences.",
    "lighting_change": "Matching map with meaningful lighting, shadow, glare, or exposure differences.",
    "altitude_scale_change": "Matching map with flight altitude or visual-scale variation from the nominal bundle.",
    "repeated_patterns": "Matching map with repeated structures, rows, lots, roofs, or other ambiguity-prone patterns.",
    "wrong_map": "Nonmatching map or deliberately shifted area; accepted rate should remain zero by default.",
    "quad_vibration": "Prop-off and future prop-on capture with vibration, rolling blur, and mount rigidity notes.",
    "thermal_or_eo_future": "Metadata-ready non-RGB capture path; v1 should mark unsupported or degraded rather than silently accepting.",
    "long_fix_interval": "Runtime segment with intentionally sparse accepted fixes; dead-reckoning gaps and covariance growth should be visible.",
    "map_age_or_terrain_change": "Older or changed map area with construction, vegetation, or seasonal differences.",
}


def expected_behavior_for_condition(condition: str) -> str:
    return FIELD_CONDITION_EXPECTED_BEHAVIOR.get(condition, "degraded")


def label_for_condition(condition: str) -> str:
    return FIELD_CONDITION_LABELS.get(condition, condition.replace("_", " ").title())


def notes_for_condition(condition: str) -> str:
    return FIELD_CONDITION_NOTES.get(condition, "")
