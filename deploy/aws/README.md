# Running SkySift on AWS Batch

The image runs `python -m skysift` as its entry point, so the job `command` starts at the sub-command (`run`, `status`, ...).

1. **Build and push the image to ECR**
   ```bash
   aws ecr create-repository --repository-name skysift
   docker build -f docker/Dockerfile -t ACCOUNT.dkr.ecr.REGION.amazonaws.com/skysift:latest .
   aws ecr get-login-password | docker login --username AWS --password-stdin ACCOUNT.dkr.ecr.REGION.amazonaws.com
   docker push ACCOUNT.dkr.ecr.REGION.amazonaws.com/skysift:latest
   ```
2. **Create an S3 bucket** for the blob store and set `SKYSIFT_BLOB_URL=s3://YOUR-BUCKET/blobs`. The image installs the
   `s3` extra (boto3).
3. **Optional: RDS Postgres** for the results database. Set `SKYSIFT_DATABASE_URL=postgresql+psycopg://...`. The image
   does not bundle a Postgres driver: for a `postgresql` URL install the `pg` extra (`pip install -e ".[s3,pg]"`,
   or a derived image that runs it). Without it, keep the default SQLite file on an attached volume.
4. **Register the job definition** after replacing the `ACCOUNT`, `REGION`, `YOUR-BUCKET` and role placeholders in
   `deploy/aws/batch-job-definition.json`:
   ```bash
   aws batch register-job-definition --cli-input-json file://deploy/aws/batch-job-definition.json
   ```
5. **Submit a sweep**
   ```bash
   aws batch submit-job --job-name skysift-sweep --job-queue YOUR-QUEUE --job-definition skysift-sweep
   ```
   Override the sweep per submission with `--container-overrides command=run,--source,kbonus_bkg,--limit,1000`.

## Permissions, secrets and networking

* The job role needs `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject` and `s3:ListBucket` on the bucket, and nothing else.
* The database URL is not in the template's `environment`: it is injected from AWS Secrets Manager through the
  `secrets` entry `SKYSIFT_DATABASE_URL` (replace the placeholder ARN). The task execution role
  (`executionRoleArn`) needs `secretsmanager:GetSecretValue` on that secret.
* The job needs outbound internet access (a public IP or a NAT gateway) to pull the image from ECR and to reach MAST.
* The template assumes the `pg` extra is installed (`pip install -e ".[s3,pg]"` in a derived image, or edit the
  Dockerfile's install line). With the default SQLite database, remove the `secrets` entry.
