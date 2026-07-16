# Design Document

## Problem Statement

Organizations with chatbot deployments generate thousands of multi-turn conversations daily. Extracting actionable insights — topic trends, sentiment patterns, resolution rates — requires reading each conversation manually, which doesn't scale beyond a few hundred per day.

## Solution

A batch processing pipeline that uses Large Language Models to automatically:
1. Summarize each conversation into a concise flow description
2. Classify user sentiment (Positive / Negative / Neutral)
3. Extract topic keywords for trend analysis

## Design Principles

### 1. Privacy-First Architecture
All text is sanitized locally before reaching the LLM. No PII (emails, phone numbers, addresses, financial data) is ever sent to Bedrock. This is enforced at the module level in `src/pii.py`.

### 2. Bounded Memory
The pipeline never loads more than one Parquet file into memory at a time, and within each file, data is streamed in configurable batches (default: 50K rows). This allows processing terabytes of input on a single ml.m5.xlarge instance.

### 3. Idempotent Loads
The Redshift loading uses a staging table with delete-then-insert (merge pattern). Re-running the pipeline for the same date produces identical results without duplicates.

### 4. Graceful Degradation
- Throttled requests: exponential backoff (up to 5 retries)
- Content-filtered responses: retry with simplified prompt
- Persistent failures: logged as ERROR, pipeline continues

### 5. Throughput Optimization
- Multi-region Bedrock: round-robin across us-east-1 and us-west-2
- Parallel workers: up to 100 concurrent threads
- Adaptive connection pooling: sized to worker count + buffer

## Data Flow

```
Input (Parquet)
  │
  ├── chat_id, turn_id, turn_rank
  ├── question, response
  ├── question_raw, response_raw
  └── chat_date
         │
         ▼
  [Preprocessing]
  • Sort by chat_id + turn_rank
  • Concatenate turns
  • Detect language
  • Find acronyms
  • Check helpfulness
         │
         ▼
  [Sanitization]
  • Remove emails, phones, addresses
  • Remove financial data
  • Remove profanity
  • Truncate to 8000 chars
         │
         ▼
  [LLM Summarization]
  • Structured prompt → Bedrock
  • Parse SUMMARY / SENTIMENT / KEYWORDS
  • Retry on filter/throttle
         │
         ▼
Output (Parquet → Redshift)
  │
  ├── chat_id, chat_date
  ├── summary, chat_sentiment, keywords
  ├── is_in_scope, user_language
  ├── acronym_count, acronyms_found
  ├── total_turns, summarized_at
  └──
```

## Prompt Engineering

The summarization prompt is designed with:
- **Strict PII rules** — explicit instructions to never include identifiers
- **Structured output format** — SUMMARY/SENTIMENT/KEYWORDS labels for reliable parsing
- **Sentiment signals** — guidance on what constitutes negative vs neutral
- **Content safety** — fallback prompt if the primary is filtered
- **Length control** — max 150 words for summary, 3-8 keywords

## Technology Choices

| Choice | Rationale |
|--------|-----------|
| Bedrock Nova Micro | Cheapest model, sufficient quality for summarization |
| Parquet | Columnar, compressed, native Redshift COPY support |
| Threading (not async) | boto3 is thread-safe; Bedrock is I/O-bound |
| Data API (not JDBC) | No VPC/security group setup needed from SageMaker |
| Staging + merge | Idempotent without requiring UPSERT syntax |
