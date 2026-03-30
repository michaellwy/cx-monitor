#!/usr/bin/env python3
"""Patch the fli library to support currency, country, and aircraft type extraction."""

import os
import fli

SITE = os.path.dirname(fli.__file__)

# --- Patch SearchFlights for currency/country/language params ---
flights_path = os.path.join(SITE, "search", "flights.py")
with open(flights_path) as f:
    src = f.read()

if "currency" not in src:
    # Add __init__ with currency/country/language
    src = src.replace(
        "class SearchFlights(BaseSearch):",
        '''class SearchFlights(BaseSearch):
    def __init__(self, currency="USD", country="us", language="en"):
        super().__init__()
        self.currency = currency
        self.country = country
        self.language = language
''',
    )
    # Patch URL to include params
    src = src.replace(
        "self.BASE_URL",
        'f"{self.BASE_URL}?hl={self.language}&gl={self.country}&curr={self.currency}"',
        1,
    )
    # Add aircraft extraction
    src = src.replace(
        "duration=fl[11],",
        'duration=fl[11],\n                    aircraft=fl[17] if len(fl) > 17 and isinstance(fl[17], str) else None,',
    )
    with open(flights_path, "w") as f:
        f.write(src)
    print("Patched flights.py")
else:
    print("flights.py already patched")

# --- Patch FlightLeg model to include aircraft field ---
base_path = os.path.join(SITE, "models", "google_flights", "base.py")
with open(base_path) as f:
    src2 = f.read()

if "aircraft" not in src2:
    src2 = src2.replace(
        "duration: PositiveInt  # in minutes",
        "duration: PositiveInt  # in minutes\n    aircraft: str | None = None",
    )
    with open(base_path, "w") as f:
        f.write(src2)
    print("Patched base.py")
else:
    print("base.py already patched")
