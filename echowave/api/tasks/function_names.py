class FunctionNames:
    RUN_INTEGRATIONS_POST_WORKFLOW_RUN = "run_integrations_post_workflow_run"
    PROCESS_WORKFLOW_COMPLETION = "process_workflow_completion"
    SYNC_CAMPAIGN_SOURCE = "sync_campaign_source"
    PROCESS_CAMPAIGN_BATCH = "process_campaign_batch"
    PROCESS_KNOWLEDGE_BASE_DOCUMENT = "process_knowledge_base_document"
    TRANSLATE_KNOWLEDGE_BASE_DOCUMENT = "translate_knowledge_base_document"
    DELIVER_WEBHOOK = "deliver_webhook"
    RUN_EVAL_CASE = "run_eval_case"
    RUN_AGENT_ROUTINE = "run_agent_routine"
    #: A webhook rang a bot's doorbell (KAN-137).
    RUN_BOT_TRIGGER = "run_bot_trigger"
    #: A task on the board was handed to a bot (KAN-140 P1).
    RUN_AGENT_TASK = "run_agent_task"
    ANSWER_CHANNEL_MESSAGE = "answer_channel_message"
    #: Decibyl, the workspace assistant, answers on its own thread.
    ANSWER_DECIBYL_MESSAGE = "answer_decibyl_message"
    EXTRACT_DOCUMENT_FIELDS = "extract_document_fields"
    EXPORT_MEMORY = "export_memory"
    #: A confirmed action fires once its undo window has passed.
    RUN_PROPOSED_ACTION = "run_proposed_action"
    COMPACT_CHANNEL_CONTEXT = "compact_channel_context"
    EMAIL_TAX_DOCUMENT = "email_tax_document"
    PLACE_MISSED_CALL_CALLBACK = "place_missed_call_callback"
