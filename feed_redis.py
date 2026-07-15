import json
import redis
from datetime import datetime

redis_url = "redis://default:FIwEoRahdTexVeF2MlgQRyT2XjhwIUuJ@time-camera-show-26003.db.redis.io:11914"
client = redis.from_url(redis_url, decode_responses=True)

with open('seed_data.json', 'r') as f:
    data = json.load(f)

for item in data:
    ticker = item['ticker']
    snapshot_date = item['snapshot_date']
    price = item['price']
    
    key = f"sparkline:{ticker}"
    client.hset(key, snapshot_date, price)
    print(f"Set {key} {snapshot_date} = {price}")

print("Done feeding data to Redis.")
