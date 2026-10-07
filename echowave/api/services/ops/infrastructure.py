"""Selected infrastructure runbooks through Systems Manager Automation
(handoff 34, "Infrastructure boundary").

The console never runs a shell. Draining or restarting workers is an
approved runbook: an SSM Automation document an infrastructure owner wrote,
reviewed and named in ``OPS_SSM_DOCUMENTS``. This module starts exactly that
document with exactly the parameters the command's schema allows, and reads
its status back. IAM, networking and destructive recovery are not here at
all; they stay restricted infrastructure workflows.

Unconfigured is a state, not an error and never a fake success: a command
whose document is not mapped answers ``needs_setup`` with what to set.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from api import constants


@dataclass(frozen=True)
class RunbookStart:
    started: bool
    execution_id: str | None
    detail: str


def documents() -> dict[str, str]:
    """``OPS_SSM_DOCUMENTS`` parsed: command name -> document name."""
    out: dict[str, str] = {}
    for pair in (constants.OPS_SSM_DOCUMENTS or "").split(","):
        name, _, document = pair.strip().partition("=")
        if name.strip() and document.strip():
            out[name.strip()] = document.strip()
    return out


class SsmAutomationRunner:
    def __init__(self, client: Any = None):
        self._client = client

    def _get(self):
        if self._client is None:
            import boto3

            self._client = boto3.client("ssm")
        return self._client

    async def start(
        self, command: str, parameters: dict[str, list[str]]
    ) -> RunbookStart:
        document = documents().get(command)
        if not document:
            return RunbookStart(
                False,
                None,
                f"No Automation document is mapped for {command}. Set OPS_SSM_DOCUMENTS "
                f"to include '{command}=<DocumentName>' and give the API role "
                "ssm:StartAutomationExecution on that document.",
            )
        if not constants.OPS_SSM_TARGET_INSTANCE_ID:
            return RunbookStart(False, None, "OPS_SSM_TARGET_INSTANCE_ID is not set.")
        params = {"InstanceId": [constants.OPS_SSM_TARGET_INSTANCE_ID], **parameters}
        client = self._get()

        def _start() -> str:
            response = client.start_automation_execution(
                DocumentName=document, Parameters=params
            )
            return str(response["AutomationExecutionId"])

        execution_id = await asyncio.to_thread(_start)
        return RunbookStart(True, execution_id, f"Started {document}.")

    async def status(self, execution_id: str) -> dict[str, Any]:
        client = self._get()

        def _get() -> dict[str, Any]:
            response = client.get_automation_execution(
                AutomationExecutionId=execution_id
            )
            execution = response.get("AutomationExecution") or {}
            return {
                "status": execution.get("AutomationExecutionStatus"),
                "steps": [
                    {"name": step.get("StepName"), "status": step.get("StepStatus")}
                    for step in execution.get("StepExecutions") or []
                ],
            }

        return await asyncio.to_thread(_get)


#: SSM Automation statuses and what they mean for a command row.
TERMINAL = {
    "Success": "succeeded",
    "CompletedWithSuccess": "succeeded",
    "Failed": "failed",
    "TimedOut": "failed",
    "Cancelled": "failed",
    "Rejected": "rejected",
    "CompletedWithFailure": "failed",
}


def get_runner() -> SsmAutomationRunner:
    return SsmAutomationRunner()
