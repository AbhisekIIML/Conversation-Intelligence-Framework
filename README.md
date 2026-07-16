# Conversation Intelligence Framework

A production-grade AI framework that transforms raw enterprise chatbot conversations into structured business intelligence using LLM-powered summarization, sentiment analysis, multilingual detection, keyword extraction, domain-specific enrichment, and scalable cloud-native orchestration on AWS.

---

## Overview

Enterprise conversational assistants generate millions of multi-turn interactions every month. While these conversations contain valuable operational and product insights, they are typically stored only as raw transcripts, making large-scale analysis difficult.

The Conversation Intelligence Framework automatically converts unstructured conversations into structured analytics by enriching each interaction with:

- AI-generated conversation summaries
- User sentiment classification
- Language detection
- Keyword extraction
- Domain-specific acronym identification
- Out-of-scope interaction detection
- Automated data quality validation
- Cloud-native orchestration and scalable processing

The enriched output is delivered into Amazon Redshift, enabling downstream analytics, operational reporting, and product intelligence without manual transcript review.

---

## Features

- **Chat Summarization** — LLM-generated flow summaries describing how chats evolved
- **Sentiment Analysis** — Classifies user sentiment as Positive / Negative / Neutral
- **Keyword Extraction** — Identifies 3-8 topic keywords per chat
- **Prompt Engineering** — Structured prompts with strict PII/content rules and retry logic
- **Batch Processing** — Streams through files one at a time with configurable batch sizes
- **AWS Bedrock Integration** — Multi-region round-robin for throughput with adaptive retries
- **SageMaker Inference** — Runs on notebook instances with bounded memory usage
- **PII Sanitization** — Strips emails, phone numbers, addresses, card numbers before LLM calls
- **Language Detection** — 14+ languages including Hinglish via lingua library
- **Content Filter Handling** — Graceful retry with simplified prompts when Bedrock filters content
- **Error Handling** — Exponential backoff, throttle handling, and per-chat error tracking
- **Configurable Prompts** — Easy to modify summarization instructions and output format
- **Idempotent Loads** — Staging table + merge pattern prevents duplicate records in Redshift

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                          END-TO-END PIPELINE WITH MONITORING                         │
├─────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                     │
│  ┌──────────┐    ┌──────────┐    ┌────────────┐    ┌──────────┐    ┌───────────┐  │
│  │ Source   │───▶│ S3       │───▶│ EventBridge│───▶│  Lambda  │───▶│ SageMaker │  │
│  │Transformed───▶│ (Input)  │    │ Rule       │    │ Trigger  │    │ (Bedrock) │  │
│  │ Data     │    │          │    │            │    │          │    │           │  │
│  └──────────┘    └──────────┘    └────────────┘    └──────────┘    └─────┬─────┘  │
│  Chat Export Job                                                         │         │
│                                                                          │         │
│  ┌──────────┐    ┌──────────┐    ┌────────────┐    ┌──────────┐         │         │
│  │ Source   │───▶│ S3       │───▶│ EventBridge│───▶│  Lambda  │─────────┘         │
│  │Transformed    │ (Input)  │    │ Rule       │    │ Trigger  │                   │
│  │ Data     │    │          │    │            │    │          │                   │
│  └──────────┘    └──────────┘    └────────────┘    └──────────┘                   │
│  Weekly Data Quality Job                                                           │
│                                                                                     │
│                                          │                                          │
│                              ┌───────────┴───────────┐                             │
│                              │                       │                              │
│                          SUCCESS                   ERROR                            │
│                              │                       │                              │
│                              ▼                       ▼                              │
│                     ┌──────────────┐        ┌────────────────┐                     │
│                     │  S3 (Output) │        │ EventBridge    │                     │
│                     │  (Parquet)   │        │ Error Rule     │                     │
│                     └──────┬───────┘        └───────┬────────┘                     │
│                            │                        │                              │
│                            ▼                        ▼                              │
│                     ┌──────────────┐        ┌────────────────┐                     │
│                     │  Redshift    │        │ Lambda         │                     │
│                     │  (chat_      │        │ (Error Handler)│                     │
│                     │   summary    │        └───────┬────────┘                     │
│                     │   dataset)   │                │                              │
│                     └──────────────┘                │                              │
│                                            ┌────────┴────────┐                     │
│                                            │                 │                     │
│                                            ▼                 ▼                     │
│                                    ┌──────────────┐  ┌──────────────┐             │
│                                    │ SNS          │  │ CloudWatch   │             │
│                                    │ (Email Alert)│  │ Alarm        │             │
│                                    └──────┬───────┘  └──────┬───────┘             │
│                                           │                 │                     │
│                                           ▼                 ▼                     │
│                                    ┌──────────────┐  ┌──────────────┐             │
│                                    │  Email to    │  │  Ticket to   │             │
│                                    │  Team        │  │  On-Call      │             │
│                                    └──────────────┘  └──────────────┘             │
│                                                                                     │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

### Pipeline Flow

