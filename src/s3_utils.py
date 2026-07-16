"""
S3 Storage Utilities
=====================

Handles all S3 interactions for the summarization pipeline:
    - Listing input Parquet files
    - Streaming batches from large files
    - Writing typed Parquet output
    - Computing date-based output paths
"""

import os
import re
import logging
from datetime import datetime, timezone
from typing import List, Tuple

import boto3
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import s3fs

logger = logging.getLogger(__name__)


class S3Storage:
    """
    Manages S3 read/write operations for the pipeline.

    Provides streaming batch reads to bound memory usage and writes
    properly-typed Parquet for downstream Redshift COPY compatibility.

    Args:
        input_path: S3 URI for input Parquet files.
        output_path: S3 URI base for output (date will be appended).
        region: AWS region for the S3 client.
    """

    INPUT_COLUMNS = [
        "chat_id", "turn_id", "turn_rank",
        "question", "response", "chat_date",
        "question_raw", "response_raw",
    ]

    def __init__(self, input_path: str, output_path: str, region: str):
        self.input_path = input_path
        self.output_path = output_path
        self.fs = s3fs.S3FileSystem()
        self.s3_client = boto3.client("s3", region_name=region)
        self._output_bucket = None
        self._output_prefix = None

    def list_input_files(self) -> List[str]:
        """
        List Parquet files in the input path.

        Excludes underscore-prefixed files (_metadata, _SUCCESS, etc.).

        Returns:
            List of S3 keys (without s3:// prefix).
        """
        self.fs.invalidate_cache()
        files = [
            f for f in self.fs.ls(self.input_path)
            if f.endswith(".parquet") and not f.split("/")[-1].startswith("_")
        ]
        logger.info(f"Found {len(files)} parquet files in {self.input_path}")
        return files

    def read_batches(self, file_path: str, batch_size: int):
        """
        Stream DataFrames from a Parquet file in fixed-size batches.

        Args:
            file_path: S3 key of the Parquet file.
            batch_size: Rows per batch.

        Yields:
            pd.DataFrame with INPUT_COLUMNS.
        """
        pf = pq.ParquetFile(self.fs.open(file_path))
        for batch in pf.iter_batches(batch_size=batch_size, columns=self.INPUT_COLUMNS):
            yield batch.to_pandas()

    def compute_output_path(self) -> Tuple[str, str]:
        """
        Derive output bucket and prefix from the input path date.

        Extracts date from folder name patterns like dt_YYYYMMDD or
        run-date_YYYYMMDD.

        Returns:
            Tuple of (bucket_name, prefix_with_trailing_slash).
        """
        date_match = re.search(r'(?:dt_|run-date_)(\d{8})', self.input_path)
        if date_match:
            run_date = date_match.group(1)
            run_date_formatted = f"{run_date[:4]}-{run_date[4:6]}-{run_date[6:]}"
        else:
            run_date_formatted = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            logger.warning(f"Could not extract date from input path, using today: {run_date_formatted}")

        output_with_date = self.output_path.rstrip("/") + f"/dt_{run_date_formatted}/"
        parts = output_with_date.replace("s3://", "").split("/", 1)

        self._output_bucket = parts[0]
        self._output_prefix = parts[1] if len(parts) > 1 else ""
        if self._output_prefix and not self._output_prefix.endswith("/"):
            self._output_prefix += "/"

        return self._output_bucket, self._output_prefix

    def write_results(self, results: List[dict], file_index: int) -> int:
        """
        Write results as a Parquet file to S3.

        Ensures proper integer typing for Redshift and strips
        pandas metadata from the schema.

        Args:
            results: List of result dictionaries.
            file_index: Sequence number for the output file.

        Returns:
            Next file index (file_index + 1).
        """
        if not self._output_bucket:
            raise RuntimeError("Call compute_output_path() before writing.")

        result_df = pd.DataFrame(results)
        result_df["total_turns"] = result_df["total_turns"].astype("int64")
        result_df["acronym_count"] = result_df["acronym_count"].astype("int32")

        table = pa.Table.from_pandas(result_df, preserve_index=False)
        table = table.replace_schema_metadata({})

        local_path = f"/tmp/summaries_{file_index:06d}.parquet"
        pq.write_table(table, local_path)

        key = f"{self._output_prefix}summaries_{file_index:06d}.parquet"
        self.s3_client.upload_file(local_path, self._output_bucket, key)
        os.remove(local_path)

        logger.info(f"Wrote {len(results):,} rows → s3://{self._output_bucket}/{key}")
        return file_index + 1

    def get_output_uri(self) -> str:
        """Get the full S3 URI for the output location."""
        return f"s3://{self._output_bucket}/{self._output_prefix}"
