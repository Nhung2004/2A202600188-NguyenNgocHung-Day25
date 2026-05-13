import redis
try:
    r = redis.Redis(host='localhost', port=6379, db=0)
    print(f"Ping: {r.ping()}")
    r.close()
except Exception as e:
    print(f"Error: {e}")
