"""
Output Writer Module
=====================

Handles formatting and writing pipeline results in various formats.
Currently supports Parquet output optimized for Redshift COPY operations.

This module can be extended to support additional output formats
(CSV, JSON Lines, etc.) if needed.
"""

import os
import logging
from typing import List

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)


# Output schema definition matching the Redshift target table
OUTPUT_COLUMNS = [
    "chat_id",
    "chat_date",
    "summary",
    "chat_sentiment",
    "keywords",
    "is_in_scope",
    "acronym_count",
    "acronyms_found",
    "user_language",
    "total_turns",
    "summarized_at",
]


def write_parquet(results: List[dict], output_path: str) -> str:
    """
    Write results as a local Parquet file with proper typing.

    Ensures integer columns are correctly typed for Redshift
    and removes pandas metadata from the schema for clean COPY.

    Args:
        results: List of result dictionaries (one per chat).
        output_path: Local file path to write.

    Returns:
        The output_path that was written.

    Example:
        >>> write_parquet(results, "/tmp/summaries_000001.parquet")
        '/tmp/summaries_000001.parquet'
    """
    df = pd.DataFrame(results)

    # Ensure correct types for Redshift
    df["total_turns"] = df["total_turns"].astype("int64")
    df["acronym_count"] = df["acronym_count"].astype("int32")

    # Reorder columns to match schema
    available_cols = [c for c in OUTPUT_COLUMNS if c in df.columns]
    df = df[available_cols]

    # Write clean Parquet (no pandas metadata)
    table = pa.Table.from_pandas(df, preserve_index=False)
    table = table.replace_schema_metadata({})
    pq.write_table(table, output_path)

    logger.info(f"Wrote {len(results):,} rows to {output_path}")
    return output_path


def format_result(chat_row: pd.Series, bedrock_result: dict) -> dict:
    """
    Format a single chat's processing result for output.

    Combines metadata from the grouped chat row with the LLM
    summarization result into a flat dictionary matching the
    output schema.

    Args:
        chat_row: Grouped chat row with metadata fields.
        bedrock_result: Dict with 'summary', 'sentiment', 'keywords'.

    Returns:
        Flat dictionary ready for DataFrame/Parquet output.
    """
    from datetime import datetime, timezone

    return {
        "chat_id": chat_row["chat_id"],
        "chat_date": str(chat_row["chat_date"]),
        "summary": bedrock_result["summary"],
        "chat_sentiment": bedrock_result["sentiment"],
        "keywords": bedrock_result["keywords"],
        "is_in_scope": chat_row["helpfulness"],
        "acronym_count": chat_row["acronym_count"],
        "acronyms_found": chat_row["acronyms_found"],
        "user_language": chat_row["user_language"],
        "total_turns": chat_row["total_turns"],
        "summarized_at": datetime.now(timezone.utc).isoformat(),
    }
