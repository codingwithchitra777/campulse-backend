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
        # Market overview broadcast preference
        cur.execute("""
            ALTER TABLE users ADD COLUMN IF NOT EXISTS market_overview_enabled BOOLEAN NOT NULL DEFAULT TRUE;
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
        # Trade journal: a free-text note + comma-separated tags per trade (the
        # reflection layer — annotate why you made the trade).
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS note TEXT;")
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS tags VARCHAR(255);")
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

        # Price alerts: notify the user (via their linked Telegram) when a symbol
        # crosses a target. One-shot — deactivated once it fires.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS price_alerts (
                alert_id VARCHAR(100) PRIMARY KEY,
                user_id VARCHAR(100) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                market VARCHAR(16) NOT NULL DEFAULT 'CSX',
                symbol VARCHAR(50) NOT NULL,
                currency VARCHAR(8) NOT NULL DEFAULT 'KHR',
                target_price NUMERIC(20, 4) NOT NULL,
                direction VARCHAR(8) NOT NULL,
                active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                triggered_at TIMESTAMP
            );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_price_alerts_active ON price_alerts(active) WHERE active;")

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

        # Corporate actions (bonus shares / splits): admin-entered per (market,
        # symbol) with an ex-date; a daemon applies each one once to every
        # holder's open lots (price ÷ m in place + a bonus BUY row dated at the
        # ex-date). trades.corp_action_id marks rows created by an action, which
        # is also the per-user resume guard for a half-applied action.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS corporate_actions (
                action_id VARCHAR(100) PRIMARY KEY,
                market VARCHAR(16) NOT NULL DEFAULT 'CSX',
                symbol VARCHAR(50) NOT NULL,
                action_type VARCHAR(16) NOT NULL,
                ratio_new INTEGER NOT NULL,
                ratio_held INTEGER NOT NULL,
                ex_date DATE NOT NULL,
                note TEXT,
                created_by VARCHAR(100),
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                applied_at TIMESTAMP
            );
        """)
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS corp_action_id VARCHAR(100);")

        # Market Events (Holidays, Dividends) booked by admin for the Calendar
        cur.execute("""
            CREATE TABLE IF NOT EXISTS market_events (
                event_id VARCHAR(100) PRIMARY KEY,
                market VARCHAR(16) NOT NULL DEFAULT 'CSX',
                event_type VARCHAR(20) NOT NULL, -- 'holiday' or 'dividend'
                symbol VARCHAR(50), -- null for holiday
                event_date DATE NOT NULL,
                description TEXT,
                created_by VARCHAR(100),
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # AI Coach: one cached insight per user. snapshot_hash is the cache key —
        # a regenerate is skipped while the user's stats hash to the same value.
        # generated_at also backs the once-per-day refresh limit.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ai_insights (
                user_id VARCHAR(100) PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
                snapshot_hash VARCHAR(64) NOT NULL,
                insight TEXT NOT NULL,
                model VARCHAR(64) NOT NULL,
                generated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
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

        # Personal loan ledger — money the user lent to / borrowed from a person.
        # Entirely separate from trading: never touches P/L, holdings, or the
        # equity curve. direction 'lent' = they owe me, 'borrowed' = I owe them.
        # status is a derived cache (open|partial|settled), recomputed whenever a
        # repayment is added/removed; outstanding = principal - Σ repayments.
        # last_reminded_date guards the due-date Telegram reminder to once a day.
        cur.execute("""
                        CREATE TABLE IF NOT EXISTS loans (
                loan_id VARCHAR(100) PRIMARY KEY,
                user_id VARCHAR(100) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                direction VARCHAR(10) NOT NULL,
                counterparty VARCHAR(255) NOT NULL,
                principal NUMERIC(20, 4) NOT NULL,
                currency VARCHAR(8) NOT NULL DEFAULT 'KHR',
                loan_date DATE NOT NULL,
                due_date DATE,
                note TEXT,
                status VARCHAR(10) NOT NULL DEFAULT 'open',
                last_reminded_date DATE,
                rate_pct NUMERIC(10, 4),
                rate_period VARCHAR(10),
                term_months INTEGER,
                method VARCHAR(20),
                fixed_payment NUMERIC(20, 4),
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # Migration: Add calculation columns to loans
        try:
            cur.execute("ALTER TABLE loans ADD COLUMN rate_pct NUMERIC(10, 4);")
            cur.execute("ALTER TABLE loans ADD COLUMN rate_period VARCHAR(10);")
            cur.execute("ALTER TABLE loans ADD COLUMN term_months INTEGER;")
            cur.execute("ALTER TABLE loans ADD COLUMN method VARCHAR(20);")
            cur.execute("ALTER TABLE loans ADD COLUMN fixed_payment NUMERIC(20, 4);")
        except Exception:
            pass # columns already exist

        cur.execute("CREATE INDEX IF NOT EXISTS idx_loans_user ON loans(user_id);")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS loan_repayments (
                repayment_id VARCHAR(100) PRIMARY KEY,
                loan_id VARCHAR(100) NOT NULL REFERENCES loans(loan_id) ON DELETE CASCADE,
                amount NUMERIC(20, 4) NOT NULL,
                paid_date DATE NOT NULL,
                note TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_loan_repayments_loan ON loan_repayments(loan_id);")
