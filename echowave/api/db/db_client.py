from api.db.activation_client import ActivationClient
from api.db.agent_event_client import AgentEventClient
from api.db.agent_task_client import AgentTaskClient
from api.db.agent_trigger_client import AgentTriggerClient
from api.db.api_key_client import APIKeyClient
from api.db.app_interaction_client import AppInteractionClient
from api.db.bot_event_webhook_client import BotEventWebhookClient
from api.db.bot_trigger_client import BotTriggerClient
from api.db.browser_client import BrowserClient
from api.db.campaign_client import CampaignClient
from api.db.contact_client import ContactClient
from api.db.do_not_call_client import DoNotCallClient
from api.db.email_verification_client import EmailVerificationClient
from api.db.embed_token_client import EmbedTokenClient
from api.db.escalation_client import EscalationClient
from api.db.file_folder_client import FileFolderClient
from api.db.folder_client import FolderClient
from api.db.integration_client import IntegrationClient
from api.db.knowledge_base_client import KnowledgeBaseClient
from api.db.kyc_client import KycClient
from api.db.meeting_client import MeetingClient
from api.db.member_connection_client import MemberConnectionClient
from api.db.missed_call_client import MissedCallClient
from api.db.organisation_fact_client import OrganisationFactClient
from api.db.organisation_skill_client import OrganisationSkillClient
from api.db.organization_client import OrganizationClient
from api.db.organization_configuration_client import OrganizationConfigurationClient
from api.db.organization_usage_client import OrganizationUsageClient
from api.db.password_reset_client import PasswordResetClient
from api.db.reports_client import ReportsClient
from api.db.routine_client import RoutineClient
from api.db.sandbox_job_client import SandboxJobClient
from api.db.site_project_client import SiteProjectClient
from api.db.telephony_configuration_client import TelephonyConfigurationClient
from api.db.telephony_phone_number_client import TelephonyPhoneNumberClient
from api.db.tool_client import ToolClient
from api.db.training_loop_client import TrainingLoopClient
from api.db.user_client import UserClient
from api.db.verified_number_client import VerifiedNumberClient
from api.db.webhook_credential_client import WebhookCredentialClient
from api.db.webhook_delivery_client import WebhookDeliveryClient
from api.db.workflow_client import WorkflowClient
from api.db.workflow_recording_client import WorkflowRecordingClient
from api.db.workflow_run_client import WorkflowRunClient
from api.db.workflow_run_text_session_client import WorkflowRunTextSessionClient
from api.db.workflow_template_client import WorkflowTemplateClient


class DBClient(
    AgentEventClient,
    EscalationClient,
    TrainingLoopClient,
    MeetingClient,
    RoutineClient,
    BotTriggerClient,
    AgentTaskClient,
    AppInteractionClient,
    OrganisationFactClient,
    OrganisationSkillClient,
    KycClient,
    MissedCallClient,
    ActivationClient,
    DoNotCallClient,
    EmailVerificationClient,
    PasswordResetClient,
    VerifiedNumberClient,
    WorkflowClient,
    WorkflowRunClient,
    WorkflowRunTextSessionClient,
    UserClient,
    OrganizationClient,
    OrganizationConfigurationClient,
    OrganizationUsageClient,
    IntegrationClient,
    MemberConnectionClient,
    WorkflowTemplateClient,
    CampaignClient,
    ContactClient,
    ReportsClient,
    SandboxJobClient,
    SiteProjectClient,
    BrowserClient,
    APIKeyClient,
    EmbedTokenClient,
    AgentTriggerClient,
    WebhookCredentialClient,
    WebhookDeliveryClient,
    BotEventWebhookClient,
    ToolClient,
    KnowledgeBaseClient,
    FileFolderClient,
    WorkflowRecordingClient,
    TelephonyConfigurationClient,
    TelephonyPhoneNumberClient,
    FolderClient,
):
    """
    Unified database client that combines all specialized database operations.

    This client inherits from:
    - WorkflowClient: handles workflow and workflow definition operations
    - WorkflowRunClient: handles workflow run operations
    - UserClient: handles user and user configuration operations
    - OrganizationClient: handles organization operations
    - OrganizationConfigurationClient: handles organization configuration operations
    - OrganizationUsageClient: handles organization usage reporting aggregates
    - IntegrationClient: handles integration operations
    - MemberConnectionClient: which member owns which Composio connected account (WS-1)
    - WorkflowTemplateClient: handles workflow template operations
    - CampaignClient: handles campaign operations
    - ReportsClient: handles reports and analytics operations
    - APIKeyClient: handles API key operations
    - EmbedTokenClient: handles embed token and session operations
    - AgentTriggerClient: handles agent trigger operations for API-based call triggering
    - WebhookCredentialClient: handles webhook credential operations
    - WebhookDeliveryClient: handles durable outbound webhook delivery records
    - BotEventWebhookClient: handles where a bot posts its own events
    - ToolClient: handles tool operations for reusable HTTP API tools
    - KnowledgeBaseClient: handles knowledge base document and vector search operations
    - FolderClient: handles folder operations for grouping workflows (agents)
    """

    pass
