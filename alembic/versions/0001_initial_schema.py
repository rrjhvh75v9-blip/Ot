"""Initial schema — all artarb tables.

Revision ID: 0001
Revises:
Create Date: 2026-05-25

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------ artists
    op.create_table(
        "artists",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("rkd_id", sa.String(50), nullable=True, unique=True),
        sa.Column("name_canonical", sa.String(500), nullable=False),
        sa.Column("name_variants", postgresql.JSONB(), nullable=True),
        sa.Column("nationality", sa.String(100), nullable=True),
        sa.Column("born", sa.SmallInteger(), nullable=True),
        sa.Column("died", sa.SmallInteger(), nullable=True),
        sa.Column("movement", sa.String(200), nullable=True),
        sa.Column("market_tier", sa.String(50), nullable=True),
    )

    # --------------------------------------------------------------------- lots
    op.create_table(
        "lots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "artist_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artists.id"),
            nullable=False,
        ),
        sa.Column("source_platform", sa.String(100), nullable=False),
        sa.Column("source_lot_id", sa.String(200), nullable=True),
        sa.Column("source_url", sa.String(2048), nullable=True),
        sa.Column("title", sa.String(1000), nullable=True),
        sa.Column("year_created", sa.SmallInteger(), nullable=True),
        sa.Column("technique", sa.String(300), nullable=True),
        sa.Column("medium", sa.String(300), nullable=True),
        sa.Column("width_cm", sa.Numeric(8, 2), nullable=True),
        sa.Column("height_cm", sa.Numeric(8, 2), nullable=True),
        sa.Column("edition_type", sa.String(50), nullable=True),
        sa.Column("edition_number", sa.Integer(), nullable=True),
        sa.Column("edition_total", sa.Integer(), nullable=True),
        sa.Column("signed", sa.Boolean(), nullable=True),
        sa.Column("framed", sa.Boolean(), nullable=True),
        sa.Column("condition", sa.String(100), nullable=True),
        sa.Column("provenance_notes", sa.Text(), nullable=True),
        sa.Column("catalogue_reference", sa.String(200), nullable=True),
        sa.Column("country_sale", sa.String(100), nullable=True),
    )
    op.create_index("ix_lots_artist_id", "lots", ["artist_id"])

    # ------------------------------------------------------------ price_events
    op.create_table(
        "price_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "lot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("lots.id"),
            nullable=False,
        ),
        sa.Column("platform", sa.String(100), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("currency", sa.String(10), nullable=True),
        sa.Column("amount_eur", sa.Numeric(14, 2), nullable=True),
        sa.Column("buyer_premium_pct", sa.Numeric(6, 4), nullable=True),
        sa.Column("amount_with_premium", sa.Numeric(14, 2), nullable=True),
        sa.Column("sale_date", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_price_events_lot_id", "price_events", ["lot_id"])
    op.create_index("ix_price_events_sale_date", "price_events", ["sale_date"])

    # -------------------------------------------------------------- listings
    op.create_table(
        "listings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "lot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("lots.id"),
            nullable=False,
        ),
        sa.Column("platform", sa.String(100), nullable=False),
        sa.Column("listing_url", sa.String(2048), nullable=True),
        sa.Column("status", sa.String(50), nullable=True),
        sa.Column("opening_bid", sa.Numeric(14, 2), nullable=True),
        sa.Column("current_bid", sa.Numeric(14, 2), nullable=True),
        sa.Column("bid_count", sa.Integer(), nullable=True),
        sa.Column("sale_type", sa.String(50), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "discovered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_listings_lot_id", "listings", ["lot_id"])

    # ----------------------------------------------------------- search_trends
    op.create_table(
        "search_trends",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "artist_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artists.id"),
            nullable=False,
        ),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("geo", sa.String(10), nullable=True),
        sa.Column("relative_interest", sa.SmallInteger(), nullable=True),
    )
    op.create_index("ix_search_trends_artist_id", "search_trends", ["artist_id"])

    # ----------------------------------------------------------- market_events
    op.create_table(
        "market_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "artist_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artists.id"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=True),
        sa.Column("source", sa.String(200), nullable=True),
        sa.Column("headline", sa.String(1000), nullable=True),
        sa.Column("url", sa.String(2048), nullable=True),
        sa.Column("price_impact_pct", sa.Numeric(7, 4), nullable=True),
    )
    op.create_index("ix_market_events_artist_id", "market_events", ["artist_id"])
    op.create_index("ix_market_events_event_date", "market_events", ["event_date"])

    # ------------------------------------------------------------ exhibitions
    op.create_table(
        "exhibitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "artist_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artists.id"),
            nullable=False,
        ),
        sa.Column("venue", sa.String(500), nullable=True),
        sa.Column("city", sa.String(200), nullable=True),
        sa.Column("country", sa.String(100), nullable=True),
        sa.Column("opens_at", sa.Date(), nullable=True),
        sa.Column("closes_at", sa.Date(), nullable=True),
        sa.Column("is_retrospective", sa.Boolean(), nullable=True),
        sa.Column("source_url", sa.String(2048), nullable=True),
    )
    op.create_index("ix_exhibitions_artist_id", "exhibitions", ["artist_id"])

    # ------------------------------------------------------- regional_price_index
    op.create_table(
        "regional_price_index",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "artist_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artists.id"),
            nullable=False,
        ),
        sa.Column("country", sa.String(100), nullable=False),
        sa.Column("platform", sa.String(100), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("median_hammer", sa.Numeric(14, 2), nullable=True),
        sa.Column("sample_size", sa.Integer(), nullable=True),
        sa.Column("sell_through_rate", sa.Numeric(5, 4), nullable=True),
    )
    op.create_index(
        "ix_regional_price_index_artist_id", "regional_price_index", ["artist_id"]
    )

    # ----------------------------------------------------------- opportunities
    op.create_table(
        "opportunities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "listing_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("listings.id"),
            nullable=False,
        ),
        sa.Column(
            "artist_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artists.id"),
            nullable=False,
        ),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("buy_platform", sa.String(100), nullable=True),
        sa.Column("buy_price_estimate", sa.Numeric(14, 2), nullable=True),
        sa.Column("sell_platform", sa.String(100), nullable=True),
        sa.Column("sell_price_estimate", sa.Numeric(14, 2), nullable=True),
        sa.Column("expected_profit", sa.Numeric(14, 2), nullable=True),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("rationale", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(50), nullable=True),
        sa.Column("actual_buy_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("actual_sell_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("actual_profit", sa.Numeric(14, 2), nullable=True),
    )
    op.create_index("ix_opportunities_artist_id", "opportunities", ["artist_id"])
    op.create_index("ix_opportunities_listing_id", "opportunities", ["listing_id"])

    # -------------------------------------------------------------- portfolio
    op.create_table(
        "portfolio",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "opportunity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("opportunities.id"),
            nullable=True,
        ),
        sa.Column(
            "lot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("lots.id"),
            nullable=False,
        ),
        sa.Column("purchased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purchase_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("storage_location", sa.String(300), nullable=True),
        sa.Column("insurance_value", sa.Numeric(14, 2), nullable=True),
        sa.Column("sold_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sale_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("net_profit", sa.Numeric(14, 2), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
    )
    op.create_index("ix_portfolio_opportunity_id", "portfolio", ["opportunity_id"])
    op.create_index("ix_portfolio_lot_id", "portfolio", ["lot_id"])

    # -------------------------------------------------------------- scrape_log
    op.create_table(
        "scrape_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("platform", sa.String(100), nullable=False),
        sa.Column("url", sa.String(2048), nullable=True),
        sa.Column("status_code", sa.SmallInteger(), nullable=True),
        sa.Column("items_found", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "scraped_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_scrape_log_scraped_at", "scrape_log", ["scraped_at"])


def downgrade() -> None:
    op.drop_index("ix_scrape_log_scraped_at", table_name="scrape_log")
    op.drop_table("scrape_log")

    op.drop_index("ix_portfolio_lot_id", table_name="portfolio")
    op.drop_index("ix_portfolio_opportunity_id", table_name="portfolio")
    op.drop_table("portfolio")

    op.drop_index("ix_opportunities_listing_id", table_name="opportunities")
    op.drop_index("ix_opportunities_artist_id", table_name="opportunities")
    op.drop_table("opportunities")

    op.drop_index("ix_regional_price_index_artist_id", table_name="regional_price_index")
    op.drop_table("regional_price_index")

    op.drop_index("ix_exhibitions_artist_id", table_name="exhibitions")
    op.drop_table("exhibitions")

    op.drop_index("ix_market_events_event_date", table_name="market_events")
    op.drop_index("ix_market_events_artist_id", table_name="market_events")
    op.drop_table("market_events")

    op.drop_index("ix_search_trends_artist_id", table_name="search_trends")
    op.drop_table("search_trends")

    op.drop_index("ix_listings_lot_id", table_name="listings")
    op.drop_table("listings")

    op.drop_index("ix_price_events_sale_date", table_name="price_events")
    op.drop_index("ix_price_events_lot_id", table_name="price_events")
    op.drop_table("price_events")

    op.drop_index("ix_lots_artist_id", table_name="lots")
    op.drop_table("lots")

    op.drop_table("artists")
