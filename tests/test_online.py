import asyncio
import os

import pytest

from travel.providers import Providers


@pytest.mark.online
@pytest.mark.skipif(os.getenv("TRAVEL_LIVE_TEST") != "1", reason="Explicit live provider opt-in required")
def test_open_meteo_lisbon(tmp_path):
    city = asyncio.run(Providers(tmp_path, online=True).geocode("Lisbon"))
    assert city and city.country == "PT" and city.evidence.confidence == "provider"
