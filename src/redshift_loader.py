"""
Redshift Data Loader
=====================

Loads summarization results from S3 into Amazon Redshift using
the Data API with an idempotent staging-and-merge pattern.

Load Strategy:
    1. CREATE target table (if not exists)
    2. CREATE staging table
    3. COPY from S3 Parquet into staging
    4. DELETE matching rows from target
    5. INSERT new rows from staging
    6. DROP staging table

This ensures re-running the pipeline with the same data produces
identical results without duplicates.
"""

import time
import logging
from typing import Optional

import boto3
import pandas as pd

logger = logging.getLogger(__name__)


class RedshiftLoader:
    """
    Loads data into Redshift via the Data API.

    Uses STS role assumption for secure access and handles
    Redshift's concurrent statement limits with retry logic.

    Args:
        cluster_id: Redshift cluster identifier.
        database: Target database name.
        secret_arn: Secrets Manager ARN for credentials.
        role_arn: IAM role to assume for Data API access.
        iam_role: IAM role for COPY command (S3 → Redshift).
        table: Fully qualified target table (schema.table).
        region: AWS region of the Redshift cluster.
    """

    def __init__(
        self,
        cluster_id: str,
        database: str,
        secret_arn: str,
        role_arn: str,
        iam_role: str,
        table: str,
        region: str,
    ):
        self.cluster_id = cluster_id
        self.database = database
        self.secret_arn = secret_arn
        self.iam_role = iam_role
        self.table = table
        self._client = self._create_client(role_arn, region)

    def _create_client(self, role_arn: str, region: str):
        """Assume IAM role and create Redshift Data API client."""
        sts = boto3.client("sts")
        assumed = sts.assume_role(
            RoleArn=role_arn,
            RoleSessionName="chat-summarizer-redshift-load",
        )
        creds = assumed["Credentials"]
        client = boto3.client(
            "redshift-data",
            region_name=region,
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
        )
        logger.info("Redshift Data API client created via role assumption.")
        return client

    def load_from_s3(self, s3_path: str) -> int:
        """
        Execute the full staging-and-merge load.

        Args:
            s3_path: S3 URI containing output Parquet files.

        Returns:
            Total row count in target table after loading.
        """
        staging = f"{self.table}_staging"
        target_short = self.table.split('.')[-1]

        # Step 1: Target table
        self._execute(self._ddl_sql(self.table), "Creating target table")

        # Step 2: Staging table
        self._execute(f"DROP TABLE IF EXISTS {staging};", "Dropping old staging")
        self._execute(self._ddl_sql(staging), "Creating staging table")

        # Step 3: COPY from S3
        self._execute(
            f"COPY {staging} FROM '{s3_path}' IAM_ROLE '{self.iam_role}' FORMAT AS PARQUET;",
            "Loading from S3",
        )

        # Step 4: Delete matching rows
        self._execute(
            f"""DELETE FROM {self.table}
                USING {staging} AS S
                WHERE {target_short}.chat_id = S.chat_id
                  AND {target_short}.chat_date = S.chat_date;""",
            "Deleting existing matches",
        )

        # Step 5: Insert new rows
        self._execute(
            f"""INSERT INTO {self.table}
                (chat_id, chat_date, summary, chat_sentiment,
                 keywords, is_in_scope, acronym_count, acronyms_found,
                 user_language, total_turns, summarized_at)
                SELECT S.chat_id, S.chat_date, S.summary, S.chat_sentiment,
                       S.keywords, S.is_in_scope, S.acronym_count, S.acronyms_found,
                       S.user_language, S.total_turns, S.summarized_at
                FROM {staging} S;""",
            "Inserting summaries",
        )

        # Cleanup
        self._execute(f"DROP TABLE IF EXISTS {staging};", "Cleanup staging")

        # Verify
        return self._get_count()

    def get_sample(self, limit: int = 5) -> Optional[pd.DataFrame]:
        """
        Fetch sample rows for verification.

        Args:
            limit: Number of rows to return.

        Returns:
            DataFrame with sample data, or None if empty.
        """
        stmt_id = self._execute(f"SELECT * FROM {self.table} LIMIT {limit};")
        result = self._client.get_statement_result(Id=stmt_id)
        if not result.get("Records"):
            return None
        cols = [c["name"] for c in result["ColumnMetadata"]]
        rows = [[list(f.values())[0] for f in rec] for rec in result["Records"]]
        return pd.DataFrame(rows, columns=cols)

    def _ddl_sql(self, table_name: str) -> str:
        """Generate CREATE TABLE IF NOT EXISTS SQL."""
        return f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            chat_id              VARCHAR(256),
            chat_date            VARCHAR(50),
            summary              VARCHAR(65535),
            chat_sentiment       VARCHAR(20),
            keywords             VARCHAR(2000),
            is_in_scope          VARCHAR(20),
            acronym_count        INTEGER,
            acronyms_found       VARCHAR(2000),
            user_language        VARCHAR(100),
            total_turns          BIGINT,
            summarized_at        VARCHAR(50)
        );"""

    def _execute(self, sql: str, description: str = "") -> str:
        """
        Execute SQL via Data API with retry on statement limits.

        Retries up to 10 times with increasing backoff if Redshift
        reports ActiveStatementsExceeded.

        Args:
            sql: SQL to execute.
            description: Log-friendly description.

        Returns:
            Statement ID.
        """
        if description:
            logger.info(f"  {description}...")

        for attempt in range(10):
            try:
                resp = self._client.execute_statement(
                    ClusterIdentifier=self.cluster_id,
                    Database=self.database,
                    SecretArn=self.secret_arn,
                    Sql=sql,
                )
                break
            except Exception as e:
                if "ActiveStatementsExceeded" in str(e):
                    wait = 30 * (attempt + 1)
                    logger.warning(f"  Statement limit hit, waiting {wait}s ({attempt+1}/10)")
                    time.sleep(wait)
                else:
                    raise
        else:
            raise RuntimeError("Failed after 10 retries — statement limit exceeded")

        stmt_id = resp["Id"]
        self._wait(stmt_id)
        return stmt_id

    def _wait(self, stmt_id: str):
        """Poll until statement completes or fails."""
        while True:
            status = self._client.describe_statement(Id=stmt_id)
            state = status["Status"]
            if state == "FINISHED":
                return
            elif state in ("FAILED", "ABORTED"):
                raise RuntimeError(f"SQL failed: {status.get('Error', 'unknown')}")
            time.sleep(2)

    def _get_count(self) -> int:
        """Get total row count from target table."""
        stmt_id = self._execute(f"SELECT COUNT(*) FROM {self.table};", "Row count check")
        result = self._client.get_statement_result(Id=stmt_id)
        return result["Records"][0][0]["longValue"]
