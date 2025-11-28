import boto3
import os

S3 = os.getenv("S3_BUCKET", "")
AWSID = os.getenv("AWS_ACCESS_KEY_ID", "")
AWSKEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")
REGION = os.getenv("AWS_REGION", "us-east-1")

def upload_to_s3_if_configured(local_path, key):
    if not S3:
        return None

    s3 = boto3.client(
        "s3",
        aws_access_key_id=AWSID,
        aws_secret_access_key=AWSKEY,
        region_name=REGION
    )

    s3.upload_file(local_path, S3, key, ExtraArgs={"ACL": "public-read", "ContentType": "video/mp4"})
    return f"https://{S3}.s3.{REGION}.amazonaws.com/{key}"
