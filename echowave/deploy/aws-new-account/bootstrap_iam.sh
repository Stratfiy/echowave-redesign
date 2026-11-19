#!/usr/bin/env bash
# Create the IAM that a NEW AWS account needs to run Decibyl and be deployed to.
#
# Adapted from docs/DEPLOY-GITHUB-ACTIONS.md and
# docs/deployment/parameter-store.mdx, which describe the same setup for the
# account the platform is leaving. Read those for the reasoning; this script is
# only the ordered, repeatable version of them.
#
#   export AWS_PROFILE=decibyl-new
#   export NEW_ACCOUNT_ID=123456789012
#   export NEW_INSTANCE_ID=i-0abc...        # once the box exists
#   export NEW_S3_BUCKET=... NEW_KYC_BUCKET=... NEW_BACKUP_MIRROR_BUCKET=...
#   export OPS_SECRET_NAMESPACE=decibyl/prod/providers/   # only if used
#   bash bootstrap_iam.sh --dry-run
#   bash bootstrap_iam.sh
#
# Safe to re-run: every create tolerates an existing entity, and the two
# inline policies are overwritten with what the templates now say.
#
# It does NOT create the instance, the buckets, or the Parameter Store
# entries. Buckets first (their names go in the instance policy), then the
# instance, then this, then copy_parameter_store.sh.

set -euo pipefail

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGION="${AWS_REGION:-ap-south-1}"

: "${NEW_ACCOUNT_ID:?set NEW_ACCOUNT_ID to the 12-digit id of the new account}"

run() {
    if [ "$DRY_RUN" = 1 ]; then
        printf 'would run: %s\n' "$*"
    else
        "$@"
    fi
}

# Tolerate "already exists" without hiding other failures.
allow_exists() {
    local out status
    if [ "$DRY_RUN" = 1 ]; then printf 'would run: %s\n' "$*"; return 0; fi
    set +e
    out="$("$@" 2>&1)"; status=$?
    set -e
    if [ $status -ne 0 ]; then
        case "$out" in
            *EntityAlreadyExists* | *already\ exists* | *LimitExceeded*\ *provider*) echo "exists already, left as it is" ;;
            *) printf '%s\n' "$out" >&2; return $status ;;
        esac
    fi
}

render() {
    # Substitute the placeholders in a template. Only the names we set, so an
    # unset one stays visible as a placeholder rather than becoming an empty
    # string that silently widens a policy.
    local src="$1" dest="$2"
    sed -e "s|NEW_ACCOUNT_ID|${NEW_ACCOUNT_ID}|g" \
        -e "s|NEW_INSTANCE_ID|${NEW_INSTANCE_ID:-NEW_INSTANCE_ID}|g" \
        -e "s|NEW_S3_BUCKET|${NEW_S3_BUCKET:-NEW_S3_BUCKET}|g" \
        -e "s|NEW_KYC_BUCKET|${NEW_KYC_BUCKET:-NEW_KYC_BUCKET}|g" \
        -e "s|NEW_BACKUP_MIRROR_BUCKET|${NEW_BACKUP_MIRROR_BUCKET:-NEW_BACKUP_MIRROR_BUCKET}|g" \
        -e "s|OPS_SECRET_NAMESPACE|${OPS_SECRET_NAMESPACE:-OPS_SECRET_NAMESPACE}|g" \
        "$src" > "$dest"
    if grep -qE 'NEW_[A-Z0-9_]+|OPS_SECRET_NAMESPACE|FALLBACK_MODEL_ID|CHEAP_MODEL_ID|EMBEDDING_MODEL_ID' "$dest"; then
        echo
        echo "NOTE: $dest still contains placeholders:"
        grep -oE 'NEW_[A-Z0-9_]+|OPS_SECRET_NAMESPACE|FALLBACK_MODEL_ID|CHEAP_MODEL_ID|EMBEDDING_MODEL_ID' "$dest" | sort -u | sed 's/^/  - /'
        echo "Fill them in, or delete the statements you do not need, before applying."
        echo
    fi
}

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "== account ${NEW_ACCOUNT_ID}, region ${REGION} =="
echo

echo "-- 1. rendering policies"
render "$HERE/trust.json.template"               "$WORK/trust.json"
render "$HERE/deploy-role-policy.json.template"  "$WORK/deploy-policy.json"
render "$HERE/instance-role-policy.json.template" "$WORK/instance-policy.json"
for f in trust.json deploy-policy.json instance-policy.json; do
    python3 -c "import json,sys;json.load(open(sys.argv[1]))" "$WORK/$f"
done
echo "   all three parse as JSON"

echo "-- 2. GitHub's OIDC provider (once per account)"
allow_exists aws iam create-open-id-connect-provider \
    --url https://token.actions.githubusercontent.com \
    --client-id-list sts.amazonaws.com \
    --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1

echo "-- 3. the instance role and its profile"
allow_exists aws iam create-role --role-name DecibylEC2SSM \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
run aws iam attach-role-policy --role-name DecibylEC2SSM \
    --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
run aws iam put-role-policy --role-name DecibylEC2SSM \
    --policy-name decibyl-prod-runtime --policy-document "file://$WORK/instance-policy.json"
allow_exists aws iam create-instance-profile --instance-profile-name DecibylEC2SSM
allow_exists aws iam add-role-to-instance-profile \
    --instance-profile-name DecibylEC2SSM --role-name DecibylEC2SSM

echo "-- 4. the role GitHub Actions assumes"
allow_exists aws iam create-role --role-name DecibylGitHubDeploy \
    --assume-role-policy-document "file://$WORK/trust.json"
run aws iam update-assume-role-policy --role-name DecibylGitHubDeploy \
    --policy-document "file://$WORK/trust.json"
run aws iam put-role-policy --role-name DecibylGitHubDeploy \
    --policy-name DecibylDeploySSM --policy-document "file://$WORK/deploy-policy.json"

if [ -n "${NEW_INSTANCE_ID:-}" ]; then
    echo "-- 5. attaching the profile to ${NEW_INSTANCE_ID}"
    echo "   (IAM roles are global; this association is per-instance and does"
    echo "    NOT follow an AMI copy or a snapshot restore)"
    allow_exists aws ec2 associate-iam-instance-profile \
        --instance-id "$NEW_INSTANCE_ID" \
        --iam-instance-profile Name=DecibylEC2SSM --region "$REGION"
else
    echo "-- 5. skipped: NEW_INSTANCE_ID is not set"
    echo "   Once the box exists, re-run with it set, and re-run step 4 too so"
    echo "   the deploy policy pins ssm:SendCommand to the real instance."
fi

echo
echo "== next =="
if [ "$DRY_RUN" = 1 ]; then
    echo "That was a dry run. Nothing was created."
else
    echo "Deploy role ARN, for the GitHub secret AWS_DEPLOY_ROLE_ARN:"
    aws iam get-role --role-name DecibylGitHubDeploy --query Role.Arn --output text || true
    echo
    echo "Confirm the box has checked in to SSM before trusting any of it:"
    echo "  aws ssm describe-instance-information --region ${REGION} \\"
    echo "    --query \"InstanceInformationList[].{Id:InstanceId,Ping:PingStatus}\" --output table"
fi
