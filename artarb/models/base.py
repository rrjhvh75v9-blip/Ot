import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Index,
    Integer, Numeric, SmallInteger, String, Text, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from artarb.database import Base


class Artist(Base):
    __tablename__ = "artists"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rkd_id: Mapped[Optional[str]] = mapped_column(String(50), unique=True)
    name_canonical: Mapped[str] = mapped_column(String(500))
    name_variants: Mapped[Optional[dict]] = mapped_column(JSONB)
    nationality: Mapped[Optional[str]] = mapped_column(String(100))
    born: Mapped[Optional[int]] = mapped_column(SmallInteger)
    died: Mapped[Optional[int]] = mapped_column(SmallInteger)
    movement: Mapped[Optional[str]] = mapped_column(String(200))
    market_tier: Mapped[Optional[str]] = mapped_column(String(50))
    specialty_category: Mapped[Optional[str]] = mapped_column(String(50))
    arbitrage_notes: Mapped[Optional[str]] = mapped_column(Text)

    lots: Mapped[list["Lot"]] = relationship(back_populates="artist")
    search_trends: Mapped[list["SearchTrend"]] = relationship(back_populates="artist")
    market_events: Mapped[list["MarketEvent"]] = relationship(back_populates="artist")
    exhibitions: Mapped[list["Exhibition"]] = relationship(back_populates="artist")
    regional_price_indices: Mapped[list["RegionalPriceIndex"]] = relationship(back_populates="artist")
    opportunities: Mapped[list["Opportunity"]] = relationship(back_populates="artist")


class Lot(Base):
    __tablename__ = "lots"
    __table_args__ = (Index("ix_lots_artist_id", "artist_id"),)

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artist_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("artists.id"))
    source_platform: Mapped[str] = mapped_column(String(100))
    source_lot_id: Mapped[Optional[str]] = mapped_column(String(200))
    source_url: Mapped[Optional[str]] = mapped_column(String(2048))
    title: Mapped[Optional[str]] = mapped_column(String(1000))
    year_created: Mapped[Optional[int]] = mapped_column(SmallInteger)
    technique: Mapped[Optional[str]] = mapped_column(String(300))
    medium: Mapped[Optional[str]] = mapped_column(String(300))
    width_cm: Mapped[Optional[float]] = mapped_column(Numeric(8, 2))
    height_cm: Mapped[Optional[float]] = mapped_column(Numeric(8, 2))
    edition_type: Mapped[Optional[str]] = mapped_column(String(50))
    edition_number: Mapped[Optional[int]] = mapped_column(Integer)
    edition_total: Mapped[Optional[int]] = mapped_column(Integer)
    signed: Mapped[Optional[bool]] = mapped_column(Boolean)
    framed: Mapped[Optional[bool]] = mapped_column(Boolean)
    condition: Mapped[Optional[str]] = mapped_column(String(100))
    provenance_notes: Mapped[Optional[str]] = mapped_column(Text)
    catalogue_reference: Mapped[Optional[str]] = mapped_column(String(200))
    country_sale: Mapped[Optional[str]] = mapped_column(String(100))

    artist: Mapped["Artist"] = relationship(back_populates="lots")
    price_events: Mapped[list["PriceEvent"]] = relationship(back_populates="lot")
    listings: Mapped[list["Listing"]] = relationship(back_populates="lot")
    portfolio_entries: Mapped[list["Portfolio"]] = relationship(back_populates="lot")


class PriceEvent(Base):
    __tablename__ = "price_events"
    __table_args__ = (
        Index("ix_price_events_lot_id", "lot_id"),
        Index("ix_price_events_sale_date", "sale_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lot_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("lots.id"))
    platform: Mapped[str] = mapped_column(String(100))
    event_type: Mapped[str] = mapped_column(String(50))
    amount: Mapped[Optional[float]] = mapped_column(Numeric(14, 2))
    currency: Mapped[Optional[str]] = mapped_column(String(10))
    amount_eur: Mapped[Optional[float]] = mapped_column(Numeric(14, 2))
    buyer_premium_pct: Mapped[Optional[float]] = mapped_column(Numeric(6, 4))
    amount_with_premium: Mapped[Optional[float]] = mapped_column(Numeric(14, 2))
    sale_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    lot: Mapped["Lot"] = relationship(back_populates="price_events")


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (Index("ix_listings_lot_id", "lot_id"),)

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lot_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("lots.id"))
    platform: Mapped[str] = mapped_column(String(100))
    listing_url: Mapped[Optional[str]] = mapped_column(String(2048))
    status: Mapped[Optional[str]] = mapped_column(String(50))
    opening_bid: Mapped[Optional[float]] = mapped_column(Numeric(14, 2))
    current_bid: Mapped[Optional[float]] = mapped_column(Numeric(14, 2))
    bid_count: Mapped[Optional[int]] = mapped_column(Integer)
    sale_type: Mapped[Optional[str]] = mapped_column(String(50))
    ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    lot: Mapped["Lot"] = relationship(back_populates="listings")
    opportunities: Mapped[list["Opportunity"]] = relationship(back_populates="listing")


class SearchTrend(Base):
    __tablename__ = "search_trends"
    __table_args__ = (Index("ix_search_trends_artist_id", "artist_id"),)

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artist_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("artists.id"))
    week_start: Mapped[date] = mapped_column(Date)
    geo: Mapped[Optional[str]] = mapped_column(String(10))
    relative_interest: Mapped[Optional[int]] = mapped_column(SmallInteger)

    artist: Mapped["Artist"] = relationship(back_populates="search_trends")


