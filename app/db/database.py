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
