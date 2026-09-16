# Stop the box building: cut over to ECR

**Why.** The production box builds `api`, `ui` and `sandbox` on every deploy,
because it has never had a credential for a registry. Two costs, and the
second is the one customers feel:

1. Build cache accumulates with no ceiling. On 16 Sep 2026 it reached
   **143.2GB of a 145GB disk** — everything else on the machine is 11GB. The
   SSM agent could not write its own output, every deploy failed before
   running a line of the deploy script, and a merge carrying five
   customer-facing fixes sat unshipped for two hours.
2. A Next.js production build **saturates all four vCPU**, and those are the
   same cores that carry live calls: `run_pipeline_telephony` runs inside the
   uvicorn workers. Deploying during business hours degrades every
   conversation in progress, and presents to the customer as the model being
   slow.

Both go away when the box stops building. A deploy becomes a pull: seconds,
and no CPU.

**What was already true.** `docker-compose.yaml` has always read
`image: ${REGISTRY}/decibyl-<name>:${IMAGE_TAG}`, and `build-images.yml` has
been building and pushing to GHCR on every merge. The only missing piece was
the box being allowed to pull.

**What we measured.** From the box, `https://ghcr.io/v2/` answers `401` and
`https://api.ecr.ap-south-1.amazonaws.com/` answers `404`. Both mean *reached
it*. So the box has egress and the problem was only ever a credential — no
NAT gateway and no VPC endpoints are needed.

**Why ECR over a GHCR token.** With ECR there is no password anywhere: the
box proves it is that box with its instance role and AWS hands it a 12-hour
token. A GHCR token would be a long-lived secret living on the production
box. Same outcome today, different thing to explain to a hospital later.

---

## 1. Create the three repositories

```bash
for name in api ui sandbox; do
  aws ecr create-repository \
    --repository-name "decibyl-$name" \
    --region ap-south-1 \
    --image-scanning-configuration scanOnPush=true
done
```

## 2. Let the registry delete old images by itself

This is the part that makes "it cleans up after itself" true at the registry
rather than as a cron job on a box that can fail. Keep the last 20 builds;
everything older goes.

```bash
cat > /tmp/lifecycle.json <<'JSON'
{
  "rules": [
    {
      "rulePriority": 1,
      "description": "Keep the last 20 builds; a rollback never needs more.",
      "selection": {
        "tagStatus": "any",
        "countType": "imageCountMoreThan",
        "countNumber": 20
      },
      "action": { "type": "expire" }
    }
  ]
}
JSON

for name in api ui sandbox; do
  aws ecr put-lifecycle-policy \
    --repository-name "decibyl-$name" \
    --region ap-south-1 \
    --lifecycle-policy-text file:///tmp/lifecycle.json
done
```

## 3. Let CI push

The role in `AWS_DEPLOY_ROLE_ARN` already exists and is already assumed by
`deploy.yml` over OIDC. It needs to be allowed to push:

```json
{
  "Effect": "Allow",
  "Action": [
    "ecr:GetAuthorizationToken",
    "ecr:BatchCheckLayerAvailability",
    "ecr:InitiateLayerUpload",
    "ecr:UploadLayerPart",
    "ecr:CompleteLayerUpload",
    "ecr:PutImage"
  ],
  "Resource": "*"
}
```

`ecr:GetAuthorizationToken` has to be on `*`; the rest can be narrowed to the
three repository ARNs once this is working.

## 4. Let the box pull

On the EC2 instance role (the same one that already reads SSM and RDS):

```json
{
  "Effect": "Allow",
  "Action": [
    "ecr:GetAuthorizationToken",
    "ecr:BatchCheckLayerAvailability",
    "ecr:GetDownloadUrlForLayer",
    "ecr:BatchGetImage"
  ],
  "Resource": "*"
}
```

Read-only on purpose. The box has no business pushing an image.

## 5. Prove it before switching anything

Merge first, let CI publish one build, then on the box:

```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGISTRY="$ACCOUNT.dkr.ecr.ap-south-1.amazonaws.com"

aws ecr get-login-password --region ap-south-1 \
  | docker login --username AWS --password-stdin "$REGISTRY"

docker pull "$REGISTRY/decibyl-api:latest"
```

If that pulls, everything above is right.

## 6. Switch

In the box's `.env`:

```
REGISTRY=<account>.dkr.ecr.ap-south-1.amazonaws.com
IMAGE_SOURCE=registry
```

The next deploy pulls instead of building. **To go back, delete the
`IMAGE_SOURCE` line** — one edit, and the box builds again as it does today.

## 7. Keep the login alive

An ECR token lasts 12 hours, so the box needs to re-login before each deploy.
`ci_deploy.sh` does not do this yet — until it does, either add the
`get-login-password` line from step 5 to the deploy, or run it from a
`systemd` timer every 6 hours.

> **This is the one remaining sharp edge.** A deploy at hour 13 fails on the
> pull with a 401 rather than anything that reads like "your token expired".
> Do not skip it.

---

## What this does not cover

**Zero-downtime.** Pulling removes the six-minute build, but the container
restart at the end is still a few seconds of interruption. Blue/green behind
nginx is a separate piece of work.

**GHCR.** It keeps being pushed to. It costs nothing, and a second copy of a
release is not a bad thing to have.
