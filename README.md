# Decibyl

**Agents talk, remember, act, and learn from outcomes.**

Decibyl is being shaped for individuals and businesses: a main assistant that
uses relevant memory, coordinates specialist agents, and helps complete work.
Voice, chat and connected apps are ways to interact and act.

## Product direction and current implementation

The intended experience is organized into separate personal and business
**vaults**, inspired by Obsidian. Each vault contains its own Decibyl assistant,
specialists, memory, tasks, files and connected apps. Business vaults also
include human teammates. Memory should be visible and editable, actions
traceable, and access controlled within and between vaults.

The application already contains a workspace assistant, specialist agents,
templates and marketplace shelves, shared and agent-specific memory, a memory
graph with Obsidian export, task delegation, recurring routines and approval
cards. These are implementation foundations, not a claim that the complete
vault experience has shipped.

Personal/business vault semantics, granular teammate access, linked-note
editing, consistent action permissions and bounded recurring authorization
still need tailoring and validation. Learning from outcomes is the product
principle; measured improvement is not yet a claim established by this README.

The agreed default is to research, organize and draft automatically, while
sending messages, spending money and deleting information require approval.
Users can grant limited recurring authorization and review an agent's role,
tools, memory access and permissions before activation. Existing execution
paths do not yet establish this as one universally enforced policy.

## Repository

The production application lives in [`echowave/`](echowave/), not the top-level preview scaffolding.

## Start here

- [Application overview and setup](echowave/README.md)
- [Development guide](echowave/DEVELOPING.md)
- [Contributor instructions](echowave/AGENTS.md)
- [Frontend](echowave/ui/) — Next.js dashboard
- [Backend](echowave/api/) — FastAPI services and voice runtime
- [Python SDK](echowave/sdk/python/README.md) and [TypeScript SDK](echowave/sdk/typescript/README.md)
- [Documentation](echowave/docs/README.md)
- [Platform audit and improvement backlog, 7 September 2026](echowave/docs/audits/2026-09-07-platform-review.md)

The top-level `frontend/` and `backend/` directories are retained legacy preview/scaffold code.
Use the nested application's setup instructions and tests when contributing to the live product.
