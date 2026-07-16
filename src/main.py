"""
Main Pipeline Orchestrator
===========================

Entry point for the chat summarization pipeline. Coordinates all modules
to execute the full workflow:

    S3 (Parquet) → Preprocessing → Bedrock LLM → S3 (output) → Redshift

Usage:
    python src/main.py

    With environment overrides:
    S3_INPUT_PATH=s3://bucket/input/ MAX_CHATS=-1 python src/main.py
"""

import sys
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import pandas as pd

sys.path.insert(0, "..")
from config.config_template import *
from src.chat_grouping import group_turns_into_chats, load_acronyms
from src.summarizer import ChatSummarizer
from src.s3_utils import S3Storage
from src.redshift_loader import RedshiftLoader

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


def process_one(row: pd.Series, summarizer: ChatSummarizer) -> dict:
    """
    Process a single chat through the summarization pipeline.

    Args:
        row: Grouped chat row containing chat_text and metadata.
        summarizer: Initialized ChatSummarizer instance.

    Returns:
        Dictionary with summarized fields ready for output.
    """
    result = summarizer.summarize(row["chat_text"])
    return {
        "chat_id": row["chat_id"],
        "chat_date": str(row["chat_date"]),
        "summary": result["summary"],
        "chat_sentiment": result["sentiment"],
        "keywords": result["keywords"],
        "is_in_scope": row["helpfulness"],
        "acronym_count": row["acronym_count"],
        "acronyms_found": row["acronyms_found"],
        "user_language": row["user_language"],
        "total_turns": row["total_turns"],
        "summarized_at": datetime.now(timezone.utc).isoformat(),
    }


