# 🚀 Pricing Architecture Refactor Walkthrough

The backend has been completely refactored to decouple CSX API fetching from client requests. It is now a highly robust, scalable system that uses Redis for centralized locking and caching.

## What Was Changed

### 1. `app/services/redis_service.py`
- **Distributed Locking:** Added the `acquire_lock(lock_name, timeout)` method. It uses a Redis atomic `SETNX` operation to grab a temporary lock.
- **Serialization Methods:** Added `save_latest_prices()` and `get_latest_prices()` to serialize the parsed price dictionary into JSON and write it directly to Redis under the `csx:latest_prices` key.

### 2. `app/services/pricing.py`
- **Background Thread Polling Loop (`_background_refresh_loop`):**
  - Sleep interval dropped from `30` to `15` seconds.
  - Added a safety check: `if RedisService().acquire_lock("csx:poll_lock", timeout=10):`.
  - **Why this matters:** If you spin up 10 workers for scale, only 1 worker gets the lock. The other 9 will see the lock is taken and instantly skip polling for that cycle. This prevents CSX from banning your IP!
- **Data Consumption (`get_all_prices`, `get_latest_price`):**
  - Completely removed `TTLCache` (the old in-memory cache).
  - All read functions now instantly fetch the `csx:latest_prices` JSON string from Redis, parse it, and return it. No external network request is made.
  - If Redis is entirely empty (e.g. during a server reboot and CSX is down), it safely returns the hardcoded `FALLBACK_PRICES`.

## Verification
- We verified that the in-memory cache is fully stripped from the `__init__` constructor.
- The `get_all_prices` and `get_latest_price` functions now have $O(1)$ read complexity directly against Redis.
