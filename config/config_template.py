"""
Configuration Template
======================

Copy this file and update the values to match your AWS environment.
All settings are centralized here for easy management.

Usage:
    cp config/config_template.py config/config.py
    # Edit config.py with your actual values

Environment variables override these defaults when set.
"""

import os


# =============================================================================
# S3 CONFIGURATION
# =============================================================================

S3_INPUT_PATH = os.environ.get(
    "S3_INPUT_PATH",
    "s3://your-bucket/chats/input/dt_20260101/"
)

S3_OUTPUT_PATH = os.environ.get(
    "S3_OUTPUT_PATH",
    "s3://your-bucket/chats/output/"
)

ACRONYM_CSV_PATH = os.environ.get(
    "ACRONYM_CSV_PATH",
    "s3://your-bucket/config/acronym_mapping.csv"
)


# =============================================================================
# AMAZON BEDROCK CONFIGURATION
# =============================================================================

BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-micro-v1:0")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
BEDROCK_REGIONS = ["us-east-1", "us-west-2"]

# Cross-region inference profile mapping
# us-east-1 supports direct model ID; other regions need inference profiles
BEDROCK_MODEL_IDS = {
    "us-east-1": BEDROCK_MODEL_ID,
    "us-west-2": "us.amazon.nova-micro-v1:0",
}


# =============================================================================
# PROCESSING CONFIGURATION
# =============================================================================

MAX_CHATS = int(os.environ.get("MAX_CHATS", "1000"))       # -1 for all
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "100"))     # parallel Bedrock threads
MAX_FILE_SIZE_MB = 900                                       # output file rotation size
BATCH_READ_SIZE = 50000                                      # rows per Parquet batch


# =============================================================================
# AMAZON REDSHIFT CONFIGURATION
# =============================================================================

REDSHIFT_CLUSTER_ID = os.environ.get("REDSHIFT_CLUSTER_ID", "your-redshift-cluster")
REDSHIFT_DB = os.environ.get("REDSHIFT_DB", "your_database")
REDSHIFT_SECRET_ARN = os.environ.get(
    "REDSHIFT_SECRET_ARN",
    "arn:aws:secretsmanager:us-east-1:123456789012:secret/your-secret"
)
REDSHIFT_ROLE_ARN = os.environ.get(
    "REDSHIFT_ROLE_ARN",
    "arn:aws:iam::123456789012:role/YourRedshiftAccessRole"
)
REDSHIFT_IAM_ROLE = os.environ.get(
    "REDSHIFT_IAM_ROLE",
    "arn:aws:iam::123456789012:role/YourRedshiftCopyRole"
)
REDSHIFT_TABLE = os.environ.get("REDSHIFT_TABLE", "your_schema.chat_summary")
