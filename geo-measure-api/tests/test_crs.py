from app.services.crs import utm_epsg


def test_utm_zones():
    assert utm_epsg(77.0, 11.0) == 32643      # India, N
    assert utm_epsg(-122.4, 37.8) == 32610    # San Francisco
    assert utm_epsg(151.2, -33.9) == 32756    # Sydney, S
    assert utm_epsg(0.0, 90.0) == 32661       # polar -> UPS north
    assert utm_epsg(180.0, 0.0) == 32660      # zone clamped to 60