1. **Ingestion**: Source transformed data is exported as Parquet to S3 (Chat Export Job)
2. **Trigger**: EventBridge rule detects new files → invokes Lambda
3. **Processing**: Lambda starts SageMaker job (Bedrock summarization)
4. **Output**: Results written to S3 (Parquet) → loaded into Redshift (chat_summary dataset)
5. **Data Quality**: Weekly Data Quality Job validates output and triggers re-processing if needed
6. **Monitoring**: On failure, EventBridge error rule triggers alerting Lambda

### Alerting & Monitoring

| Event | Action | Target |
|-------|--------|--------|
| Pipeline failure | SNS notification | Email to team |
| Pipeline failure | CloudWatch Alarm | Auto-creates ticket for on-call |
| SageMaker timeout | EventBridge error rule | Lambda error handler |
| Data quality check fails | SNS + CloudWatch | Email + ticket |

### Request Flow

```
┌────────────────┐
│ Read Parquet   │
│ batch from S3  │
└───────┬────────┘
        │
        ▼
┌────────────────┐
│ Group turns by │
│ chat           │
└───────┬────────┘
        │
        ▼
┌────────────────┐     ┌─────────────────┐
│ For each       │     │ Sanitize PII:   │
│ chat:          │────▶│ emails, phones, │
│                │     │ addresses, IDs  │
└────────────────┘     └───────┬─────────┘
                               │
                               ▼
                       ┌─────────────────┐
                       │ Call Bedrock     │
                       │ (round-robin    │
                       │  across regions)│
                       └───────┬─────────┘
                               │
                    ┌──────────┴──────────┐
                    │                     │
                    ▼                     ▼
           ┌──────────────┐     ┌──────────────┐
           │ Success:     │     │ Content      │
           │ Parse output │     │ Filtered:    │
           │ summary +    │     │ Retry with   │
           │ sentiment +  │     │ simpler      │
           │ keywords     │     │ prompt       │
           └──────┬───────┘     └──────┬───────┘
                  │                     │
                  └──────────┬──────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │ Write Parquet   │
                    │ to S3 output    │
                    └───────┬─────────┘
                            │
                            ▼
                    ┌─────────────────┐
                    │ COPY into       │
                    │ Redshift via    │
                    │ staging + merge │
                    └─────────────────┘
```

### Deployment Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              AWS Account                                 │
│                                                                         │
│  ┌───────────────┐         ┌──────────────────────────┐                │
│  │  SageMaker    │         │  Amazon Bedrock          │                │
│  │  Processing   │────────▶│                          │                │
│  │  Job          │         │  us-east-1: Nova Micro   │                │
│  │               │    ┌───▶│  us-west-2: Nova Micro   │                │
│  └───────┬───────┘    │    └──────────────────────────┘                │
│          │            │                                                 │
│          │    100 parallel workers                                      │
│          │    (round-robin across regions)                              │
│          │                                                              │
│          ▼                                                              │
│  ┌───────────────┐         ┌──────────────────────────┐                │
│  │  Amazon S3    │         │  Amazon Redshift         │                │
│  │               │         │                          │                │
│  │  /input/      │         │  chat_summary            │                │
│  │  /output/     │────────▶│  (staging + merge)       │                │
│  │  /config/     │  COPY   │                          │                │
│  └───────────────┘         └──────────────────────────┘                │
│                                                                         │
│  ┌─────────────────────── MONITORING ──────────────────────────────┐   │
│  │                                                                  │   │
│  │  EventBridge ──▶ Lambda (error handler) ──▶ SNS (email)         │   │
│  │                                          ──▶ CloudWatch Alarm   │   │
│  │                                               ──▶ Ticket (oncall)│   │
│  │                                                                  │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Technologies

| Technology | Purpose |
|-----------|---------|
| Python 3.9+ | Core language |
| Amazon Bedrock | LLM inference (Nova Micro) |
| Amazon SageMaker | Notebook execution environment |
| Amazon S3 | Input/output data storage (Parquet) |
| Amazon Redshift | Analytics data warehouse |
| boto3 | AWS SDK for Python |
| pandas | Data manipulation and grouping |
| PyArrow | Parquet read/write |
| s3fs | S3 filesystem interface |
| lingua | Language detection |

---

## Example

### Input: Raw Chat (3 turns)

```
Turn 1:
User: I placed an order last week and it still shows as processing. Can you help?
Bot: I'd be happy to help you check on your order status. Could you provide your order number?

Turn 2:
User: It's 123-4567890-1234567
Bot: I can see your order is currently in the fulfillment stage. It should ship within 24 hours.

Turn 3:
User: Great, thank you!
Bot: You're welcome! You'll receive a shipping notification once it's dispatched.
```

### Output: Generated Summary

| Field | Value |
|-------|-------|
| **summary** | The user inquired about a delayed order still showing as processing. The bot requested the order number, confirmed the order was in fulfillment, and assured it would ship within 24 hours. The chat concluded positively with the user thanking the bot. |
| **sentiment** | Positive |
| **keywords** | order status, processing, fulfillment, shipping, order tracking |
| **user_language** | English |
| **total_turns** | 3 |
| **is_in_scope** | Y |

