# Deployment Guide

## Infrastructure Requirements

### SageMaker Notebook Instance
- **Instance type**: ml.m5.xlarge (4 vCPU, 16 GB RAM) — sufficient since workload is I/O bound
- **Storage**: 50 GB EBS (temporary Parquet writes)
- **IAM role**: Needs S3, Bedrock, STS, and Secrets Manager access

### Amazon Bedrock
- **Model**: amazon.nova-micro-v1:0 (enable in Bedrock console)
- **Regions**: us-east-1 and us-west-2 (for throughput)
- **Quotas**: Request increase for InvokeModel if processing >50K chats/day

### Amazon S3
- **Input bucket**: Contains dated folders with Parquet files
- **Output bucket**: Pipeline writes here (can be same bucket, different prefix)
- **Config bucket**: Acronym mapping CSV

### Amazon Redshift
- **Cluster**: Any size (Data API handles connection management)
- **IAM role**: Needs COPY access to the S3 output bucket
- **Schema**: Auto-created by the pipeline on first run

## IAM Permissions

### SageMaker Execution Role
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:ListBucket", "s3:PutObject"],
      "Resource": ["arn:aws:s3:::your-bucket/*", "arn:aws:s3:::your-bucket"]
    },
    {
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel"],
      "Resource": ["arn:aws:bedrock:*::foundation-model/amazon.nova-micro-v1:0"]
    },
    {
      "Effect": "Allow",
      "Action": ["sts:AssumeRole"],
      "Resource": ["arn:aws:iam::123456789012:role/YourRedshiftAccessRole"]
    },
    {
      "Effect": "Allow",
      "Action": ["secretsmanager:GetSecretValue"],
      "Resource": ["arn:aws:secretsmanager:*:*:secret/your-secret*"]
    }
  ]
}
```

### Redshift COPY Role
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:ListBucket"],
      "Resource": ["arn:aws:s3:::your-bucket/chats/output/*"]
    }
  ]
}
```

## Setup Steps

1. **Enable Bedrock models** in us-east-1 and us-west-2
2. **Create S3 bucket** with input/output/config prefixes
3. **Upload acronym CSV** to config prefix (optional)
4. **Create Redshift cluster** and configure Data API access
5. **Create SageMaker notebook** with the execution role above
6. **Clone this repository** into the notebook
7. **Update config/config_template.py** with your values
8. **Run**: `python src/main.py`

## Scheduling (Production)

For automated daily runs, wrap in a SageMaker Processing Job:

```python
from sagemaker.processing import ScriptProcessor

processor = ScriptProcessor(
    role="arn:aws:iam::123456789012:role/SageMakerRole",
    image_uri="763104351884.dkr.ecr.us-east-1.amazonaws.com/pytorch-training:2.0-cpu-py310",
    instance_count=1,
    instance_type="ml.m5.xlarge",
)

processor.run(
    code="src/main.py",
    arguments=["--input-date", "20260115"],
)
```

Or trigger via EventBridge + Lambda for daily scheduling.

## Monitoring

- **CloudWatch Logs**: Pipeline logs all progress to stdout (captured by SageMaker)
- **Error rate**: Track `errors / completed` ratio
- **Throughput**: ~200-500 chats/minute depending on Bedrock quotas
- **Cost**: Monitor Bedrock token usage via Cost Explorer
