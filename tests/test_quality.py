import pytest

pytest.importorskip("cv2")

from vision_nav.quality import estimate_visual_position_confidence


def test_visual_position_confidence_combines_match_and_georef_quality():
    assert estimate_visual_position_confidence(0.8, 0.95) == 0.76
    assert estimate_visual_position_confidence(1.2, None) == 1.0
    assert estimate_visual_position_confidence(0.8, -1.0) == 0.0
