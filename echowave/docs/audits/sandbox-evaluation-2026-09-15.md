# Sandbox evaluation for agent code execution (working plan, Steps 5 and 6)

Evaluated 15 September 2026 in the founder's AWS Mumbai account (region ap-south-1). Repository `Stratfiy/echowave-redesign`, harness `echowave/scripts/sandbox_checks/five_checks.py` on branch `sandbox-checks-steps-5-6`.

**Residency:** everything that ran, ran inside the founder's AWS Mumbai account on one EC2 instance. Only the *self-hosted* form of each candidate was evaluated: Daytona from the public `daytonaio/daytona` source at tag v0.190.0, and E2B from the public `e2b-dev/infra` source at tag 2026.30. No hosted or managed vendor service was used, no vendor account was created, and no data left the account except Docker image pulls. The hourly prices quoted below are AWS EC2 prices, not vendor prices.

## Result in one line

Daytona self-hosted works and passes four of five checks, but its sandboxes are privileged Docker containers and its control plane phones home to PostHog unless that is switched off. E2B self-hosted could not be stood up in this account, because Firecracker needs a bare-metal instance of 96 vCPUs and the account's EC2 quota is 8. The recommendation for Step 7 is to build our own small sandbox runner to the sandbox-as-tool contract.

## Candidate one: Daytona self-hosted (source v0.190.0, images v0.189.0)