def run_pipeline(skip_redshift: bool = False) -> dict:
    """
    Execute the full summarization pipeline.

    Steps:
        1. List input Parquet files from S3
        2. Stream batches, group turns into chats
        3. Call Bedrock in parallel for each chat
        4. Write output Parquet files to S3
        5. Load results into Redshift (staging + merge)

    Args:
        skip_redshift: If True, skip the Redshift loading step.

    Returns:
        Execution metrics dictionary.
    """
    start_time = time.time()

    # Initialize components
    storage = S3Storage(S3_INPUT_PATH, S3_OUTPUT_PATH, AWS_REGION)
    summarizer = ChatSummarizer(BEDROCK_REGIONS, BEDROCK_MODEL_IDS, MAX_WORKERS)
    acronym_set = load_acronyms(ACRONYM_CSV_PATH)

    # Setup output path
    bucket, prefix = storage.compute_output_path()
    output_uri = storage.get_output_uri()

    # List input files
    all_files = storage.list_input_files()
    if not all_files:
        logger.warning("No input files found. Exiting.")
        return {"completed": 0, "errors": 0, "files_written": 0}

    logger.info(f"Processing {len(all_files)} files with {MAX_WORKERS} workers")
    logger.info(f"MAX_CHATS = {MAX_CHATS} ({'all' if MAX_CHATS == -1 else 'test mode'})")
    logger.info(f"Output: {output_uri}")

    # Processing state
    max_file_bytes = MAX_FILE_SIZE_MB * 1024 * 1024
    results = []
    current_file_bytes = 0
    completed = 0
    errors = 0
    file_index = 0
    hit_limit = False
    seen_chats = set()

    for file_num, parquet_file in enumerate(all_files):
        if hit_limit:
            break

        logger.info(f"File {file_num+1}/{len(all_files)}: s3://{parquet_file}")
        leftover_rows = pd.DataFrame()

        for batch_df in storage.read_batches(parquet_file, BATCH_READ_SIZE):
            if hit_limit:
                break

            chunk = batch_df

            # Prepend leftover rows from previous batch
            if len(leftover_rows) > 0:
                chunk = pd.concat([leftover_rows, chunk], ignore_index=True)
                leftover_rows = pd.DataFrame()

            # Keep last chat_id rows as leftover for next batch
            last_chat_id = chunk["chat_id"].iloc[-1]
            leftover_mask = chunk["chat_id"] == last_chat_id
            leftover_rows = chunk[leftover_mask].copy()
            chunk = chunk[~leftover_mask]

            if len(chunk) == 0:
                continue

            # Group and deduplicate
            grouped = group_turns_into_chats(chunk, acronym_set)
            grouped = grouped[~grouped["chat_id"].isin(seen_chats)]
            if len(grouped) == 0:
                continue

            # Apply limit
            if MAX_CHATS > 0:
                remaining = MAX_CHATS - completed
                if remaining <= 0:
                    hit_limit = True
                    break
                grouped = grouped.head(remaining)

            seen_chats.update(grouped["chat_id"].tolist())

            # Process in parallel
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {
                    executor.submit(process_one, row, summarizer): idx
                    for idx, row in grouped.iterrows()
                }
                for future in as_completed(futures):
                    try:
                        result = future.result()
                        row_bytes = sum(len(str(v)) for v in result.values()) + 20
                        results.append(result)
                        current_file_bytes += row_bytes
                        if result["summary"].startswith("ERROR"):
                            errors += 1
                    except Exception as e:
                        logger.error(f"Worker exception: {e}")
                        errors += 1
                    completed += 1

                    # Rotate output file
                    if current_file_bytes >= max_file_bytes:
                        file_index = storage.write_results(results, file_index)
                        results = []
                        current_file_bytes = 0

            # Progress
            if completed % 1000 == 0 and completed > 0:
                elapsed = time.time() - start_time
                rate = completed / elapsed if elapsed > 0 else 0
                logger.info(f"Progress: {completed:,} | Rate: {rate:.1f}/sec | Errors: {errors:,}")

        # Process leftover rows
        if len(leftover_rows) > 0 and not hit_limit:
            grouped = group_turns_into_chats(leftover_rows, acronym_set)
            grouped = grouped[~grouped["chat_id"].isin(seen_chats)]
            if len(grouped) > 0 and MAX_CHATS > 0:
                remaining = MAX_CHATS - completed
                if remaining > 0:
                    grouped = grouped.head(remaining)
                else:
                    hit_limit = True
            if not hit_limit and len(grouped) > 0:
                seen_chats.update(grouped["chat_id"].tolist())
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                    futures = {
                        executor.submit(process_one, row, summarizer): idx
                        for idx, row in grouped.iterrows()
                    }
                    for future in as_completed(futures):
                        try:
                            result = future.result()
                            results.append(result)
                            if result["summary"].startswith("ERROR"):
                                errors += 1
                        except Exception as e:
                            logger.error(f"Worker exception: {e}")
                            errors += 1
                        completed += 1

    # Flush remaining results
    if results:
        file_index = storage.write_results(results, file_index)

    elapsed = time.time() - start_time
    logger.info(f"Complete: {completed:,} chats in {elapsed/3600:.1f}h | Errors: {errors:,}")

    # Load into Redshift
    if not skip_redshift:
        try:
            loader = RedshiftLoader(
                REDSHIFT_CLUSTER_ID, REDSHIFT_DB, REDSHIFT_SECRET_ARN,
                REDSHIFT_ROLE_ARN, REDSHIFT_IAM_ROLE, REDSHIFT_TABLE, AWS_REGION,
            )
            row_count = loader.load_from_s3(output_uri)
            logger.info(f"Redshift load complete. Total rows: {row_count:,}")
        except Exception as e:
            logger.error(f"Redshift load failed: {e}")

    return {
        "completed": completed,
        "errors": errors,
        "files_written": file_index,
        "elapsed_hours": round(elapsed / 3600, 2),
        "output_path": output_uri,
    }


if __name__ == "__main__":
    results = run_pipeline()
    print(f"\n{'='*60}")
    print("PIPELINE COMPLETE")
    print("=" * 60)
    print(f"  Chats processed:  {results['completed']:,}")
    print(f"  Errors:           {results['errors']:,}")
    print(f"  Files written:    {results['files_written']}")
    print(f"  Runtime:          {results['elapsed_hours']:.1f} hours")
    print(f"  Output:           {results['output_path']}")
    print("=" * 60)
