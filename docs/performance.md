# Performance Characteristics

## Throughput

| Configuration | Throughput | Cost per 100K chats |
|--------------|-----------|---------------------|
| 1 region, 50 workers | ~150 chats/min | ~$5.00 |
| 2 regions, 100 workers | ~400 chats/min | ~$5.00 |
| 2 regions, 200 workers | ~600 chats/min | ~$5.00 |

Cost is dominated by Bedrock token usage, not compute time. More workers
reduce wall-clock time but don't increase cost.

## Bottlenecks

1. **Bedrock rate limits** — Primary constraint. Multi-region round-robin helps.
2. **Network I/O** — Bedrock calls are ~1-3s each. Parallelism masks this.
3. **Parquet reads** — Negligible. S3 throughput exceeds processing speed.
4. **Redshift COPY** — Fast for Parquet format (~1M rows/minute).

## Memory Usage

| Component | Memory | Notes |
|-----------|--------|-------|
| One Parquet batch | ~200 MB | 50K rows × typical chat width |
| Results buffer | ~100 MB | Accumulates until file rotation |
| Bedrock connections | ~50 MB | 100 connection pool |
| Overhead | ~150 MB | Python, pandas, PyArrow |
| **Total peak** | **~500 MB** | Well within ml.m5.xlarge (16 GB) |

## Scaling Considerations

### Vertical Scaling
- **Workers > 200**: Diminishing returns due to Bedrock quotas
- **Batch size > 100K**: Minimal benefit, increases memory
- **Instance upgrade**: Not needed — pipeline is I/O bound

### Horizontal Scaling
For >1M chats/day, consider:
- Partition input files by date range
- Run multiple SageMaker Processing Jobs in parallel
- Each job processes a non-overlapping file set
- Redshift merge handles deduplication if overlap occurs

## Latency Breakdown (per chat)

| Step | Avg Time | Notes |
|------|----------|-------|
| Read from batch | <1ms | Already in memory |
| Preprocessing | ~5ms | Grouping, language detection |
| PII sanitization | ~2ms | Regex-based |
| Bedrock API call | ~1.5s | Varies by input length |
| Response parsing | <1ms | String splitting |
| **Total** | **~1.5s** | Dominated by Bedrock latency |

With 100 parallel workers: effective throughput = 100 / 1.5s ≈ 67 chats/sec ≈ 400/min.

## Error Rates (observed)

| Error Type | Rate | Handling |
|-----------|------|----------|
| Throttling (429) | 2-5% | Exponential backoff, resolves within retries |
| Content filtered | 0.5-1% | Simplified prompt retry, ~80% recover |
| Timeout | <0.1% | Standard retry |
| Unrecoverable | <0.1% | Logged as ERROR, pipeline continues |

## Optimization Tips

1. **Increase MAX_WORKERS** gradually until throttling errors rise above 5%
2. **Request Bedrock quota increase** if processing >50K chats daily
3. **Use cross-region inference profiles** for automatic failover
4. **Set MAX_FILE_SIZE_MB = 500** if Redshift COPY is slow (smaller files = more parallelism)
5. **Pre-filter** empty or single-turn chats before the pipeline to reduce unnecessary Bedrock calls
