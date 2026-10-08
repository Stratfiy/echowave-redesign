# Source this in a staging workflow step: the two test accounts, as env.
#
# The GitHub `staging` environment's secrets win. Without them, the accounts
# created on the box are read from its root-only file (echowave/STAGING.md),
# and the passwords are masked in the log before anything can print them.
# Never echoes a password; never writes one to a file.

if [ -z "${STAGING_EMAIL_A:-}" ] && sudo test -f /home/ubuntu/decibyl-staging/check.env; then
  while IFS='=' read -r key value; do
    case "$key" in
      STAGING_URL|STAGING_UI_URL|STAGING_EMAIL_A|STAGING_PASSWORD_A|STAGING_EMAIL_B|STAGING_PASSWORD_B)
        case "$key" in *PASSWORD*) echo "::add-mask::$value" ;; esac
        export "$key=$value" ;;
    esac
  done < <(sudo cat /home/ubuntu/decibyl-staging/check.env)
fi
