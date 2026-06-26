from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


class GeocodeCache(Base):
    """Cache of city/state -> lat/lon lookups against Nominatim.

    Negative results (city couldn't be geocoded) are stored with NULL
    latitude/longitude so we don't keep hammering the service for a
    misspelling. Re-queries are gated by ``fetched_at`` only for misses;
    successful resolutions are considered stable.
    """

    __tablename__ = "geocode_cache"
    __table_args__ = (
        UniqueConstraint("city_norm", "state_norm", "country_norm", name="ux_geocode_cache_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # Normalized lookup keys (lowercased, stripped). Originals are kept
    # for display/debugging.
    city_norm: Mapped[str] = mapped_column(String(255), nullable=False)
    state_norm: Mapped[str] = mapped_column(String(100), nullable=False)
    country_norm: Mapped[str] = mapped_column(String(64), nullable=False)
    city: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(100), nullable=False)
    country: Mapped[str] = mapped_column(String(64), nullable=False)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReverseGeocodeCache(Base):
    """Cache of (lat, lon) -> closest populated place lookups against Overpass.

    Lat/lon are bucketed to two decimals (~1 km) as integer hundredths so
    the same operator's repeat check-ins all hit a single row regardless
    of GPS noise. Negative results (no nearby place found) are stored
    with NULL city/state and re-tried after the TTL.
    """

    __tablename__ = "reverse_geocode_cache"
    __table_args__ = (
        UniqueConstraint("lat_key", "lon_key", name="ux_reverse_geocode_cache_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    lat_key: Mapped[int] = mapped_column(Integer, nullable=False)
    lon_key: Mapped[int] = mapped_column(Integer, nullable=False)
    city: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
