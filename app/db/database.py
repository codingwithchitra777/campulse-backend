import logging
import threading
from contextlib import contextmanager
from psycopg2.pool import ThreadedConnectionPool
from app.core.config import settings

logger = logging.getLogger(__name__)

_pool = None
_pool_lock = threading.Lock()

def get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            # Double-checked locking pattern to prevent race condition
            if _pool is None:
                try:
                    logger.info("Initializing PostgreSQL Connection Pool...")
                    _pool = ThreadedConnectionPool(
                        minconn=1,
                        maxconn=20,
                        dsn=settings.database_url
                    )
                    # Initialize schema
                    conn = _pool.getconn()
                    try:
                        init_db(conn)
                        conn.commit()
                    except Exception as e:
                        conn.rollback()
                        logger.error(f"Error initializing database schema: {e}", exc_info=True)
                        raise
                    finally:
                        _pool.putconn(conn)
                except Exception as e:
                    logger.critical(f"Failed to create connection pool: {e}", exc_info=True)
                    raise
    return _pool

@contextmanager
def get_db():
    pool = get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)

def init_db(conn):
    with conn.cursor() as cur:
        # Create users table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id VARCHAR(100) PRIMARY KEY,
                user_name VARCHAR(255) NOT NULL,
                register_date TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                chat_id BIGINT
            );
        """)
        cur.execute("""
            ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(20) NOT NULL DEFAULT 'user';
        """)
        # Email captured from Google login (Telegram provides none). Nullable.
        cur.execute("""
            ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(255);
        """)
        # Create trades table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                trade_id VARCHAR(100) PRIMARY KEY,
                user_id VARCHAR(100) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                seq INT NOT NULL,
                ticker VARCHAR(50) NOT NULL,
                side VARCHAR(10) NOT NULL,
                price INT NOT NULL,
                qty INT NOT NULL,
                commission INT NOT NULL,
                order_date TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Multi-market foundation (Phase 1): every trade belongs to a market
        # (CSX | US | GOLD_KH) and is denominated in a currency (KHR | USD).
        # Existing rows are all CSX/riel, so the defaults backfill them correctly.
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS market VARCHAR(16) NOT NULL DEFAULT 'CSX';")
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS currency VARCHAR(8) NOT NULL DEFAULT 'KHR';")
        # Create allocations table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS allocations (
                alloc_id VARCHAR(100) PRIMARY KEY,
                user_id VARCHAR(100) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                ticker VARCHAR(50) NOT NULL,
                sell_trade_id VARCHAR(100) NOT NULL REFERENCES trades(trade_id) ON DELETE CASCADE,
                buy_trade_id VARCHAR(100) NOT NULL REFERENCES trades(trade_id) ON DELETE CASCADE,
                qty_allocated INT NOT NULL,
                buy_price INT NOT NULL,
                buy_commission INT NOT NULL,
                buy_qty INT NOT NULL,
                sell_price INT NOT NULL,
                sell_commission INT NOT NULL,
                sell_qty INT NOT NULL,
                realised_pnl INT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Allocations inherit the trade's market/currency so realised P/L can be
        # grouped per currency later (kept in sync with the trades table above).
        cur.execute("ALTER TABLE allocations ADD COLUMN IF NOT EXISTS market VARCHAR(16) NOT NULL DEFAULT 'CSX';")
        cur.execute("ALTER TABLE allocations ADD COLUMN IF NOT EXISTS currency VARCHAR(8) NOT NULL DEFAULT 'KHR';")

        # Account linking: a login identity (alias, e.g. a Telegram user id) points
        # at a canonical account (primary, a Google user id). resolve_primary() maps
        # an alias to its primary at JWT-mint and bot-write time; unlinked ids map to
        # themselves. Google is the canonical account per the identity model.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_links (
                alias_user_id VARCHAR(100) PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
                primary_user_id VARCHAR(100) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                linked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_links_primary ON user_links(primary_user_id);
        """)
        # One-time, short-lived codes minted by a logged-in (primary) account and
        # redeemed by the Telegram bot's /start <code> deep link to create a link.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS link_codes (
                code VARCHAR(64) PRIMARY KEY,
                primary_user_id VARCHAR(100) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                used_at TIMESTAMP
            );
        """)

        # Daily CSX price snapshots, written by PricingService's refresh thread.
        # One row per ticker per trading day; the last write of the day wins,
        # so each row converges on that day's closing price.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                ticker VARCHAR(50) NOT NULL,
                snapshot_date DATE NOT NULL,
                price INT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (ticker, snapshot_date)
            );
        """)
        # Which market a snapshotted symbol belongs to (default CSX for existing rows).
        cur.execute("ALTER TABLE price_history ADD COLUMN IF NOT EXISTS market VARCHAR(16) NOT NULL DEFAULT 'CSX';")

        # Phase 2 — widen money columns from INT to NUMERIC so non-riel markets
        # (USD cents, fractional gold) keep sub-unit precision. Lossless for the
        # existing whole-riel data. Guarded on the current type so it runs once.
        cur.execute("""
            DO $$
            BEGIN
                IF (SELECT data_type FROM information_schema.columns
                    WHERE table_name = 'trades' AND column_name = 'price') = 'integer' THEN
                    ALTER TABLE trades
                        ALTER COLUMN price TYPE NUMERIC(20, 4),
                        ALTER COLUMN qty TYPE NUMERIC(20, 4),
                        ALTER COLUMN commission TYPE NUMERIC(20, 4);
                END IF;
                IF (SELECT data_type FROM information_schema.columns
                    WHERE table_name = 'allocations' AND column_name = 'buy_price') = 'integer' THEN
                    ALTER TABLE allocations
                        ALTER COLUMN qty_allocated TYPE NUMERIC(20, 4),
                        ALTER COLUMN buy_price TYPE NUMERIC(20, 4),
                        ALTER COLUMN buy_commission TYPE NUMERIC(20, 4),
                        ALTER COLUMN buy_qty TYPE NUMERIC(20, 4),
                        ALTER COLUMN sell_price TYPE NUMERIC(20, 4),
                        ALTER COLUMN sell_commission TYPE NUMERIC(20, 4),
                        ALTER COLUMN sell_qty TYPE NUMERIC(20, 4),
                        ALTER COLUMN realised_pnl TYPE NUMERIC(20, 4);
                END IF;
                IF (SELECT data_type FROM information_schema.columns
                    WHERE table_name = 'price_history' AND column_name = 'price') = 'integer' THEN
                    ALTER TABLE price_history ALTER COLUMN price TYPE NUMERIC(20, 4);
                END IF;
            END $$;
        """)

        # Symbols a user tracks without owning (feeds live quotes + news).
        cur.execute("""
            CREATE TABLE IF NOT EXISTS watchlist (
                user_id VARCHAR(100) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                market VARCHAR(16) NOT NULL DEFAULT 'CSX',
                symbol VARCHAR(50) NOT NULL,
                currency VARCHAR(8) NOT NULL DEFAULT 'KHR',
                added_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, market, symbol)
            );
        """)

        # Phase 3 — admin-maintained prices for instruments with no live feed
        # (local Cambodian gold: no free API, so an admin sets the daily board).
        # One current row per (market, symbol); the ManualProvider reads it.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS manual_prices (
                market VARCHAR(16) NOT NULL,
                symbol VARCHAR(50) NOT NULL,
                price NUMERIC(20, 4) NOT NULL,
                currency VARCHAR(8) NOT NULL DEFAULT 'USD',
                change NUMERIC(20, 4) NOT NULL DEFAULT 0,
                updated_by VARCHAR(100),
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (market, symbol)
            );
        """)
