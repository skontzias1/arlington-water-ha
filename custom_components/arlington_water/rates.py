"""Arlington County residential rates. No Home Assistant code here.

https://www.arlingtonva.us/Government/Programs/Water-Utilities/Customer-Service/Rates
"""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Rates:
    """One rate year. Per quarter, or per 1,000 gallons."""

    start: date
    through: date
    water_base: float
    sewer_base: float
    refuse: float
    tier_1: float
    tier_2: float
    sewer: float


RATES = (
    Rates(
        date(2025, 7, 1), date(2026, 6, 30), 16.82, 13.98, 415.75 / 4, 4.54, 7.27, 10.48
    ),
    Rates(
        date(2026, 7, 1), date(2027, 6, 30), 17.51, 13.98, 439.27 / 4, 4.73, 7.57, 10.48
    ),
)
TIER_1_TG = 9.0


def current(day: date) -> Rates:
    """Return the latest rates that have started."""
    return next((r for r in reversed(RATES) if r.start <= day), RATES[0])


def next_gallon(rates: Rates, gallons: float, winter_tg: float | None) -> float:
    """Price of the next gallon, including sewer while it still depends on use."""
    tg = gallons / 1000
    price = rates.tier_1 if tg < TIER_1_TG else rates.tier_2
    if winter_tg is None or tg < winter_tg:
        price += rates.sewer
    return round(price / 1000, 6)


def estimate(
    rates: Rates, gallons: float, winter_tg: float | None, refuse: bool
) -> float:
    """Estimated bill so far: use plus the full quarter's fixed charges."""
    tg = gallons / 1000
    low = min(tg, TIER_1_TG)
    sewer = tg if winter_tg is None else min(tg, winter_tg)
    total = (
        low * rates.tier_1
        + (tg - low) * rates.tier_2
        + sewer * rates.sewer
        + rates.water_base
        + rates.sewer_base
    )
    return round(total + (rates.refuse if refuse else 0.0), 2)
