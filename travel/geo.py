from math import asin, cos, radians, sin, sqrt


def haversine(lat1, lon1, lat2, lon2):
    """Great-circle distance in kilometers."""
    latitude1, latitude2 = radians(lat1), radians(lat2)
    latitude_delta = latitude2 - latitude1
    longitude_delta = radians(lon2 - lon1)
    chord = sin(latitude_delta / 2) ** 2 + cos(latitude1) * cos(latitude2) * sin(longitude_delta / 2) ** 2
    return 12742 * asin(sqrt(min(1, chord)))
