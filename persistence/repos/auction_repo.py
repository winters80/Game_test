"""Auction listing and bid queries against a sqlite3.Connection."""
from __future__ import annotations

import sqlite3
from typing import Any


def add_auction_listing(
    conn: sqlite3.Connection,
    listing_id: str, auction_guild: str, item_id: str | None,
    item_type: str, quantity: int, starting_bid: int,
    expires_turn: int, seller_npc_id: str | None = None,
    reserve_price: int | None = None,
) -> None:
    conn.execute(
        """INSERT INTO auction_listings
           (listing_id, auction_guild, item_id, item_type, quantity,
            current_bid, minimum_bid, reserve_price, seller_npc_id, expires_turn)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            listing_id, auction_guild, item_id, item_type, quantity,
            starting_bid, starting_bid, reserve_price, seller_npc_id, expires_turn,
        ),
    )
    conn.commit()


def place_bid(conn: sqlite3.Connection, listing_id: str, bidder: str, amount: int, turn: int) -> bool:
    """Place a bid. Returns True if bid was accepted (higher than current)."""
    row = conn.execute(
        "SELECT current_bid, status FROM auction_listings WHERE listing_id = ?",
        (listing_id,),
    ).fetchone()
    if not row or row["status"] != "active":
        return False
    if amount <= row["current_bid"]:
        return False
    try:
        conn.execute("BEGIN")
        conn.execute(
            "UPDATE auction_listings SET current_bid = ? WHERE listing_id = ?",
            (amount, listing_id),
        )
        conn.execute(
            "INSERT INTO auction_bids (listing_id, bidder, bid_amount, bid_turn) VALUES (?, ?, ?, ?)",
            (listing_id, bidder, amount, turn),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return True


def get_active_listings(conn: sqlite3.Connection, item_type: str | None = None) -> list[dict[str, Any]]:
    if item_type:
        rows = conn.execute(
            "SELECT * FROM auction_listings WHERE status = 'active' AND item_type = ?",
            (item_type,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM auction_listings WHERE status = 'active'"
        ).fetchall()
    return [dict(r) for r in rows]


def expire_listings(conn: sqlite3.Connection, current_turn: int) -> list[dict[str, Any]]:
    """Close all listings past their expiry. Returns the expired listings."""
    rows = conn.execute(
        "SELECT * FROM auction_listings WHERE status = 'active' AND expires_turn <= ?",
        (current_turn,),
    ).fetchall()
    expired = [dict(r) for r in rows]
    if expired:
        ids = [r["listing_id"] for r in expired]
        placeholders = ",".join("?" * len(ids))
        conn.execute(
            f"UPDATE auction_listings SET status = 'expired' WHERE listing_id IN ({placeholders})",
            ids,
        )
        conn.commit()
    return expired