| Check | Verdict | Evidence |
|---|---|---|
| 1. Container or micro-VM | Yes, container | Runner-side inspection of a live sandbox: each sandbox is a runc Docker container, started privileged with seccomp disabled and no CPU or memory limits, sharing the host kernel (the same kernel string inside the box and on the host). The user process runs as a non-root user with an empty capability set. |
| 2. Egress denied by default, allowed per job for a named host | Yes | Block-all box: connections to 1.1.1.1:443 and example.com:443 both failed. Allow-list box: example.com opened, 1.1.1.1 still failed. Firewall rules seen live in the runner during the run: a per-sandbox chain with RETURN for the two allowed /32 addresses and DROP for everything else, hooked from Docker's DOCKER-USER chain. Daytona's allow list takes IP ranges, not hostnames, so the harness resolves the named host first. |
| 3. No secrets in the box, no metadata endpoint | Yes | The only key-like environment variable is `GPG_KEY`, the public signing fingerprint shipped in the official Python image. No AWS files. The instance metadata service at 169.254.169.254 was unreachable, both a plain TCP connect and an IMDSv2 token request. (The harness printed "no" for this row purely because of that variable's name.) |
| 4. No call-home to the vendor during a plain run | No as shipped, yes after one change | Inside the box: no connections at all. Control plane: during a plain create, run and delete, the Daytona API container opened a completed HTTPS connection to 18.239.115.71, a CloudFront address that the PostHog host baked into Daytona's compose file resolves to. With the PostHog key blanked and the API container recreated, a second capture over two sandbox runs and 45 seconds idle showed no vendor contact; the only public traffic was the host operating system's own time sync. |
| 5. A Python client call runs a script and returns output | Yes | Client on the same host: first sandbox create 3.0 s, later creates 0.5 to 0.65 s, script run 0.07 to 0.1 s, output returned. |

**Setup time:** about 10 minutes of machine time from launch to the first successful run, about 35 minutes including the fixes listed under decisions. **Cost:** one t3.large for 19 minutes at USD 0.0896 an hour, about USD 0.03; the 40 GB root volume added less than a cent. **Caveats:** fourteen containers in the shipped compose stack; the public repository is frozen at v0.190.0 under AGPL-3.0, so no further security fixes will arrive; Docker Hub does not even carry v0.190.0 images for the API, runner or SSH gateway.

## Candidate two: E2B self-hosted (`e2b-dev/infra` tag 2026.30, Apache-2.0)

| Check | Verdict | Evidence |
|---|---|---|
| 1. Container or micro-VM | Micro-VM by design, not verified | Their architecture document and orchestrator code run each sandbox as a Firecracker micro-VM in its own network namespace. Nothing was run. |
| 2. Egress denied by default, allowed per job for a named host | No by default; per-job control exists | The orchestrator source says "Internet access is allowed by default" and the Python SDK's `allow_internet_access` defaults to true. Per-sandbox allow and deny lists, including hostnames, are enforced with nftables inside the sandbox's network namespace, and a deny-all mode exists. |
| 3. No secrets in the box, no metadata endpoint | Unclear | Not run. |
| 4. No call-home to the vendor during a plain run | Unclear, likely none | The runtime code has no vendor endpoints; one command-line helper contacts api.e2b.dev only when asked to pull a template. The SDK defaults to the vendor's e2b.app domain unless a self-hosted domain is set, so any future test must set that. Not verified at runtime. |
| 5. A Python client call runs a script and returns output | Unclear | Not run. |

**Setup time:** not stood up. **Cost:** USD 0. **Why:** Firecracker needs KVM, which on EC2 means a bare-metal instance (c5.metal is 96 vCPUs at USD 4.08 an hour in Mumbai; m5.metal USD 4.85). This account's quota for both on-demand and Spot standard instances is 8 vCPUs, of which 4 are used by the existing production server, so no bare-metal launch is possible by any route. Even with quota, E2B's supported self-host path is Terraform with Nomad, Consul, Postgres, ClickHouse, object storage and a Cloudflare zone, and their AWS support is marked beta. Their local-development path also requires KVM.

## Created and deleted in AWS

| Resource | Created | Deleted |
|---|---|---|
| Security group `decibyl-sandbox-eval-daytona` (sg-0899f3af7d9924d0f), no inbound rules | 12:33 UTC | 12:52 UTC |
| EC2 instance i-058eebd6801bc9771, t3.large, Ubuntu 24.04, IMDSv2 required, hop limit 1, instance profile `decibyl-ec2-ssm` (pre-existing) | 12:33 UTC | terminated 12:52 UTC |
| 40 GB gp3 root volume of that instance | 12:33 UTC | deleted with the instance |

No key pairs, IAM roles, instance profiles or VPC changes were made. The pre-existing t3.xlarge server "Decibyl-india" was not touched. A query for resources tagged `decibyl-sandbox-eval=true` returns nothing live. Inside the instance, a Daytona organisation and a sandbox-scoped API key existed in a root-only file; both died with the instance.

## Recommendation for Step 7

Build ours to the sandbox-as-tool contract rather than adopting either candidate. Daytona gives the weaker isolation (a privileged container) wrapped in a large, frozen stack with telemetry on by default. E2B has the isolation we want but cannot run in this account today and carries a heavy operational footprint. The parts of Daytona that earned a "yes" are small: a Docker container per job, a default-drop firewall chain with a per-job allow list, no credentials passed in, and the metadata service kept out by the instance's own hop limit. That can be reproduced on one ordinary EC2 host in a few hundred lines, with the container unprivileged, seccomp on, capabilities dropped, and CPU and memory limits set, which is already stricter than Daytona. The harness keeps the loop and every credential, creates the box on demand, runs code, files and shell only, bridges tool calls back to the harness, and stores state in our own Postgres so a crashed box loses nothing. If a VM boundary is wanted later, request an EC2 quota increase to 96 vCPUs for standard instances (a form in the console, usually about a day); a two-hour Firecracker or E2B trial would then cost about USD 9.

## Decisions not in the brief, and two security notes

- **Rotate the evaluation key now.** Early in the session a shell check printed the key's values into the session transcript. The key is also not least-privilege: besides the scoped inline policy, the IAM user `decibyl-sandbox-eval` carries AdministratorAccess and several other managed policies. The evaluation stayed within the intended scope (EC2, SSM and read-only lookups in ap-south-1) and did not use that extra power.
- **Images:** Docker Hub has no v0.190.0 images for the Daytona API, runner or SSH gateway, so v0.189.0 images ran under the v0.190.0 configuration. The MinIO image was switched to quay.io because Docker Hub no longer serves it.
- **Access without a browser:** dex's password grant was enabled in its config, the organisation's default region was set through the API, and a sandbox-scoped API key was minted and kept in a root-only file on the instance.
- **Client location:** the evaluation session has no raw network access, only HTTPS through a proxy, so the host was driven over SSM and the client ran on the host itself. The security group therefore had no inbound rules at all, stricter than "from your own IP".
- **PostHog:** measured on first, then disabled for a second capture.
- **Harness:** the adapters were changed to match the real SDKs (Daytona takes a params object, CIDRs and a region target; E2B exposes `allow_internet_access`). The probe the box runs is unchanged. Committed on branch `sandbox-checks-steps-5-6`; no pull request was opened.
- **Not done:** no quota increase was requested, and the pre-existing production instance was not touched.
