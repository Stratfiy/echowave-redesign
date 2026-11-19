# Moving Decibyl to a new AWS account

The IAM and the configuration half of the account move. The rest of the
operation — the instance, the data, DNS, certificates and carrier callbacks —
is in `../../MIGRATE-TO-MUMBAI.md`, which is the same shape of job minus the
account change.

Everything here is adapted from two documents already in this repo, and they
remain the explanation; these files are only the repeatable version:

- `../../docs/DEPLOY-GITHUB-ACTIONS.md` — the OIDC trust, both roles, and why
  deploys use SSM rather than an SSH key.
- `../../docs/deployment/parameter-store.mdx` — the instance role's Parameter
  Store policy and the one-time `.env` import.

## Order

Do these in order. Steps 1 and 2 have external waits, so start them first.

1. **Request an EC2 vCPU quota increase** in the new account. A new account's
   default on-demand limit may refuse the instance class outright, and the
   error does not mention quotas. Hours to two days.
2. **Request Bedrock model access** in `ap-south-1`. Granted per account, so
   the new one has none. Add the Claude-Platform-on-AWS workspace too if
   `CLAUDE_BACKEND` uses it.
3. **Create the buckets**, with new names. Bucket names are globally unique,
   so the new account cannot reuse one the old account still holds. Their
   names go into the instance policy, which is why this comes before step 5.
   Keep `BACKUP_MIRROR_BUCKET` in a different region from the primary on
   purpose.
4. **Launch the instance.** Note its id.
5. **Run `bootstrap_iam.sh`** (below).
6. **Run `copy_parameter_store.py`** (below).
7. **Update GitHub**: secret `AWS_DEPLOY_ROLE_ARN`, secret `EC2_INSTANCE_ID`,
   variable `AWS_REGION`. All three must agree with the new account and
   region, along with the instance ARN inside the deploy policy.
8. **Register the self-hosted runner on the new box** with labels
   `self-hosted, linux, x64, production`. `deploy.yml` and `ops.yml` both run
   on it. Without it, every deploy and every ops command queues forever and
   nothing says why.
9. **Move the data**, then verify. `../../MIGRATE-TO-MUMBAI.md` §6 and
   `scripts/verify_region_migration.py`.

## bootstrap_iam.sh

Creates GitHub's OIDC provider, the `DecibylEC2SSM` instance role and profile,
the `DecibylGitHubDeploy` role with its trust and inline policy, and attaches
the profile to the instance.

```bash
export AWS_PROFILE=decibyl-new
export NEW_ACCOUNT_ID=123456789012
export NEW_INSTANCE_ID=i-0abc...
export NEW_S3_BUCKET=... NEW_KYC_BUCKET=... NEW_BACKUP_MIRROR_BUCKET=...
export OPS_SECRET_NAMESPACE=decibyl/prod/providers/   # only if used

bash bootstrap_iam.sh --dry-run    # prints every call it would make
bash bootstrap_iam.sh
```

It is safe to re-run: creates tolerate an existing entity, and both inline
policies are overwritten with what the templates now say. It renders the three
`*.json.template` files into a temp directory, checks each parses as JSON, and
**names any placeholder it could not fill** rather than substituting an empty
string, because an empty string in a resource ARN silently widens a policy.

Run it again once the instance exists if you ran it before: the deploy policy
pins `ssm:SendCommand` to one instance id, and until that is real the
statement does not match anything.

## copy_parameter_store.py

Copies `/decibyl/prod/` from the old account's Parameter Store into the new
one's.

```bash
python3 copy_parameter_store.py \
    --source-profile decibyl-old --target-profile decibyl-new --dry-run
python3 copy_parameter_store.py \
    --source-profile decibyl-old --target-profile decibyl-new
```

**Why not just re-import `.env`?** Because Parameter Store is the source of
truth and `.env` may be older. `parameter-store.mdx` tells the founder to
rotate exposed secrets by editing their parameters in the console, and the box
only learns those values at the next `config-sync`. So copy
parameter-to-parameter, and keep `scripts/ops/env_to_ssm.sh --dry-run` as the
fallback for anything this misses.

What it does and does not do:

- Values never reach the process list — `put-parameter` is called with
  `--cli-input-json file://` against a 0600 temp file.
- Output is names only. No value is printed, not even truncated.
- Nothing existing is replaced without `--overwrite`.
- `SecureString` stays `SecureString`, written under `alias/aws/ssm`.
- It refuses to run if both profiles resolve to the same account.
- It does **not** copy KMS key policies, parameter policies or tags.

## The three things that go wrong

**The OIDC `sub` claim is not what it looks like.** GitHub stamps the owner's
and repository's immutable numeric ids into the subject
(`repo:owner@<owner_id>/repo@<repo_id>:...`), so a plain-name pattern never
matches and every run fails at "Authenticate to AWS" with
`Not authorized to perform sts:AssumeRoleWithWebIdentity` however correct the
rest looks. `trust.json.template` wildcards past it. Confirm the real `sub`
in CloudTrail if it ever needs re-diagnosing.

**The instance profile association does not travel.** IAM roles are global,
but attaching one is per-instance and does not follow an AMI copy or a
snapshot restore. This is the step people skip.

**A green deploy does not mean a finished migration.** `ci_deploy.sh` gates on
`GET /api/v1/health`, which reads configuration and returns. It never opens a
database connection, never touches Redis, never asks S3 anything — so it says
`ok` on an empty schema, on a bucket still in the old account, and on a
regenerated credential secret. Use `scripts/verify_region_migration.py` and
`scripts/verify_managed_model_tier.py` instead.