---

## Getting Started

### Prerequisites

- AWS account with access to:
  - Amazon SageMaker
  - Amazon Bedrock (Nova Micro model enabled)
  - Amazon S3
  - Amazon Redshift (Data API)
- Python 3.9+
- IAM roles with appropriate permissions for S3, Bedrock, and Redshift

### Installation

```bash
git clone https://github.com/YOUR_USERNAME/genai-chat-summarizer.git
cd genai-chat-summarizer
pip install -r requirements.txt
```

### Configuration

Edit `config/config_template.py` or set environment variables:

```python
# In config_template.py:
S3_INPUT_PATH = "s3://your-bucket/chats/input/dt_20260101/"
S3_OUTPUT_PATH = "s3://your-bucket/chats/output/"
BEDROCK_MODEL_ID = "amazon.nova-micro-v1:0"
MAX_CHATS = 1000   # set to -1 for all
MAX_WORKERS = 100
```

Or via environment variables:

```bash
export S3_INPUT_PATH="s3://my-bucket/chats/input/dt_20260115/"
export MAX_CHATS=-1
export MAX_WORKERS=50
python src/main.py
```

### Running

```bash
# Run the full pipeline
python src/main.py
```

Or use individual modules in a notebook:

```python
from src.summarizer import ChatSummarizer
from src.pii import sanitize_text

# Sanitize text
clean = sanitize_text("Contact john@example.com about order 111-2345678-9012345")
# → 'Contact [EMAIL] about order [ORDER_ID]'

# Summarize a single chat
summarizer = ChatSummarizer(
    regions=["us-east-1"],
    model_ids={"us-east-1": "amazon.nova-micro-v1:0"},
    max_workers=10,
)
result = summarizer.summarize("Turn 1:\nUser: Hello\nBot: Hi there!")
```

---

## Project Structure

```
genai-chat-summarizer/
│
├── README.md
├── LICENSE
├── requirements.txt
│
├── architecture/
│   ├── architecture.png
│   └── workflow.png
│
├── config/
│   └── config_template.py
│
├── src/
│   ├── main.py                  # Pipeline entry point and orchestrator
│   ├── summarizer.py            # Bedrock LLM integration
│   ├── pii.py                   # PII sanitization
│   ├── language_detector.py     # Multi-language detection
│   ├── chat_grouping.py         # Turn → chat aggregation
│   ├── s3_utils.py              # S3 read/write operations
│   ├── redshift_loader.py       # Redshift staging + merge
│   └── writer.py                # Output formatting
│
├── sample_data/
│   ├── sample_chat.json         # Example input
│   └── sample_output.json       # Example output
│
├── docs/
│   ├── design.md                # Architecture and design decisions
│   ├── deployment.md            # Infrastructure and IAM setup
│   └── performance.md           # Throughput and scaling guide
│
└── images/
```

### Module Responsibilities

| Module | Responsibility |
|--------|---------------|
| `config_template.py` | Centralized settings with environment variable overrides |
| `main.py` | Orchestrates the end-to-end pipeline workflow |
| `chat_grouping.py` | Groups turns → chats, detects language, extracts acronyms |
| `pii.py` | Removes PII and profanity before LLM calls |
| `summarizer.py` | Multi-region Bedrock with round-robin and retries |
| `language_detector.py` | 14+ language detection with Hinglish support |
| `s3_utils.py` | Streaming batch reads and typed Parquet writes |
| `redshift_loader.py` | Idempotent staging + merge via Data API |
| `writer.py` | Output schema definition and formatting |

---

## Output Schema

| Column | Type | Description |
|--------|------|-------------|
| chat_id | VARCHAR(256) | Unique chat identifier |
| chat_date | VARCHAR(50) | Date of the chat |
| summary | VARCHAR(65535) | LLM-generated flow summary |
| chat_sentiment | VARCHAR(20) | Positive / Negative / Neutral |
| keywords | VARCHAR(2000) | Comma-separated topic keywords |
| is_in_scope | VARCHAR(20) | Y if bot was helpful, N otherwise |
| acronym_count | INTEGER | Number of domain acronyms detected |
| acronyms_found | VARCHAR(2000) | Comma-separated acronyms |
| user_language | VARCHAR(100) | Detected chat language |
| total_turns | BIGINT | Number of turns in chat |
| summarized_at | VARCHAR(50) | ISO timestamp of when processing ran |

---

## Cost Estimate

| Resource | Cost | Notes |
|----------|------|-------|
| Bedrock (Nova Micro) | ~$0.035 / 1M input tokens | Cheapest Bedrock model |
| SageMaker (ml.m5.xlarge) | ~$0.23/hr | Mostly idle, waiting on Bedrock |
| S3 | < $0.01 | Parquet storage is compact |
| **100K chats** | **~$5 total** | Bedrock dominates cost |

---

## License

MIT License — see [LICENSE](LICENSE) for details.
