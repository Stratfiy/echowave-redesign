# Auto-deploy from GitHub, without an SSH key

`\.github/workflows/deploy.yml` deploys to the EC2 box on every push to `main`,
and on demand for any branch. This is the one-time setup that makes it work.

**It uses AWS Systems Manager, not SSH.** That is the point of it:

- **Port 22 can stay closed to the world.** The SSM agent connects outbound;
  nothing has to be open inbound.
- **There is no key.** No PEM in GitHub secrets, none on a laptop, none to leak
  or rotate. The runner asks GitHub for a short-lived OIDC token and trades it
  for an IAM role that lasts one job.
- **Access is revocable in one place.** Delete the trust policy and every future
  run stops, without touching any machine.
- **Everything is in CloudTrail**, with the workflow run id attached.

Budget 20 minutes. Steps 1–3 are AWS, step 4 is GitHub, step 5 is the test.

---

## 1. Let the instance talk to SSM

The agent is pre-installed on Amazon Linux and on Ubuntu images since 16.04. It
needs an instance profile with the managed policy:

```bash
aws iam create-role --role-name DecibylEC2SSM \
  --assume-role-policy-document '{
    "Version":"2012-10-17",
    "Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

aws iam attach-role-policy --role-name DecibylEC2SSM \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore

aws iam create-instance-profile --instance-profile-name DecibylEC2SSM
aws iam add-role-to-instance-profile \
  --instance-profile-name DecibylEC2SSM --role-name DecibylEC2SSM

# Attach it to the running instance — no restart needed
aws ec2 associate-iam-instance-profile \
  --instance-id <i-xxxxxxxx> \
  --iam-instance-profile Name=DecibylEC2SSM
```

Confirm the instance has checked in — this should list it within a minute or two:

```bash
aws ssm describe-instance-information \
  --query "InstanceInformationList[].{Id:InstanceId,Ping:PingStatus}" --output table
```

If it does not appear, the agent is not running or has no route out:
`sudo systemctl status amazon-ssm-agent` on the box.

## 2. Trust GitHub's OIDC provider

Once per AWS account:

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1
```

## 3. Create the role the workflow assumes

**Scope the trust to this repository, and to the branch you deploy from.** A
wildcard here means any workflow in any repo you own can deploy production.

`trust.json` — replace the account id and the owner/repo:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com" },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
      },
      "StringLike": {
        "token.actions.githubusercontent.com:sub": [
          "repo:Stratfiy/echowave-redesign:ref:refs/heads/main",
          "repo:Stratfiy/echowave-redesign:environment:production"
        ]
      }
    }
  }]
}
```

`policy.json` — only the two SSM calls the workflow makes, on the one instance:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "ssm:SendCommand",
      "Resource": [
        "arn:aws:ssm:*:*:document/AWS-RunShellScript",
        "arn:aws:ec2:*:*:instance/<i-xxxxxxxx>"
      ]
    },
    {
      "Effect": "Allow",
      "Action": ["ssm:GetCommandInvocation", "ssm:ListCommandInvocations"],
      "Resource": "*"
    }
  ]
}
```

```bash
aws iam create-role --role-name DecibylGitHubDeploy \
  --assume-role-policy-document file://trust.json
aws iam put-role-policy --role-name DecibylGitHubDeploy \
  --policy-name DecibylDeploySSM --policy-document file://policy.json
aws iam get-role --role-name DecibylGitHubDeploy --query Role.Arn --output text
```

Keep that ARN for the next step.

## 4. Tell GitHub

**Settings → Secrets and variables → Actions.**

| Kind | Name | Value |
|---|---|---|
| Secret | `AWS_DEPLOY_ROLE_ARN` | the ARN from step 3 |
| Secret | `EC2_INSTANCE_ID` | `i-xxxxxxxx` |
| Variable | `AWS_REGION` | e.g. `us-east-1` — **see the note below** |
| Variable | `DEPLOY_PROJECT_DIR` | only if the clone is not at `/home/ubuntu/echowave-redesign/echowave` |
| Variable | `DEPLOY_RUN_AS` | only if the box user is not `ubuntu` |

Neither of those "secrets" is a credential — an ARN and an instance id are
identifiers, useless without the trust policy. They are secrets only to keep
account details out of public logs.

Then **Settings → Environments → `production`**. Add required reviewers if you
want a human to approve each production deploy; the workflow already targets
that environment, so the gate applies with no further change.

> **Region.** The instance is in `us-east-1` today. That is the data-residency
> problem in `PROVIDER-PRICING.md` and `KNOWN_ISSUES.md` #16 — call recordings
> of Indian farmers should be in `ap-south-1`. When you migrate, change this
> variable and the instance id together.

## 5. Test it before trusting it

Run it by hand first, against this branch, so a broken setup is not discovered
by a push to `main`:

**Actions → Deploy → Run workflow**, and set *ref* to the branch you want.

Watch for: the AWS auth step succeeding (proves OIDC), the SSM command being
accepted (proves the role policy), and the deploy output appearing in the log
(proves the instance ran it). The health check must pass before the job is
green.

---

## What the deploy actually does

`scripts/ci_deploy.sh`, on the box:

1. Records the current commit, so it can roll back.
2. Fetches and checks out the requested ref, submodules included.
3. `docker compose build` then `up -d`.
4. `alembic upgrade head`.
5. Polls `/api/v1/health` for up to 150 seconds.
6. Prints the billing readiness findings — informational only.

**If any step fails it checks out the previous commit and rebuilds.** The one
thing it does not roll back is the database: a migration that half-applied needs
a person, and pretending otherwise turns one bad deploy into a corrupted
database.

**It never touches `.env`.** Configuration is yours, it holds live Razorpay keys
and the credential secret, and a deploy that rewrites it is a deploy that breaks
the platform by being run twice. A release needing a new variable needs it added
by hand first — `DEPLOY.md` §3.

## Things worth knowing

**It is not zero-downtime.** `docker compose up -d` replaces containers, so
calls in flight are cut. `scripts/rolling_update.sh` exists for draining
workers on the bare-metal path; wiring that into this workflow is the obvious
next improvement, and matters as soon as real campaigns are running.

**Deploys are serialised.** Two runs cannot overlap; the second waits. It does
not cancel the first, because a half-finished deploy is worse than a queued one.

**Doc-only pushes do not deploy.** `paths-ignore` covers `**/*.md` and `docs/`.

**To stop deploys entirely**, delete the IAM role from step 3. Every future run
fails at authentication, and nothing on the instance changes.
