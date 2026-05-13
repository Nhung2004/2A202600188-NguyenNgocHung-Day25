# Day 10 Reliability Report

## 1. Architecture summary

The system routes user requests through a caching layer first. If there's a cache miss, the request goes to the `ReliabilityGateway`, which iterates through a fallback chain of LLM providers. Each provider is protected by a `CircuitBreaker`. If the primary provider fails consistently, its circuit opens, and the gateway routes the request to the backup provider. If all providers fail (or their circuits are open), the gateway returns a static fallback message. The caching layer uses a `SharedRedisCache` to ensure state is shared across multiple instances, reducing load on providers and saving costs.

```
User Request
    |
    v
[Gateway] ---> [Cache check] ---> HIT? return cached
    |                                 |
    v                                 v MISS
[Circuit Breaker: Primary] -------> Provider A
    |  (OPEN? skip)
    v
[Circuit Breaker: Backup] --------> Provider B
    |  (OPEN? skip)
    v
[Static fallback message]
```

## 2. Configuration

| Setting | Value | Reason |
|---|---:|---|
| failure_threshold | 3 | High enough to absorb occasional network jitter, but low enough to fail fast during outages. |
| reset_timeout_seconds | 2 | Gives the provider a short window to recover before we probe again with a `HALF_OPEN` state. |
| success_threshold | 1 | A single successful probe is enough to close the circuit and resume normal traffic flow. |
| cache TTL | 300 | 5 minutes is a reasonable balance between serving fresh answers and maximizing cache hit rates. |
| similarity_threshold | 0.92 | Tuned using character 3-grams to ensure semantic matching while avoiding false hits for slightly different queries. |
| load_test requests | 100 | Sufficient volume to trigger circuit breaker state transitions and measure cache performance. |

## 3. SLO definitions

Define your target SLOs and whether your system meets them:

| SLI | SLO target | Actual value | Met? |
|---|---|---:|---|
| Availability | >= 99% | 99.75% | Yes |
| Latency P95 | < 2500 ms | 314.44 ms | Yes |
| Fallback success rate | >= 95% | 97.14% | Yes |
| Cache hit rate | >= 10% | 79.00% | Yes |
| Recovery time | < 5000 ms | ~3797 ms | Yes |

## 4. Metrics

Summary of `reports/metrics.json`.

| Metric | Value |
|---|---:|
| availability | 0.9975 |
| error_rate | 0.0025 |
| latency_p50_ms | 234.61 |
| latency_p95_ms | 314.44 |
| latency_p99_ms | 318.45 |
| fallback_success_rate | 0.9714 |
| cache_hit_rate | 0.79 |
| estimated_cost_saved | 0.316 |
| circuit_open_count | 4 |
| recovery_time_ms | Null (calculated on no-cache run: 3797.2) |

## 5. Cache comparison

Run simulation with cache enabled vs disabled. Fill in both columns:

| Metric | Without cache | With cache | Delta |
|---|---:|---:|---|
| latency_p50_ms | 234.34 | 234.61 | +0.27 (Redis overhead) |
| latency_p95_ms | 314.33 | 314.44 | +0.11 |
| estimated_cost | 0.178444 | 0.04187 | -76.5% |
| cache_hit_rate | 0 | 0.79 | +79.0% |

## 6. Redis shared cache

Explain why shared cache matters for production:

- Why in-memory cache is insufficient for multi-instance deployments: Each instance maintains its own separate cache. This leads to redundant API calls for the same query routed to different instances, lowering the overall cache hit rate and increasing costs.
- How `SharedRedisCache` solves this: Redis acts as a centralized data store. Once any gateway instance caches a response, all other instances instantly have access to it, maximizing the hit rate across the entire cluster.

### Evidence of shared state

Two separate cache instances (`c1` and `c2`) pointing to the same Redis URL can read each other's data:

```python
# test_shared_state_across_instances
c1.set("shared query", "shared response")
cached, _ = c2.get("shared query")
assert cached == "shared response"  # Passes
```

### Redis CLI output

```bash
# docker compose exec redis redis-cli KEYS "rl:cache:*"
rl:cache:b2a52f7dc795
rl:cache:095946136fea
rl:cache:9e413fd814eb
rl:cache:8baa2cfa11fa
```

### In-memory vs Redis latency comparison (optional)

| Metric | In-memory cache | Redis cache | Notes |
|---|---:|---:|---|
| latency_p50_ms | ~238.19 | 234.61 | Redis overhead is minimal for this workload |

## 7. Chaos scenarios

| Scenario | Expected behavior | Observed behavior | Pass/Fail |
|---|---|---|---|
| primary_timeout_100 | All traffic fallback to backup, circuit opens | Circuit opened, high fallback success rate. | Pass |
| primary_flaky_50 | Circuit oscillates, mix of primary and fallback | Circuit opened multiple times, availability maintained. | Pass |
| all_healthy | All requests via primary, no circuit opens | No circuit opens, 100% traffic handled efficiently. | Pass |
| cache_stress | High latency on primary, test cache | Cache handled majority of traffic, protecting slow primary. | Pass |

## 8. Failure analysis

Explain one remaining weakness and how you would fix it before production.

- What could still go wrong? 
  The circuit breaker state is currently kept in-memory per gateway instance. If one instance detects a provider failure and opens its circuit, other instances will still route traffic to the failing provider until they individually trip their own thresholds.
- What would you change? 
  I would move the circuit breaker state (failure counts, open/close status) to Redis. This ensures that when the provider goes down, the entire gateway cluster fails over instantly.

## 9. Next steps

List 2-3 concrete improvements you would make:

1. **Redis-backed Circuit State**: Synchronize circuit breaker state across all instances using Redis to prevent redundant failures.
2. **Cost-Aware Routing**: Add logic to track API spending. If the monthly budget hits 90%, prioritize the cheaper backup provider or serve cache-only responses.
3. **Concurrency**: Update `run_simulation` to use `concurrent.futures.ThreadPoolExecutor` to test the system under true concurrent load and identify race conditions.
