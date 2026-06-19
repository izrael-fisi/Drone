from vision_nav.georef import SimpleGeoReference, build_georef_from_cli, georef_from_json, georef_to_json


def test_pixel_to_local_default_north_up_axes():
    georef = SimpleGeoReference(origin_lat=40.0, origin_lon=-75.0, gsd_m=0.5)

    east_m, north_m = georef.pixel_to_local_m(10, 8)

    assert east_m == 5.0
    assert north_m == -4.0


def test_georef_json_round_trip():
    georef = SimpleGeoReference(
        origin_lat=40.0,
        origin_lon=-75.0,
        gsd_m=0.25,
        origin_pixel_x=100,
        origin_pixel_y=200,
        rotation_deg=10,
    )

    decoded = georef_from_json(georef_to_json(georef))

    assert decoded == georef


def test_build_georef_requires_core_fields_together():
    try:
        build_georef_from_cli(
            origin_lat=40.0,
            origin_lon=None,
            gsd_m=0.25,
            origin_pixel_x=0,
            origin_pixel_y=0,
            rotation_deg=0,
        )
    except ValueError as exc:
        assert "must be provided together" in str(exc)
    else:
        raise AssertionError("Expected incomplete georef arguments to fail")