class MarketEvent(Base):
    __tablename__ = "market_events"
    __table_args__ = (
        Index("ix_market_events_artist_id", "artist_id"),
        Index("ix_market_events_event_date", "event_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artist_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("artists.id"))
    event_type: Mapped[str] = mapped_column(String(100))
    event_date: Mapped[Optional[date]] = mapped_column(Date)
    source: Mapped[Optional[str]] = mapped_column(String(200))
    headline: Mapped[Optional[str]] = mapped_column(String(1000))
    url: Mapped[Optional[str]] = mapped_column(String(2048))
    price_impact_pct: Mapped[Optional[float]] = mapped_column(Numeric(7, 4))

    artist: Mapped["Artist"] = relationship(back_populates="market_events")


class Exhibition(Base):
    __tablename__ = "exhibitions"
    __table_args__ = (Index("ix_exhibitions_artist_id", "artist_id"),)

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artist_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("artists.id"))
    venue: Mapped[Optional[str]] = mapped_column(String(500))
    city: Mapped[Optional[str]] = mapped_column(String(200))
    country: Mapped[Optional[str]] = mapped_column(String(100))
    opens_at: Mapped[Optional[date]] = mapped_column(Date)
    closes_at: Mapped[Optional[date]] = mapped_column(Date)
    is_retrospective: Mapped[Optional[bool]] = mapped_column(Boolean)
    source_url: Mapped[Optional[str]] = mapped_column(String(2048))

    artist: Mapped["Artist"] = relationship(back_populates="exhibitions")


class RegionalPriceIndex(Base):
    __tablename__ = "regional_price_index"
    __table_args__ = (Index("ix_regional_price_index_artist_id", "artist_id"),)

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artist_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("artists.id"))
    country: Mapped[str] = mapped_column(String(100))
    platform: Mapped[str] = mapped_column(String(100))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    median_hammer: Mapped[Optional[float]] = mapped_column(Numeric(14, 2))
    sample_size: Mapped[Optional[int]] = mapped_column(Integer)
    sell_through_rate: Mapped[Optional[float]] = mapped_column(Numeric(5, 4))

    artist: Mapped["Artist"] = relationship(back_populates="regional_price_indices")


class Opportunity(Base):
    __tablename__ = "opportunities"
    __table_args__ = (
        Index("ix_opportunities_artist_id", "artist_id"),
        Index("ix_opportunities_listing_id", "listing_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    listing_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("listings.id"))
    artist_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("artists.id"))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    buy_platform: Mapped[Optional[str]] = mapped_column(String(100))
    buy_price_estimate: Mapped[Optional[float]] = mapped_column(Numeric(14, 2))
    sell_platform: Mapped[Optional[str]] = mapped_column(String(100))
    sell_price_estimate: Mapped[Optional[float]] = mapped_column(Numeric(14, 2))
    expected_profit: Mapped[Optional[float]] = mapped_column(Numeric(14, 2))
    confidence_score: Mapped[Optional[float]] = mapped_column(Numeric(5, 4))
    rationale: Mapped[Optional[dict]] = mapped_column(JSONB)
    status: Mapped[Optional[str]] = mapped_column(String(50))
    arbitrage_category: Mapped[Optional[str]] = mapped_column(String(500))
    actual_buy_price: Mapped[Optional[float]] = mapped_column(Numeric(14, 2))
    actual_sell_price: Mapped[Optional[float]] = mapped_column(Numeric(14, 2))
    actual_profit: Mapped[Optional[float]] = mapped_column(Numeric(14, 2))

    listing: Mapped["Listing"] = relationship(back_populates="opportunities")
    artist: Mapped["Artist"] = relationship(back_populates="opportunities")
    portfolio_entry: Mapped[Optional["Portfolio"]] = relationship(back_populates="opportunity")


class Portfolio(Base):
    __tablename__ = "portfolio"
    __table_args__ = (
        Index("ix_portfolio_opportunity_id", "opportunity_id"),
        Index("ix_portfolio_lot_id", "lot_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    opportunity_id: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("opportunities.id"))
    lot_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("lots.id"))
    purchased_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    purchase_price: Mapped[Optional[float]] = mapped_column(Numeric(14, 2))
    storage_location: Mapped[Optional[str]] = mapped_column(String(300))
    insurance_value: Mapped[Optional[float]] = mapped_column(Numeric(14, 2))
    sold_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    sale_price: Mapped[Optional[float]] = mapped_column(Numeric(14, 2))
    net_profit: Mapped[Optional[float]] = mapped_column(Numeric(14, 2))
    notes: Mapped[Optional[str]] = mapped_column(Text)

    opportunity: Mapped[Optional["Opportunity"]] = relationship(back_populates="portfolio_entry")
    lot: Mapped["Lot"] = relationship(back_populates="portfolio_entries")


class ScrapeLog(Base):
    __tablename__ = "scrape_log"
    __table_args__ = (Index("ix_scrape_log_scraped_at", "scraped_at"),)

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    platform: Mapped[str] = mapped_column(String(100))
    url: Mapped[Optional[str]] = mapped_column(String(2048))
    status_code: Mapped[Optional[int]] = mapped_column(SmallInteger)
    items_found: Mapped[Optional[int]] = mapped_column(Integer)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
