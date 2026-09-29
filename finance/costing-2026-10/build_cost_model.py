"""Build finance/costing-2026-10/cost_model.xlsx.

Every figure lives once, on the Unit prices or Measured usage sheet, with a
label (vendor price / measured / assumption), a source and a date. Every other
sheet is formulas over those cells, so changing an input moves every result.

Run from the repository root:

    python finance/costing-2026-10/build_cost_model.py

Read-only with respect to the application: this script touches nothing but
the workbook it writes.
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HERE = Path(__file__).resolve().parent
OUT = HERE / "cost_model.xlsx"

VENDOR = "vendor price"
MEASURED = "measured"
ASSUMPTION = "assumption"
CODE = "code constant"

wb = Workbook()
R: dict[str, str] = {}  # input key -> absolute cell reference

HEAD = Font(bold=True, color="FFFFFF")
HEAD_FILL = PatternFill("solid", fgColor="1F3A5F")
LABEL_FILL = {
    VENDOR: PatternFill("solid", fgColor="E2F0D9"),
    MEASURED: PatternFill("solid", fgColor="DDEBF7"),
    ASSUMPTION: PatternFill("solid", fgColor="FFF2CC"),
    CODE: PatternFill("solid", fgColor="EDEDED"),
}


def header(ws, row, cols, widths=None):
    for i, c in enumerate(cols, 1):
        cell = ws.cell(row=row, column=i, value=c)
        cell.font = HEAD
        cell.fill = HEAD_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    for i, w in enumerate(widths or [], 1):
        ws.column_dimensions[get_column_letter(i)].width = w


# --------------------------------------------------------------------------
# Sheet 1: Vendors (Step 1 summary, no numbers)
# --------------------------------------------------------------------------
ws = wb.active
ws.title = "Vendors"
header(
    ws,
    1,
    ["Vendor", "Used for", "Where in code", "Billing unit", "Env var names", "Who pays"],
    [22, 46, 46, 22, 46, 26],
)
VENDORS = [
    ("OpenAI", "Managed LLM tiers (gpt-4.1-mini default, gpt-4.1 accurate, gpt-5 advanced); call QA; embeddings; realtime gpt-realtime-2", "api/services/configuration/managed_tiers.py:251-253, 317-321, 360", "per token", "PLATFORM_KEY_LLM_OPENAI, PLATFORM_KEY_LLM_OPENAI_REALTIME", "Platform"),
    ("Sarvam AI", "Managed LLM lite (sarvam-105b-conversations), STT default (saaras:v3), TTS default (bulbul:v3), translation, transcription", "managed_tiers.py:250,270,285; api/services/translation.py:28-94", "LLM per token; STT per hour; TTS per 1k chars", "PLATFORM_KEY_STT_SARVAM, PLATFORM_KEY_TTS_SARVAM, PLATFORM_KEY_LLM_SARVAM", "Platform"),
    ("Google Gemini", "Realtime natural tier (gemini-3.1-flash-live-preview); builder; chat catalogue", "managed_tiers.py:327-331; api/services/pipecat/realtime/gemini_live.py:40", "per token (audio tokens)", "PLATFORM_KEY_LLM_GOOGLE", "Platform when installed"),
    ("Anthropic", "Agent builder first preference (claude-sonnet-5); chat catalogue (Opus 5, Sonnet 5, Haiku 4.5)", "api/constants.py:704-722; api/services/agent_builder/client.py:553", "per token", "PLATFORM_KEY_LLM_ANTHROPIC", "Platform when builder on / model pinned"),
    ("Deepgram", "Managed STT instant tier (Flux); transcription", "managed_tiers.py:278; api/services/gen_ai/transcription/providers.py:130", "per minute", "PLATFORM_KEY_STT_DEEPGRAM", "Platform if key installed"),
    ("ElevenLabs", "Managed TTS global tier (eleven_flash_v2_5)", "managed_tiers.py:300", "per 1k chars", "PLATFORM_KEY_TTS_ELEVENLABS", "Platform"),
    ("Smallest.ai", "Managed TTS natural tier (lightning_v3.1_pro)", "managed_tiers.py:297", "per 1k chars", "PLATFORM_KEY_TTS_SMALLEST", "Platform"),
    ("Rumik AI", "Managed TTS basic tier (mulberry)", "managed_tiers.py:289", "per 1k chars", "PLATFORM_KEY_TTS_RUMIK", "Platform"),
    ("Plivo", "Managed numbers, carriage, verification SMS", "api/constants.py:330-358; api/services/telephony/providers/plivo/provider.py:596", "per minute; per number-month; per SMS", "PLATFORM_PLIVO_AUTH_ID, PLATFORM_PLIVO_AUTH_TOKEN, NUMBER_RENTAL_COST_PAISE", "Platform"),
    ("Twilio", "Verification SMS fallback; BYOK carrier; overseas reference carrier", "api/constants.py:1493; api/services/telephony/verification_sender.py:231", "per minute; per SMS", "PLATFORM_TWILIO_ACCOUNT_SID, PLATFORM_TWILIO_AUTH_TOKEN", "Platform if configured"),
    ("Meta WhatsApp Cloud API", "Platform WhatsApp number send/receive", "api/services/messaging/platform_whatsapp.py:22-31; send.py:265", "per template message", "WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID", "Platform"),
    ("Serper", "Agent web search", "api/services/workflow/web_tools.py:52-54,152", "per request", "SERPER_API_KEY", "Platform"),
    ("Composio", "Connector layer for every non-Google integration", "api/constants.py:226-232; api/services/integrations/composio/client.py:116", "per tool call past free tier", "COMPOSIO_API_KEY", "Platform"),
    ("Razorpay", "Payments, mandates, plans", "api/services/billing/payments.py:354", "% of transaction (MDR)", "RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET", "Platform"),
    ("AWS EC2 / EBS", "Production host: one box running compose (api, ui, postgres, redis, minio, falkordb, gotenberg, sandbox, coturn, nginx)", ".github/workflows/deploy.yml:89; INFRASTRUCTURE.md", "per instance-hour; per GB-month", "EC2_INSTANCE_ID (secret), AWS_REGION", "Platform"),
    ("AWS S3 / MinIO", "Recordings, KB files, KYC docs, backups (MinIO self-hosted is the default)", "api/services/storage.py:44-60", "per GB-month", "ENABLE_AWS_S3, S3_BUCKET, BACKUP_MIRROR_*", "Platform if S3"),
    ("PostHog", "Product analytics", "api/services/posthog_client.py", "per event (free tier)", "POSTHOG_API_KEY", "Vendor free tier likely"),
    ("Sentry", "Error monitoring", "api/app.py:21; ui/src/instrumentation-client.ts", "per event / plan", "SENTRY_DSN", "Vendor if DSN set"),
    ("Langfuse", "Tracing (langfuse.decibyl.ai, self-hosted)", "api/services/pipecat/tracing_config.py:155", "self-host or per trace", "LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY", "Unknown: self-hosted or cloud"),
    ("SMTP relay (vendor unnamed)", "Transactional email", "api/services/messaging/email.py:18", "per email", "SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD", "Platform"),
    ("Self-hosted (no vendor bill)", "Postgres+pgvector, Redis/ARQ, FalkorDB, Gotenberg, Tesseract/Docling, sandbox, coturn, nginx, cloudflared", "docker-compose.yaml", "infra only", "", "Infra"),
    ("BYOK only (no platform bill)", "OpenRouter, Groq, Cerebras, DeepSeek, Mistral, Fireworks, Azure, Bedrock, Cartesia, Speechmatics, Gladia, AssemblyAI, Vonage, Telnyx, Cloudonix, Vobiz, Exotel/Smartflo import", "api/services/pipecat/service_factory.py", "customer's account", "", "Customer"),
]
for i, row in enumerate(VENDORS, 2):
    for j, v in enumerate(row, 1):
        ws.cell(row=i, column=j, value=v).alignment = Alignment(wrap_text=True, vertical="top")
ws.cell(row=len(VENDORS) + 3, column=1, value="Full inventory with file:line for every hit: step1_inventory.md in this folder.")

# --------------------------------------------------------------------------
# Sheet 2: Unit prices (every vendor price; column E is the value)
# --------------------------------------------------------------------------
up = wb.create_sheet("Unit prices")
header(
    up,
    1,
    ["Key", "Vendor", "Item", "Unit", "Value", "Currency", "Label", "Source URL", "Date checked", "Note"],
    [26, 16, 40, 22, 12, 9, 14, 60, 12, 60],
)
_uprow = [2]


def price(key, vendor, item, unit, value, currency, label, url, date, note=""):
    r = _uprow[0]
    for j, v in enumerate([key, vendor, item, unit, value, currency, label, url, date, note], 1):
        c = up.cell(row=r, column=j, value=v)
        c.alignment = Alignment(wrap_text=True, vertical="top")
    up.cell(row=r, column=7).fill = LABEL_FILL[label]
    R[key] = f"'Unit prices'!$E${r}"
    _uprow[0] += 1


# ---- FX -------------------------------------------------------------------
RBI_URL = "https://www.rbi.org.in/Scripts/ReferenceRateArchive.aspx"
price("fx", "RBI / FBIL", "USD to INR reference rate, 28 Sep 2026 (latest published before 29 Sep)", "INR per USD", 95.968, "INR", VENDOR, "https://www.fbil.org.in/ (value read from CEIC, which republishes the FBIL/RBI series; the FBIL page itself could not be fetched)", "2026-09-28", "UNVERIFIED on the official page: fbil.org.in timed out and the RBI archive needs a form post. Confirm at fbil.org.in before quoting. The billing code falls back to 96.00 (api/services/billing/money.py:49) and stores its own rate history from Frankfurter.")
price("gst", "GoI", "GST on foreign vendor bills under reverse charge (creditable ITC; cash-flow only)", "share", 0.18, "", VENDOR, "https://cbic-gst.gov.in/", "2026-09-29", "Not added to unit costs: input tax credit is claimed. It is a cash-flow timing item on every foreign invoice.")

# ---- LLM (USD per 1M tokens) -----------------------------------------------
OA = "https://developers.openai.com/api/docs/pricing"
AN = "https://platform.claude.com/docs/en/about-claude/pricing"
GG = "https://ai.google.dev/gemini-api/docs/pricing"
SV = "https://docs.sarvam.ai/api-reference-docs/pricing"
D = "2026-09-29"
price("gpt41mini_in", "OpenAI", "gpt-4.1-mini input (managed default tier)", "USD per 1M tokens", 0.40, "USD", CODE, OA, "2026-09-14", "From the code rate book (default_rates.py, checked 14 Sep 2026). The 29 Sep survey found the page now leads with the gpt-5 family; gpt-4.1-mini was not re-read. Re-verify.")
price("gpt41mini_cached", "OpenAI", "gpt-4.1-mini cached input", "USD per 1M tokens", 0.10, "USD", CODE, OA, "2026-09-14", "Code rate book split rows (cached share 0.25 of input for gpt-4.1 family per OpenAI).")
price("gpt41mini_out", "OpenAI", "gpt-4.1-mini output", "USD per 1M tokens", 1.60, "USD", CODE, OA, "2026-09-14")
price("gpt41_in", "OpenAI", "gpt-4.1 input (managed accurate tier)", "USD per 1M tokens", 2.00, "USD", CODE, OA, "2026-09-14", "Code rate book; not re-read on 29 Sep.")
price("gpt41_cached", "OpenAI", "gpt-4.1 cached input", "USD per 1M tokens", 0.50, "USD", CODE, OA, "2026-09-14")
price("gpt41_out", "OpenAI", "gpt-4.1 output", "USD per 1M tokens", 8.00, "USD", CODE, OA, "2026-09-14")
price("gpt5_in", "OpenAI", "gpt-5 input", "USD per 1M tokens", 1.25, "USD", VENDOR, OA, D)
price("gpt5_cached", "OpenAI", "gpt-5 cached input", "USD per 1M tokens", 0.125, "USD", VENDOR, OA, D)
price("gpt5_out", "OpenAI", "gpt-5 output", "USD per 1M tokens", 10.00, "USD", VENDOR, OA, D)
price("gpt5nano_in", "OpenAI", "gpt-5-nano input", "USD per 1M tokens", 0.05, "USD", VENDOR, OA, D)
price("gpt5nano_cached", "OpenAI", "gpt-5-nano cached input", "USD per 1M tokens", 0.005, "USD", VENDOR, OA, D)
price("gpt5nano_out", "OpenAI", "gpt-5-nano output", "USD per 1M tokens", 0.40, "USD", VENDOR, OA, D)
price("sonnet5_in", "Anthropic", "claude-sonnet-5 input (agent builder default)", "USD per 1M tokens", 2.00, "USD", VENDOR, AN, D, "Page notes the planned rise to $3/$15 was cancelled; $2/$10 stands.")
price("sonnet5_cached", "Anthropic", "claude-sonnet-5 cached read", "USD per 1M tokens", 0.20, "USD", VENDOR, AN, D, "Cache write is 1.25x input (5 min) and is not modelled: it applies once per conversation.")
price("sonnet5_out", "Anthropic", "claude-sonnet-5 output", "USD per 1M tokens", 10.00, "USD", VENDOR, AN, D)
price("opus5_in", "Anthropic", "claude-opus-5 input (dearest model in the chat catalogue)", "USD per 1M tokens", 5.00, "USD", CODE, AN, "2026-09-14", "Code rate book. The official page on 29 Sep lists Opus 5.5 at $4/$20 and no longer lists Opus 5; the higher code figure is kept as the worst case.")
price("opus5_cached", "Anthropic", "claude-opus-5 cached read", "USD per 1M tokens", 0.50, "USD", CODE, AN, "2026-09-14")
price("opus5_out", "Anthropic", "claude-opus-5 output", "USD per 1M tokens", 25.00, "USD", CODE, AN, "2026-09-14")
price("gemini25flash_in", "Google", "gemini-2.5-flash input", "USD per 1M tokens", 0.30, "USD", VENDOR, GG, D)
price("gemini25flash_cached", "Google", "gemini-2.5-flash cached input", "USD per 1M tokens", 0.03, "USD", VENDOR, GG, D, "Plus $1.00 per 1M tokens per hour of cache storage, not modelled.")
price("gemini25flash_out", "Google", "gemini-2.5-flash output", "USD per 1M tokens", 2.50, "USD", VENDOR, GG, D)
price("sarvam105b_in", "Sarvam", "sarvam-105b input", "INR per 1M tokens", 29.28, "INR", VENDOR, SV, D)
price("sarvam105b_cached", "Sarvam", "sarvam-105b cached input", "INR per 1M tokens", 10.98, "INR", VENDOR, SV, D)
price("sarvam105b_out", "Sarvam", "sarvam-105b output", "INR per 1M tokens", 73.20, "INR", VENDOR, SV, D)
price("embed_small", "OpenAI", "text-embedding-3-small", "USD per 1M tokens", 0.02, "USD", VENDOR, OA, D)

# ---- Speech ---------------------------------------------------------------
price("sarvam_stt_hr", "Sarvam", "saaras:v3 / saarika streaming STT", "INR per hour", 30.0, "INR", VENDOR, SV, D)
price("sarvam_tts_10k", "Sarvam", "bulbul:v3 TTS", "INR per 10k characters", 30.0, "INR", VENDOR, SV, D)
price("sarvam_translate_10k", "Sarvam", "Translate", "INR per 10k characters", 20.0, "INR", VENDOR, SV, D, "Tiers to Rs0.0045 and Rs0.004 a character at volume.")
price("deepgram_flux_min", "Deepgram", "Flux multilingual streaming STT (managed instant tier)", "USD per minute", 0.0078, "USD", CODE, "https://deepgram.com/pricing", "2026-09-14", "Code rate book. The 29 Sep survey read Nova-3 multilingual streaming at $0.0058 (Growth $0.0050); Flux was not on the page read. Re-verify.")
price("deepgram_batch_min", "Deepgram", "Nova-3 pre-recorded (batch) STT", "USD per minute", 0.0052, "USD", VENDOR, "https://deepgram.com/pricing", D)
price("eleven_flash_1k", "ElevenLabs", "Flash v2.5 TTS, effective rate", "USD per 1k characters", 0.05, "USD", ASSUMPTION, "https://elevenlabs.io/pricing/api", D, "Official page on 29 Sep: pay-as-you-go $0.022/1k on a promotion to 12 Oct 2026, list $0.08/1k; Scale plan $299 for 9M chars = $0.033/1k; Business $990 for 27.18M = $0.036/1k. Our plan is unknown, so the code book's $0.05 is kept as a mid figure. Open question.")
price("smallest_pro_10k", "Smallest.ai", "Lightning v3.1 Pro TTS (managed natural tier)", "USD per 10k characters", 0.195, "USD", CODE, "https://smallest.ai/pricing/models", "2026-09-14", "Code rate book; not re-read on 29 Sep.")
price("rumik_1k", "Rumik", "Mulberry TTS (managed basic tier)", "INR per 1k characters", 0.50, "INR", CODE, "https://rumik.ai/pricing", "2026-09-14", "Code rate book, launch pricing; not re-read on 29 Sep.")
price("gemini_live_in", "Google", "Gemini Live audio input", "USD per 1M tokens", 3.00, "USD", VENDOR, GG, D, "Gemini 3.8 Live and 2.5 Flash native audio both $3.00 audio in.")
price("gemini_live_cached", "Google", "Gemini Live cached audio input", "USD per 1M tokens", 0.30, "USD", CODE, GG, "2026-07", "Code rate book (realtime_rates.py); the page read on 29 Sep does not show a cached audio price for Live. Re-verify.")
price("gemini_live_out", "Google", "Gemini Live audio output", "USD per 1M tokens", 12.00, "USD", VENDOR, GG, D)
price("gemini_live_tps_in", "Google", "audio tokens per second, input", "tokens per second", 32, "", CODE, GG, "2026-07", "realtime_rates.py; Google documents 32 tokens a second of audio.")
price("gemini_live_tps_out", "Google", "audio tokens per second, output", "tokens per second", 25, "", CODE, GG, "2026-07", "realtime_rates.py")
price("oai_rt_in", "OpenAI", "gpt-realtime audio input (managed premium tier)", "USD per 1M tokens", 32.00, "USD", VENDOR, OA, D, "Page lists gpt-realtime and gpt-realtime-1.5 at this price; the code names gpt-realtime-2.")
price("oai_rt_cached", "OpenAI", "gpt-realtime-2 cached audio input", "USD per 1M tokens", 0.40, "USD", VENDOR, OA, D)
price("oai_rt_out", "OpenAI", "gpt-realtime-2 audio output", "USD per 1M tokens", 64.00, "USD", VENDOR, OA, D)
price("oai_rt_tps_in", "OpenAI", "audio tokens per second, input", "tokens per second", 10, "", CODE, OA, "2026-07", "realtime_rates.py")
price("oai_rt_tps_out", "OpenAI", "audio tokens per second, output", "tokens per second", 20, "", CODE, OA, "2026-07", "realtime_rates.py")

# ---- Telephony, messaging, data ---------------------------------------------
price("plivo_min", "Plivo", "India voice, inbound and outbound (Voice AI telephony)", "INR per minute", 0.38, "INR", VENDOR, "https://www.plivo.com/voice/pricing/in/", D, "Confirmed on the account 27 Aug 2026 per default_rates.py:964")
price("plivo_number_month", "Plivo", "India virtual number rental", "INR per month", 200.0, "INR", VENDOR, "https://www.plivo.com/voice/pricing/in/", D, "The code carries Rs250 (api/constants.py:349 NUMBER_RENTAL_COST_PAISE). Carrier invoice not seen.")
price("twilio_us_out_min", "Twilio", "US outbound voice", "USD per minute", 0.014, "USD", VENDOR, "https://www.twilio.com/en-us/voice/pricing/us", D, "Overseas reference carriage for the global voice price.")
price("twilio_us_number_month", "Twilio", "US local number", "USD per month", 1.15, "USD", VENDOR, "https://www.twilio.com/en-us/phone-numbers/pricing/us", D)
price("wa_marketing", "Meta", "WhatsApp marketing template, India", "INR per message", 0.8631, "INR", VENDOR, "https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing", D, "Matches exchange.py:115. Rate card effective 1 Jul 2026, unchanged on the 1 Oct 2026 card.")
price("wa_utility", "Meta", "WhatsApp utility template, India", "INR per message", 0.115, "INR", VENDOR, "https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing", D, "Free inside the 24 h service window until 30 Sep 2026; chargeable from 1 Oct 2026.")
price("wa_auth", "Meta", "WhatsApp authentication template, India", "INR per message", 0.115, "INR", VENDOR, "https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing", D)
price("wa_service", "Meta", "WhatsApp service message (customer-initiated window)", "INR per message", 0.115, "INR", VENDOR, "https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing/non-template-messages", D, "Free until 30 Sep 2026. From 1 Oct 2026 charged at the utility rate after 1,000 free service messages per business number per month. Modelled at the October price since the pricing under test is not live yet.")
price("serper_query", "Serper", "Search query (Starter 50k for $50; falls to $0.0003 on the Ultimate pack)", "USD per query", 0.001, "USD", VENDOR, "https://serper.dev/", D)
price("composio_call", "Composio", "Tool call past the free tier (Hobby: 100k calls a month free; Pro $29 a month)", "USD per call", 0.0003, "USD", VENDOR, "https://composio.dev/pricing", D, "readiness.py:1229-1234 records the same figure. Below 100k calls a month the marginal cost is zero; the list rate is used so the model does not depend on staying under the cap.")
price("razorpay_mdr", "Razorpay", "Standard payment MDR", "share of payment", 0.02, "", VENDOR, "https://razorpay.com/pricing/", D, "Plus 18% GST on the fee.")
price("s3_gb_month", "AWS", "S3 Standard, ap-south-1", "USD per GB-month", 0.025, "USD", VENDOR, "https://aws.amazon.com/s3/pricing/", D)
price("ebs_gp3_gb_month", "AWS", "EBS gp3, ap-south-1", "USD per GB-month", 0.0912, "USD", VENDOR, "https://aws.amazon.com/ebs/pricing/", D)
price("ec2_hour", "AWS", "EC2 t3.xlarge (4 vCPU, 16 GB) on-demand Linux, ap-south-1", "USD per hour", 0.1792, "USD", VENDOR, "https://aws.amazon.com/ec2/pricing/on-demand/", D, "The PRICE is official; the INSTANCE TYPE is an assumption. The box's type is not in the repo. Replace with the AWS invoice.")
price("egress_gb", "AWS", "Data transfer out to internet, ap-south-1 (first 10 TB)", "USD per GB", 0.1093, "USD", VENDOR, "https://aws.amazon.com/ec2/pricing/on-demand/", D)

# --------------------------------------------------------------------------
# Sheet 3: Measured usage (and the assumptions that fill gaps)
# --------------------------------------------------------------------------
mu = wb.create_sheet("Measured usage")
header(
    mu,
    1,
    ["Key", "Activity", "Metric", "Value", "Label", "Sample (n)", "Source", "Note"],
    [26, 26, 40, 12, 14, 10, 50, 60],
)
_murow = [2]


def usage(key, activity, metric, value, label, n, source, note=""):
    r = _murow[0]
    for j, v in enumerate([key, activity, metric, value, label, n, source, note], 1):
        mu.cell(row=r, column=j, value=v).alignment = Alignment(wrap_text=True, vertical="top")
    mu.cell(row=r, column=5).fill = LABEL_FILL[label]
    R[key] = f"'Measured usage'!$D${r}"
    _murow[0] += 1


RAW = "finance/costing-2026-10/raw/"
# Prompt sizes, measured locally with the cl100k tokenizer against the code on origin/main af0c751.
usage("sys_prompt_tokens", "Decibyl chat", "System prompt tokens (all flags on)", 2523, MEASURED, 1, "Measured locally: api/services/workflow/decibyl.py prompt rendered, cl100k_base tokenizer", "Grows with memory and connected apps; this is the floor.")
usage("tools_schema_tokens", "Decibyl chat", "Tool schema tokens (24 office tools)", 5674, MEASURED, 1, "Measured locally: tool JSON schemas, cl100k_base", "Sent on every turn.")
usage("history_tokens_med", "Decibyl chat", "Conversation history + memory tokens, median", 3000, ASSUMPTION, 0, "No per-turn token receipts were readable (superadmin only); see Open questions", "Sensitivity: p90 uses 8,000.")
usage("history_tokens_p90", "Decibyl chat", "Conversation history + memory tokens, p90", 8000, ASSUMPTION, 0, "As above")
usage("chat_out_med", "Decibyl chat", "Output tokens per reply, median", 300, ASSUMPTION, 0, "Typical chat reply of 150-250 words")
usage("chat_out_p90", "Decibyl chat", "Output tokens per reply, p90", 900, ASSUMPTION, 0, "Long reply or a drafted email")
usage("chat_tool_calls", "Decibyl chat", "Tool calls per chat message, mean", 0.6, ASSUMPTION, 0, "200 exported runs show nodes_visited 1-3 per textchat run; a plain question is 0, an action is 1-2")
usage("cache_hit", "All LLM", "Prompt-cache hit rate on input tokens (base)", 0.70, ASSUMPTION, 0, "The stable prefix (system prompt + tools = 8,197 tokens) is 73% of a median input; OpenAI caches prefixes over 1,024 tokens automatically", "No measured hit rate exists: call_turn_metrics.cached_tokens is superadmin-only. Sensitivity runs 0.5 and 0.9.")
usage("builder_in", "Agent builder", "Input tokens per builder message", 9000, ASSUMPTION, 0, "Builder prompt + the agent draft resent each turn; not measured (AGENT_BUILDER_ENABLED is off in production)")
usage("builder_out", "Agent builder", "Output tokens per builder message", 1200, ASSUMPTION, 0, "A builder turn rewrites part of the draft")
usage("task_llm_turns", "Task run", "LLM turns per task run", 3, ASSUMPTION, 0, "Exported runs: textchat runs visit 1-3 nodes; a task with tools takes plan, act, report")
usage("task_tool_calls", "Task run", "Tool calls per task run", 2, ASSUMPTION, 0, "As above")
usage("routine_llm_turns", "Routine run", "LLM turns per routine run", 1, MEASURED, 20, RAW + "usage_runs*.json: 'Inbox Brief' routine runs, nodes_visited ['Start'], median 7 s on gpt-5-nano")
usage("routine_tool_calls", "Routine run", "Tool calls per routine run", 1, MEASURED, 20, "Same runs: one Gmail fetch each (usage_review.json summaries)")
usage("trigger_llm_turns", "Trigger run", "LLM turns per trigger run", 1, ASSUMPTION, 0, "A trigger is one reply to one event")
usage("kb_pages", "Knowledge ingest", "Pages per ingested document (reference: 10-page PDF)", 10, ASSUMPTION, 0, "Prompt scenario C")
usage("kb_chars_per_page", "Knowledge ingest", "Characters per page", 3000, CODE, 0, "api/services/billing/knowledge_pages.py:45 CHARS_PER_PAGE")
usage("kb_tokens_per_char", "Knowledge ingest", "Tokens per character (English prose)", 0.25, ASSUMPTION, 0, "cl100k: about 4 characters per token")
usage("kb_answer_ctx", "Knowledge answer", "Retrieved context tokens added to the reply", 3000, ASSUMPTION, 0, "Top-k chunks; the code caps chat_context_tokens per plan (plan_limits.py)")
usage("kb_query_tokens", "Knowledge answer", "Query embedding tokens", 40, ASSUMPTION, 0, "")
# Voice: measured from our workspace's 30-day call report and 60-day spend split.
usage("calls_30d", "Voice", "Calls in the last 30 days", 301, MEASURED, 301, RAW + "usage_calls.json totals.calls (31 Aug to 29 Sep 2026)")
usage("billable_min_30d", "Voice", "Billable minutes in the last 30 days", 7240 / 60, MEASURED, 301, RAW + "usage_calls.json totals.billable_seconds / 60")
usage("charged_30d_inr", "Voice", "Charged for those calls (INR)", 458.04, MEASURED, 301, RAW + "usage_calls.json totals.charged_paise / 100")
usage("stt_charged_30d", "Voice", "STT charged, last 30 days (INR)", 95.11, MEASURED, 301, RAW + "usage_spend.json series, stt, 31 Aug to 29 Sep")
usage("tts_charged_30d", "Voice", "TTS charged, last 30 days (INR)", 179.22, MEASURED, 301, RAW + "usage_spend.json series, tts")
usage("llm_charged_30d", "All", "LLM charged, last 30 days (INR, voice and text together)", 77.12, MEASURED, 301, RAW + "usage_spend.json series, llm")
usage("markup_stt", "Billing code", "STT markup applied when charging", 1.30, CODE, 0, "api/services/billing/markup.py:568-583 (KAN-54)")
usage("markup_tts", "Billing code", "TTS markup applied when charging", 1.80, CODE, 0, "markup.py:568-583")
usage("markup_llm", "Billing code", "LLM markup applied when charging", 2.00, CODE, 0, "markup.py:568-583")
usage("turns_per_min", "Voice", "LLM turns per minute of call", 6, CODE, 0, "api/services/billing/realtime_pricing.py:91 seconds_per_turn = 10", "Same shape the code prices realtime calls with.")
usage("voice_prompt_tokens", "Voice", "Agent system prompt + tools per turn (tokens)", 1800, ASSUMPTION, 0, "A voice agent carries a shorter prompt and 2-4 tools; not measured")
usage("voice_transcript_tok_per_min", "Voice", "Transcript tokens added per minute of call", 220, ASSUMPTION, 0, "About 150 spoken words a minute, both sides, at 1.4 tokens a word")
usage("voice_out_tok_per_turn", "Voice", "Output tokens per turn (spoken reply)", 45, ASSUMPTION, 0, "A 2-3 sentence spoken reply")
usage("tts_cpm", "Voice", "TTS characters per minute of call (base)", 450, ASSUMPTION, 0, "Agent talks 45% of the time (realtime_pricing.py:95) at about 1,000 chars a minute of speech; the 30-day charged TTS backs out to about 275 (see tts_cpm_measured). Sensitivity runs 350 and 600.")
usage("stt_min_per_call_min", "Voice", "STT minutes billed per call minute", 1.0, ASSUMPTION, 0, "Streaming STT runs for the whole call, both silences and speech")
usage("call_len_med_s", "Voice", "Call length, median (seconds)", 24.1, MEASURED, 301, RAW + "usage_calls.json totals.average_seconds", "237 of 301 calls were under 30 s: demo and test traffic, not a customer's pattern.")
usage("call_len_ref_s", "Voice", "Reference call length for realtime pricing (seconds)", 180, CODE, 0, "realtime_pricing.py:90 duration_seconds; prompt scenario G is also 3 minutes")
usage("agent_talk_share", "Voice", "Share of the call the agent is speaking", 0.45, CODE, 0, "realtime_pricing.py:95")
usage("caller_talk_share", "Voice", "Share of the call the caller is speaking", 0.30, CODE, 0, "realtime_pricing.py:98")
usage("translate_chars", "Translation", "Characters per translation unit as sold", 100, CODE, 0, "events.py: 1 credit per 100 characters")
usage("script_cpu_s", "Script run", "Sandbox CPU seconds per run", 5, ASSUMPTION, 0, "python:3.12-slim job; docker-in-docker on the same box")
usage("storage_gb_ws", "Storage", "Storage per active workspace (GB)", 2, ASSUMPTION, 0, "Recordings at ~1 MB a minute of call, plus KB files; no per-workspace bytes are readable")
usage("web_search_per_tool", "Web search", "Serper queries per search tool call", 1, CODE, 0, "web_tools.py:241-255 records one lookup per call")
usage("hours_month", "Infra", "Hours in a month", 730, ASSUMPTION, 0, "AWS convention")
usage("ebs_gb", "Infra", "Root volume size (GB)", 145, MEASURED, 1, "scripts/ci_deploy.sh:71 comment: 145G disk")
usage("egress_gb_month", "Infra", "Egress per month (GB)", 200, ASSUMPTION, 0, "Recordings, UI, TURN relay; no CloudWatch data was available")
usage("bench_note", "Benchmarks", "Controlled benchmark runs executed", 0, MEASURED, 0, "None: no test keys were available in this session and production keys may not be used (ground rules)")

# --------------------------------------------------------------------------
# Sheet 4: Unit costs
# --------------------------------------------------------------------------
uc = wb.create_sheet("Unit costs")
uc.column_dimensions["A"].width = 44
for col in "BCDEFGHIJKLM":
    uc.column_dimensions[col].width = 13

# Levers (base case) at the top. Sensitivity re-uses the same formulas with its own levers.
uc["A1"] = "Levers (base case; the Sensitivity sheet overrides these per scenario)"
uc["A1"].font = Font(bold=True)
LEVER_ROWS = {
    "fx": ("INR per USD", f"={R['fx']}"),
    "mm": ("Model price multiplier", 1),
    "ch": ("Prompt-cache hit rate", f"={R['cache_hit']}"),
    "cpm": ("TTS characters per minute", f"={R['tts_cpm']}"),
    "tm": ("Telephony multiplier", 1),
    "mult": ("Price multiple on cost (proposed)", 3),
    "minc": ("Minimum credits per action (proposed)", 0.5),
    "credit_inr": ("1 credit, India (INR)", 1),
    "credit_usd": ("1 credit, global (USD)", 0.012),
}
BASE = {}
for i, (k, (label, val)) in enumerate(LEVER_ROWS.items(), 2):
    uc.cell(row=i, column=1, value=label)
    uc.cell(row=i, column=2, value=val)
    BASE[k] = f"'Unit costs'!$B${i}"


def llm_usd(in_tok, out_tok, model, L):
    """USD for one LLM call: input at the cache-blended price, output at list."""
    p_in, p_c, p_out = R[f"{model}_in"], R[f"{model}_cached"], R[f"{model}_out"]
    inr = model.startswith("sarvam")
    core = f"({in_tok}*((1-{L['ch']})*{p_in}+{L['ch']}*{p_c})+{out_tok}*{p_out})/1000000"
    if inr:
        core = f"({core})/{L['fx']}"
    return f"{L['mm']}*{core}"


def voice_llm_usd_per_min(model, L):
    """Cascade LLM cost per call minute: each turn resends prompt + transcript so far."""
    # Over a reference call of T minutes the average transcript resent is half the final one.
    T = f"({R['call_len_ref_s']}/60)"
    in_per_turn = f"({R['voice_prompt_tokens']}+{R['voice_transcript_tok_per_min']}*{T}/2)"
    return f"{R['turns_per_min']}*{llm_usd(in_per_turn, R['voice_out_tok_per_turn'], model, L)}"


def realtime_usd_per_min(prefix, L):
    """Speech-to-speech per minute, the code's own model (realtime_pricing.py):
    output billed once; input = both sides' audio re-sent (turns+1)/2 times, at the cached price for the repeats."""
    T = R["call_len_ref_s"]
    turns = f"ROUND({T}/10,0)"
    ctx = f"(({turns}+1)/2)"
    conv_s = f"({T}*({R['agent_talk_share']}+{R['caller_talk_share']}))"
    fresh = f"({conv_s}*{R[prefix+'_tps_in']})"
    repeated = f"({fresh}*({ctx}-1))"
    out = f"({T}*{R['agent_talk_share']}*{R[prefix+'_tps_out']})"
    usd_call = (
        f"({fresh}*{R[prefix+'_in']}+{repeated}*(({L['ch']})*{R[prefix+'_cached']}+(1-{L['ch']})*{R[prefix+'_in']})"
        f"+{out}*{R[prefix+'_out']})/1000000"
    )
    return f"{L['mm']}*{usd_call}/({T}/60)"


def stt_usd_per_min(L):
    return f"({R['sarvam_stt_hr']}/60)*{R['stt_min_per_call_min']}/{L['fx']}"


def tts_usd_per_min(L, vendor="sarvam"):
    if vendor == "sarvam":
        return f"({R['sarvam_tts_10k']}/10000)*{L['cpm']}/{L['fx']}"
    if vendor == "eleven":
        return f"({R['eleven_flash_1k']}/1000)*{L['cpm']}"
    if vendor == "smallest":
        return f"({R['smallest_pro_10k']}/10000)*{L['cpm']}"
    if vendor == "rumik":
        return f"({R['rumik_1k']}/1000)*{L['cpm']}/{L['fx']}"
    raise KeyError(vendor)


def tel_usd_per_min(L, where="india"):
    if where == "india":
        return f"{L['tm']}*{R['plivo_min']}/{L['fx']}"
    return f"{L['tm']}*{R['twilio_us_out_min']}"


def composio_usd(n):
    return f"{n}*{R['composio_call']}"


# Activity table: (name, components dict of USD formulas) for median and p90.
def chat(model, hist, out, L):
    return {
        "LLM": llm_usd(f"({R['sys_prompt_tokens']}+{R['tools_schema_tokens']}+{hist})", out, model, L),
        "Tools": composio_usd(R["chat_tool_calls"]),
    }


def build_activities(L):
    A = []
    A.append(("1a. Chat message, default model (gpt-4.1-mini)", "per message", chat("gpt41mini", R["history_tokens_med"], R["chat_out_med"], L), chat("gpt41mini", R["history_tokens_p90"], R["chat_out_p90"], L)))
    A.append(("1b. Chat message, premium model (gpt-5)", "per message", chat("gpt5", R["history_tokens_med"], R["chat_out_med"], L), chat("gpt5", R["history_tokens_p90"], R["chat_out_p90"], L)))
    A.append(("1c. Chat message, dearest catalogue model (claude-opus-5)", "per message", chat("opus5", R["history_tokens_med"], R["chat_out_med"], L), chat("opus5", R["history_tokens_p90"], R["chat_out_p90"], L)))
    A.append(("1d. Chat message, cheapest catalogue model (gpt-5-nano)", "per message", chat("gpt5nano", R["history_tokens_med"], R["chat_out_med"], L), chat("gpt5nano", R["history_tokens_p90"], R["chat_out_p90"], L)))
    A.append(("2. Agent-building message (claude-sonnet-5)", "per message", {"LLM": llm_usd(R["builder_in"], R["builder_out"], "sonnet5", L)}, {"LLM": llm_usd(f"2*{R['builder_in']}", f"2*{R['builder_out']}", "sonnet5", L)}))
    task_turn = lambda hist, out: llm_usd(f"({R['sys_prompt_tokens']}+{R['tools_schema_tokens']}+{hist})", out, "gpt41mini", L)
    A.append(("3a. Task run (3 LLM turns + 2 tool calls, gpt-4.1-mini)", "per run", {"LLM": f"{R['task_llm_turns']}*{task_turn(R['history_tokens_med'], R['chat_out_med'])}", "Tools": composio_usd(R["task_tool_calls"])}, {"LLM": f"2*{R['task_llm_turns']}*{task_turn(R['history_tokens_p90'], R['chat_out_p90'])}", "Tools": composio_usd(f"2*{R['task_tool_calls']}")}))
    routine_in_med = f"({R['sys_prompt_tokens']}+{R['tools_schema_tokens']}+{R['history_tokens_med']})"
    routine_in_p90 = f"({R['sys_prompt_tokens']}+{R['tools_schema_tokens']}+{R['history_tokens_p90']})"
    A.append(("3b. Routine run (1 turn + 1 tool call, gpt-5-nano as run today)", "per run",
              {"LLM": f"{R['routine_llm_turns']}*{llm_usd(routine_in_med, R['chat_out_med'], 'gpt5nano', L)}", "Tools": composio_usd(R["routine_tool_calls"])},
              {"LLM": f"{R['routine_llm_turns']}*{llm_usd(routine_in_p90, R['chat_out_p90'], 'gpt41mini', L)}", "Tools": composio_usd(R["routine_tool_calls"])}))
    A.append(("3c. Trigger run (1 turn, gpt-4.1-mini)", "per run", {"LLM": task_turn(R["history_tokens_med"], R["chat_out_med"])}, {"LLM": task_turn(R["history_tokens_p90"], R["chat_out_p90"])}))
    A.append(("4a. Tool call, ordinary connector (Composio)", "per call", {"Tools": composio_usd(1)}, {"Tools": composio_usd(1)}))
    A.append(("4b. Tool call, premium connector (Composio; same vendor cost)", "per call", {"Tools": composio_usd(1)}, {"Tools": composio_usd(1)}))
    kb_tokens = f"({R['kb_pages']}*{R['kb_chars_per_page']}*{R['kb_tokens_per_char']})"
    A.append(("5a. Knowledge ingest, 10-page typed PDF (parse local + embed)", "per document", {"Embed": f"{L['mm']}*{kb_tokens}*{R['embed_small']}/1000000"}, {"Embed": f"{L['mm']}*5*{kb_tokens}*{R['embed_small']}/1000000"}))
    A.append(("5b. Knowledge answer (query embed + retrieval + reply, gpt-4.1-mini)", "per answer", {"Embed": f"{R['kb_query_tokens']}*{R['embed_small']}/1000000", "LLM": llm_usd(f"({R['sys_prompt_tokens']}+{R['tools_schema_tokens']}+{R['history_tokens_med']}+{R['kb_answer_ctx']})", R["chat_out_med"], "gpt41mini", L)}, {"Embed": f"{R['kb_query_tokens']}*{R['embed_small']}/1000000", "LLM": llm_usd(f"({R['sys_prompt_tokens']}+{R['tools_schema_tokens']}+{R['history_tokens_p90']}+2*{R['kb_answer_ctx']})", R["chat_out_p90"], "gpt41mini", L)}))
    indic = {"STT": stt_usd_per_min(L), "LLM": voice_llm_usd_per_min("gpt41mini", L), "TTS": tts_usd_per_min(L), "Telephony": tel_usd_per_min(L)}
    A.append(("6a. Voice minute, Indic stack, India (Sarvam STT + gpt-4.1-mini + Sarvam TTS + Plivo)", "per minute", indic, dict(indic, LLM=voice_llm_usd_per_min("gpt41", L))))
    indic_in = dict(indic)
    A.append(("6b. Voice minute, Indic stack, inbound India (same vendor rates)", "per minute", indic_in, dict(indic_in, LLM=voice_llm_usd_per_min("gpt41", L))))
    natural = {"STT": stt_usd_per_min(L), "LLM": voice_llm_usd_per_min("gpt41mini", L), "TTS": tts_usd_per_min(L, "smallest"), "Telephony": tel_usd_per_min(L)}
    A.append(("6c. Voice minute, natural voice (Smallest Lightning Pro TTS), India", "per minute", natural, dict(natural, LLM=voice_llm_usd_per_min("gpt41", L))))
    prem = {"STT": stt_usd_per_min(L), "LLM": voice_llm_usd_per_min("gpt41mini", L), "TTS": tts_usd_per_min(L, "eleven"), "Telephony": tel_usd_per_min(L)}
    A.append(("6d. Voice minute, premium voice (ElevenLabs Flash TTS), India", "per minute", prem, dict(prem, LLM=voice_llm_usd_per_min("gpt41", L))))
    gl = {"Realtime": realtime_usd_per_min("gemini_live", L), "Telephony": tel_usd_per_min(L)}
    A.append(("6e. Voice minute, Gemini Live, India", "per minute", gl, dict(gl, Realtime=f"1.5*{realtime_usd_per_min('gemini_live', L)}")))
    oa = {"Realtime": realtime_usd_per_min("oai_rt", L), "Telephony": tel_usd_per_min(L)}
    A.append(("6f. Voice minute, OpenAI realtime premium, India", "per minute", oa, dict(oa, Realtime=f"1.5*{realtime_usd_per_min('oai_rt', L)}")))
    ov = {"STT": f"{R['deepgram_flux_min']}*{L['mm']}", "LLM": voice_llm_usd_per_min("gpt41mini", L), "TTS": tts_usd_per_min(L, "eleven"), "Telephony": tel_usd_per_min(L, "us")}
    A.append(("6g. Voice minute, overseas (Deepgram Flux + gpt-4.1-mini + ElevenLabs + Twilio US)", "per minute", ov, dict(ov, LLM=voice_llm_usd_per_min("gpt41", L))))
    ovg = {"Realtime": realtime_usd_per_min("gemini_live", L), "Telephony": tel_usd_per_min(L, "us")}
    A.append(("6h. Voice minute, overseas, Gemini Live + Twilio US", "per minute", ovg, ovg))
    A.append(("7a. WhatsApp utility template", "per message", {"Meta": f"{R['wa_utility']}/{L['fx']}"}, {"Meta": f"{R['wa_utility']}/{L['fx']}"}))
    A.append(("7b. WhatsApp service reply (in the 24 h window)", "per message", {"Meta": f"{R['wa_service']}/{L['fx']}", "LLM": llm_usd(f"({R['sys_prompt_tokens']}+{R['history_tokens_med']})", R["chat_out_med"], "gpt41mini", L)}, {"Meta": f"{R['wa_service']}/{L['fx']}", "LLM": llm_usd(f"({R['sys_prompt_tokens']}+{R['history_tokens_p90']})", R["chat_out_p90"], "gpt41mini", L)}))
    A.append(("7c. WhatsApp marketing template", "per message", {"Meta": f"{R['wa_marketing']}/{L['fx']}"}, {"Meta": f"{R['wa_marketing']}/{L['fx']}"}))
    A.append(("8. Web search (1 Serper query + the tool turn)", "per search", {"Data": f"{R['web_search_per_tool']}*{R['serper_query']}"}, {"Data": f"3*{R['serper_query']}"}))
    A.append(("9a. Script run (sandbox on our own box; compute only)", "per run", {"Infra": f"{R['script_cpu_s']}/3600*{R['ec2_hour']}/4"}, {"Infra": f"6*{R['script_cpu_s']}/3600*{R['ec2_hour']}/4"}))
    A.append(("9b. Translation, 100 characters (Sarvam)", "per 100 chars", {"Sarvam": f"{R['translate_chars']}/10000*{R['sarvam_translate_10k']}/{L['fx']}"}, {"Sarvam": f"{R['translate_chars']}/10000*{R['sarvam_translate_10k']}/{L['fx']}"}))
    A.append(("9c. Transcription of an uploaded recording (Deepgram batch)", "per minute", {"STT": f"{R['deepgram_batch_min']}*{L['mm']}"}, {"STT": f"{R['deepgram_batch_min']}*{L['mm']}"}))
    A.append(("10a. Storage per workspace per month (S3 price for the GB held)", "per workspace-month", {"Infra": f"{R['storage_gb_ws']}*{R['s3_gb_month']}"}, {"Infra": f"5*{R['storage_gb_ws']}*{R['s3_gb_month']}"}))
    A.append(("10b. Phone number per month, India (Plivo)", "per number-month", {"Telephony": f"{R['plivo_number_month']}/{L['fx']}"}, {"Telephony": f"{R['plivo_number_month']}/{L['fx']}"}))
    A.append(("10c. Phone number per month, US (Twilio)", "per number-month", {"Telephony": R["twilio_us_number_month"]}, {"Telephony": R["twilio_us_number_month"]}))
    return A


COMPONENTS = ["STT", "LLM", "TTS", "Telephony", "Realtime", "Tools", "Embed", "Meta", "Data", "Infra", "Sarvam"]
start = len(LEVER_ROWS) + 4
header(uc, start, ["Activity", "Unit", "Case"] + [f"{c} (USD)" for c in COMPONENTS] + ["Total USD", "Total INR", "Credits at cost x multiple (min applies)", "Price INR (credits x 1)", "Gross margin", "Below-minimum?", "Margin < 60%?"])
for col in range(4, 4 + len(COMPONENTS) + 7):
    uc.column_dimensions[get_column_letter(col)].width = 12
ACT: dict[str, dict[str, str]] = {}  # activity name -> {"med": row, "p90": row}
r = start + 1
for name, unit, med, p90 in build_activities(BASE):
    for case, comps in (("median", med), ("p90", p90)):
        uc.cell(row=r, column=1, value=name)
        uc.cell(row=r, column=2, value=unit)
        uc.cell(row=r, column=3, value=case)
        for j, c in enumerate(COMPONENTS):
            col = 4 + j
            if c in comps:
                uc.cell(row=r, column=col, value=f"={comps[c]}")
        tot = 4 + len(COMPONENTS)
        first, last = get_column_letter(4), get_column_letter(tot - 1)
        uc.cell(row=r, column=tot, value=f"=SUM({first}{r}:{last}{r})")
        uc.cell(row=r, column=tot + 1, value=f"={get_column_letter(tot)}{r}*{BASE['fx']}")
        inr = f"{get_column_letter(tot + 1)}{r}"
        uc.cell(row=r, column=tot + 2, value=f"=MAX({BASE['minc']},{inr}*{BASE['mult']}/{BASE['credit_inr']})")
        cr = f"{get_column_letter(tot + 2)}{r}"
        uc.cell(row=r, column=tot + 3, value=f"={cr}*{BASE['credit_inr']}")
        pr = f"{get_column_letter(tot + 3)}{r}"
        uc.cell(row=r, column=tot + 4, value=f"=IF({pr}>0,1-{inr}/{pr},0)")
        uc.cell(row=r, column=tot + 5, value=f'=IF({inr}*{BASE["mult"]}<{BASE["minc"]}*{BASE["credit_inr"]},"yes","")')
        uc.cell(row=r, column=tot + 6, value=f'=IF({get_column_letter(tot + 4)}{r}<0.6,"yes","")')
        ACT.setdefault(name, {})[case] = r
        r += 1
UC_TOT_USD = get_column_letter(4 + len(COMPONENTS))
UC_TOT_INR = get_column_letter(5 + len(COMPONENTS))
UC_CREDITS = get_column_letter(6 + len(COMPONENTS))


def act_inr(name, case="median"):
    return f"'Unit costs'!${UC_TOT_INR}${ACT[name][case]}"


def act_usd(name, case="median"):
    return f"'Unit costs'!${UC_TOT_USD}${ACT[name][case]}"


# Cross-check block: the vendor cost backed out of what our own workspace was charged.
r += 1
uc.cell(row=r, column=1, value="Cross-check against our own 30-day charges (vendor cost = charged / code markup)").font = Font(bold=True)
r += 1
uc.cell(row=r, column=1, value="STT vendor cost per billable minute (INR)")
uc.cell(row=r, column=2, value=f"={R['stt_charged_30d']}/{R['markup_stt']}/{R['billable_min_30d']}")
R["stt_inr_min_measured"] = f"'Unit costs'!$B${r}"
r += 1
uc.cell(row=r, column=1, value="TTS vendor cost per billable minute (INR)")
uc.cell(row=r, column=2, value=f"={R['tts_charged_30d']}/{R['markup_tts']}/{R['billable_min_30d']}")
R["tts_inr_min_measured"] = f"'Unit costs'!$B${r}"
r += 1
uc.cell(row=r, column=1, value="Implied TTS characters per minute at Sarvam's price")
uc.cell(row=r, column=2, value=f"={R['tts_inr_min_measured']}/({R['sarvam_tts_10k']}/10000)")
R["tts_cpm_measured"] = f"'Unit costs'!$B${r}"
r += 1
uc.cell(row=r, column=1, value="LLM vendor cost per billable minute, upper bound (INR; includes text chat)")
uc.cell(row=r, column=2, value=f"={R['llm_charged_30d']}/{R['markup_llm']}/{R['billable_min_30d']}")
R["llm_inr_min_measured"] = f"'Unit costs'!$B${r}"
r += 1
uc.cell(row=r, column=1, value="Charged per billable minute, all components (INR)")
uc.cell(row=r, column=2, value=f"={R['charged_30d_inr']}/{R['billable_min_30d']}")
r += 1
uc.cell(row=r, column=1, value="Modelled Indic minute, vendor cost (INR), for comparison")
uc.cell(row=r, column=2, value=f"={act_inr('6a. Voice minute, Indic stack, India (Sarvam STT + gpt-4.1-mini + Sarvam TTS + Plivo)')}")
r += 1
uc.cell(row=r, column=1, value="Code's own constant for the same minute (INR): exchange.py:120")
uc.cell(row=r, column=2, value=2.51)

# --------------------------------------------------------------------------
# Sheet 5: Fixed costs
# --------------------------------------------------------------------------
fc = wb.create_sheet("Fixed costs")
header(fc, 1, ["Item", "Monthly USD", "Monthly INR", "Label", "Source / note"], [46, 14, 14, 14, 80])
FIXED = [
    ("EC2 production box (instance type assumed t3.xlarge, 4 vCPU / 16 GB)", f"={R['ec2_hour']}*{R['hours_month']}", ASSUMPTION, "Type not in the repo. The compose stack (api workers, postgres, redis, minio, falkordb, gotenberg, sandbox, coturn, nginx, CI runner) needs at least 4 vCPU / 8 GB per docs/deployment/docker.mdx. Replace with the AWS invoice."),
    ("EBS root volume 145 GB gp3", f"={R['ebs_gb']}*{R['ebs_gp3_gb_month']}", MEASURED, "Size from scripts/ci_deploy.sh:71; price from AWS EBS pricing."),
    ("Egress 200 GB", f"={R['egress_gb_month']}*{R['egress_gb']}", ASSUMPTION, "No CloudWatch figures were available."),
    ("S3 / backup mirror, 50 GB", f"=50*{R['s3_gb_month']}", ASSUMPTION, "MinIO on the box is the default; a mirror bucket is optional (BACKUP_MIRROR_*)."),
    ("Plivo platform number (verification SMS sender)", f"={R['plivo_number_month']}/{R['fx']}", CODE, "One number at the code's rental cost."),
    ("Meta WhatsApp platform number", 0, VENDOR, "Meta does not charge a number rental; per-message only."),
    ("Composio plan", 0, ASSUMPTION, "Hobby tier is free to 100k calls a month; Pro is USD 29 a month (composio.dev/pricing, 29 Sep 2026). Open question which tier the account is on."),
    ("PostHog", 0, ASSUMPTION, "Free tier (1M events a month)."),
    ("Sentry", 0, ASSUMPTION, "Developer tier is free; Team is USD 26. Open question whether a DSN is set in production."),
    ("Langfuse (self-hosted at langfuse.decibyl.ai)", 0, ASSUMPTION, "If it is Langfuse Cloud instead, Core is USD 29 a month (langfuse.com/pricing, 29 Sep 2026). Open question."),
    ("GitHub Actions", 0, MEASURED, "Self-hosted runner on the production box since 28 Sep 2026; spending limit to be set to USD 0."),
    ("Domain, DNS, Cloudflare", 2, ASSUMPTION, "decibyl.ai renewal amortised; Cloudflare free plan."),
    ("SMTP relay", 0, ASSUMPTION, "Vendor unnamed in code; most relays are free below 3k mails a month."),
    ("Razorpay", 0, VENDOR, "No fixed fee; MDR is variable and modelled per plan on the Plan margins sheet."),
]
for i, (item, usd, label, note) in enumerate(FIXED, 2):
    fc.cell(row=i, column=1, value=item)
    fc.cell(row=i, column=2, value=usd)
    fc.cell(row=i, column=3, value=f"=B{i}*{R['fx']}")
    fc.cell(row=i, column=4, value=label).fill = LABEL_FILL[label]
    fc.cell(row=i, column=5, value=note).alignment = Alignment(wrap_text=True, vertical="top")
n = len(FIXED) + 2
fc.cell(row=n, column=1, value="Total fixed per month").font = Font(bold=True)
fc.cell(row=n, column=2, value=f"=SUM(B2:B{n-1})")
fc.cell(row=n, column=3, value=f"=SUM(C2:C{n-1})")
FIXED_INR = f"'Fixed costs'!$C${n}"
n += 2
fc.cell(row=n, column=1, value="Marginal cost of one extra workspace per month").font = Font(bold=True)
n += 1
fc.cell(row=n, column=1, value="Storage (GB assumed per workspace x S3 price)")
fc.cell(row=n, column=2, value=f"={R['storage_gb_ws']}*{R['s3_gb_month']}")
fc.cell(row=n, column=3, value=f"=B{n}*{R['fx']}")
n += 1
fc.cell(row=n, column=1, value="Database rows, background jobs, Redis (share of the box)")
fc.cell(row=n, column=2, value=f"=B2/500")
fc.cell(row=n, column=3, value=f"=B{n}*{R['fx']}")
fc.cell(row=n, column=5, value="Assumption: one box carries about 500 light workspaces before the next box (docs/deployment/scaling.mdx sizes by voice concurrency, not by workspace).")
n += 1
fc.cell(row=n, column=1, value="Marginal cost per extra workspace").font = Font(bold=True)
fc.cell(row=n, column=2, value=f"=B{n-2}+B{n-1}")
fc.cell(row=n, column=3, value=f"=B{n}*{R['fx']}")
MARGINAL_INR = f"'Fixed costs'!$C${n}"
n += 2
fc.cell(row=n, column=1, value="Fixed cost allocated per paying workspace").font = Font(bold=True)
header(fc, n + 1, ["Paying workspaces", "Fixed USD / workspace", "Fixed INR / workspace", "", "Note"])
ALLOC = {}
for i, w in enumerate((50, 200, 1000)):
    row = n + 2 + i
    fc.cell(row=row, column=1, value=w)
    fc.cell(row=row, column=2, value=f"=$B${len(FIXED)+2}/A{row}")
    fc.cell(row=row, column=3, value=f"=$C${len(FIXED)+2}/A{row}")
    fc.cell(row=row, column=5, value="At 1,000 workspaces a second box is likely; the total here stays at one box, so the 1,000 figure is a floor.")
    ALLOC[w] = f"'Fixed costs'!$C${row}"

# --------------------------------------------------------------------------
# Sheet 6: Plan margins
# --------------------------------------------------------------------------
pm = wb.create_sheet("Plan margins")
pm.column_dimensions["A"].width = 34
for col in "BCDEFGHIJKLMNOPQ":
    pm.column_dimensions[col].width = 14
pm["A1"] = "Proposed plans (from the audit prompt; not live)"
pm["A1"].font = Font(bold=True)
header(pm, 2, ["Plan", "Price INR", "Price USD", "Credits", "Chat per day", "Builder msgs per month", "Agents", "Chat cost median INR (default model)", "Chat cost INR if opus ran inside the allowance (p90)", "Builder cost median INR", "Cost per credit spent INR", "Chat cost p90 INR (default model)", "Builder cost p90 INR"])
PLANS = [("Starter", 3999, 59, 3000, 30, 100, 2), ("Growth", 14999, 199, 12000, 100, 500, 10), ("Scale", 39999, 499, 32000, 300, 1500, 50)]
CHAT_MED = act_inr("1a. Chat message, default model (gpt-4.1-mini)")
CHAT_WORST = act_inr("1c. Chat message, dearest catalogue model (claude-opus-5)", "p90")
CHAT_P90 = act_inr("1a. Chat message, default model (gpt-4.1-mini)", "p90")
BUILDER = act_inr("2. Agent-building message (claude-sonnet-5)")
BUILDER_P90 = act_inr("2. Agent-building message (claude-sonnet-5)", "p90")
PLAN_ROW = {}
for i, (name, inr, usd, credits, chat_d, builder, agents) in enumerate(PLANS, 3):
    for j, v in enumerate([name, inr, usd, credits, chat_d, builder, agents], 1):
        pm.cell(row=i, column=j, value=v)
    pm.cell(row=i, column=8, value=f"={CHAT_MED}")
    pm.cell(row=i, column=9, value=f"={CHAT_WORST}")
    pm.cell(row=i, column=10, value=f"={BUILDER}")
    # A credit spent at cost x multiple carries cost = credit value / multiple. Below-minimum actions cost less, so this is the ceiling.
    pm.cell(row=i, column=11, value=f"={BASE['credit_inr']}/{BASE['mult']}")
    pm.cell(row=i, column=12, value=f"={CHAT_P90}")
    pm.cell(row=i, column=13, value=f"={BUILDER_P90}")
    PLAN_ROW[name] = i

pm.cell(row=8, column=1, value="Gross margin by utilisation (India price, INR). Cost = u x (chat/day x 30 x chat cost + builder msgs x builder cost + credits x cost per credit). Razorpay MDR deducted from revenue.").font = Font(bold=True)
header(pm, 9, ["Plan", "Case", "Utilisation", "Revenue INR (net of MDR)", "Chat cost INR", "Builder cost INR", "Credits cost INR", "Total variable INR", "Gross margin", "Gross margin < 60%?", "Fixed alloc at 50 ws INR", "Margin after fixed (50 ws)", "Margin after fixed (200 ws)", "Global revenue INR (USD price x FX, net of MDR)", "Global gross margin"])
row = 10
PM_ROWS = {}
for name, *_ in PLANS:
    p = PLAN_ROW[name]
    for case, u, chat_ref, builder_ref in (("25% use", 0.25, "H", "J"), ("50% use", 0.5, "H", "J"), ("100% use", 1.0, "H", "J"), ("worst case: all used, p90 on the default model", 1.0, "L", "M"), ("if opus chat ran inside the allowance (rule says premium chat is credits)", 1.0, "I", "M")):
        pm.cell(row=row, column=1, value=name)
        pm.cell(row=row, column=2, value=case)
        pm.cell(row=row, column=3, value=u)
        pm.cell(row=row, column=4, value=f"=$B${p}*(1-{R['razorpay_mdr']})")
        pm.cell(row=row, column=5, value=f"=C{row}*$E${p}*30*${chat_ref}${p}")
        pm.cell(row=row, column=6, value=f"=C{row}*$F${p}*${builder_ref}${p}")
        pm.cell(row=row, column=7, value=f"=C{row}*$D${p}*$K${p}")
        pm.cell(row=row, column=8, value=f"=E{row}+F{row}+G{row}")
        pm.cell(row=row, column=9, value=f"=1-H{row}/D{row}")
        pm.cell(row=row, column=10, value=f'=IF(I{row}<0.6,"yes","")')
        pm.cell(row=row, column=11, value=f"={ALLOC[50]}")
        pm.cell(row=row, column=12, value=f"=1-(H{row}+K{row})/D{row}")
        pm.cell(row=row, column=13, value=f"=1-(H{row}+{ALLOC[200]})/D{row}")
        pm.cell(row=row, column=14, value=f"=$C${p}*{BASE['fx']}*(1-{R['razorpay_mdr']})")
        pm.cell(row=row, column=15, value=f"=1-H{row}/N{row}")
        PM_ROWS[(name, case)] = row
        row += 1

row += 1
pm.cell(row=row, column=1, value="Credit pack: 5,000 credits for INR 4,500 / USD 55").font = Font(bold=True)
row += 1
header(pm, row, ["Pack", "Price INR", "Price USD", "Credits", "Cost if all spent at cost x multiple (INR)", "Gross margin India", "Global revenue INR", "Gross margin global"])
row += 1
pm.cell(row=row, column=1, value="5,000 credits")
pm.cell(row=row, column=2, value=4500)
pm.cell(row=row, column=3, value=55)
pm.cell(row=row, column=4, value=5000)
pm.cell(row=row, column=5, value=f"=D{row}*{BASE['credit_inr']}/{BASE['mult']}")
pm.cell(row=row, column=6, value=f"=1-E{row}/(B{row}*(1-{R['razorpay_mdr']}))")
pm.cell(row=row, column=7, value=f"=C{row}*{BASE['fx']}*(1-{R['razorpay_mdr']})")
pm.cell(row=row, column=8, value=f"=1-E{row}/G{row}")
PACK_ROW = row

row += 2
pm.cell(row=row, column=1, value="How many actions a plan's credits buy at the proposed prices (median cost x multiple, minimum applied)").font = Font(bold=True)
row += 1
header(pm, row, ["Plan", "Credits", "Chat messages (default)", "Chat messages (gpt-5)", "Task runs", "Knowledge answers", "Tool calls (at minimum)", "WhatsApp utility (at minimum)"])
for name, *_ in PLANS:
    row += 1
    p = PLAN_ROW[name]
    pm.cell(row=row, column=1, value=name)
    pm.cell(row=row, column=2, value=f"=$D${p}")
    for j, act in enumerate(["1a. Chat message, default model (gpt-4.1-mini)", "1b. Chat message, premium model (gpt-5)", "3a. Task run (3 LLM turns + 2 tool calls, gpt-4.1-mini)", "5b. Knowledge answer (query embed + retrieval + reply, gpt-4.1-mini)", "4a. Tool call, ordinary connector (Composio)", "7a. WhatsApp utility template"], 3):
        pm.cell(row=row, column=j, value=f"=ROUNDDOWN(B{row}/'Unit costs'!${UC_CREDITS}${ACT[act]['median']},0)")

# --------------------------------------------------------------------------
# Sheet 7: Voice margins
# --------------------------------------------------------------------------
vm = wb.create_sheet("Voice margins")
vm.column_dimensions["A"].width = 60
for col in "BCDEFGHIJ":
    vm.column_dimensions[col].width = 15
vm["A1"] = "Proposed voice prices (all-in, from the audit prompt)"
vm["A1"].font = Font(bold=True)
header(vm, 2, ["Offer", "Price per minute INR", "Price per minute USD", "Basis"])
VOICE_OFFERS = [
    ("India pay-as-you-go", 6, None, "INR 6/min"),
    ("India pack 500 min at INR 2,750", 2750 / 500, None, "INR 5.50/min"),
    ("India pack 2,000 min at INR 10,000", 10000 / 2000, None, "INR 5.00/min"),
    ("Global pay-as-you-go", None, 0.18, "USD 0.18/min"),
    ("Global pack 500 min at USD 80", None, 80 / 500, "USD 0.16/min"),
    ("Global pack 2,000 min at USD 300", None, 300 / 2000, "USD 0.15/min"),
]
OFFER_ROW = {}
for i, (name, inr, usd, basis) in enumerate(VOICE_OFFERS, 3):
    vm.cell(row=i, column=1, value=name)
    vm.cell(row=i, column=2, value=inr if inr is not None else f"=C{i}*{BASE['fx']}")
    vm.cell(row=i, column=3, value=usd if usd is not None else f"=B{i}/{BASE['fx']}")
    vm.cell(row=i, column=4, value=basis)
    OFFER_ROW[name] = i

vm.cell(row=10, column=1, value="Gross margin per minute by stack and offer (cost from Unit costs, median case; MDR deducted from the price)").font = Font(bold=True)
header(vm, 11, ["Stack", "Offer", "Cost INR / min", "Price INR / min (net of MDR)", "Gross margin", "Margin < 60%?", "Cost p90 INR / min", "Margin at p90"])
STACKS_INDIA = [
    "6a. Voice minute, Indic stack, India (Sarvam STT + gpt-4.1-mini + Sarvam TTS + Plivo)",
    "6c. Voice minute, natural voice (Smallest Lightning Pro TTS), India",
    "6d. Voice minute, premium voice (ElevenLabs Flash TTS), India",
    "6e. Voice minute, Gemini Live, India",
    "6f. Voice minute, OpenAI realtime premium, India",
]
STACKS_GLOBAL = [
    "6g. Voice minute, overseas (Deepgram Flux + gpt-4.1-mini + ElevenLabs + Twilio US)",
    "6h. Voice minute, overseas, Gemini Live + Twilio US",
    "6f. Voice minute, OpenAI realtime premium, India",
]
row = 12
VM_ROWS = {}
for stacks, offers in ((STACKS_INDIA, VOICE_OFFERS[:3]), (STACKS_GLOBAL, VOICE_OFFERS[3:])):
    for stack in stacks:
        for name, *_ in offers:
            o = OFFER_ROW[name]
            vm.cell(row=row, column=1, value=stack)
            vm.cell(row=row, column=2, value=name)
            vm.cell(row=row, column=3, value=f"={act_inr(stack)}")
            vm.cell(row=row, column=4, value=f"=$B${o}*(1-{R['razorpay_mdr']})")
            vm.cell(row=row, column=5, value=f"=1-C{row}/D{row}")
            vm.cell(row=row, column=6, value=f'=IF(E{row}<0.6,"yes","")')
            vm.cell(row=row, column=7, value=f"={act_inr(stack, 'p90')}")
            vm.cell(row=row, column=8, value=f"=1-G{row}/D{row}")
            VM_ROWS[(stack, name)] = row
            row += 1
row += 1
vm.cell(row=row, column=1, value="Phone number: proposed INR 500 / USD 5 a month against carrier cost").font = Font(bold=True)
row += 1
header(vm, row, ["Number", "Price INR", "Cost INR", "Gross margin"])
row += 1
vm.cell(row=row, column=1, value="India (Plivo)")
vm.cell(row=row, column=2, value=500)
vm.cell(row=row, column=3, value=f"={R['plivo_number_month']}")
vm.cell(row=row, column=4, value=f"=1-C{row}/B{row}")
row += 1
vm.cell(row=row, column=1, value="US (Twilio)")
vm.cell(row=row, column=2, value=f"=5*{BASE['fx']}")
vm.cell(row=row, column=3, value=f"={R['twilio_us_number_month']}*{BASE['fx']}")
vm.cell(row=row, column=4, value=f"=1-C{row}/B{row}")
row += 2
vm.cell(row=row, column=1, value="What production charges today for the same minute (charge_rule flag off): Business 650 paise, Growth 600, Scale 550 per minute (services/configuration/bundles.py:81-82)")
row += 1
vm.cell(row=row, column=1, value="Today's Business minute, INR")
vm.cell(row=row, column=2, value=6.5)
vm.cell(row=row, column=3, value=f"={act_inr(STACKS_INDIA[0])}")
vm.cell(row=row, column=4, value=f"=1-C{row}/B{row}")

# --------------------------------------------------------------------------
# Sheet 8: Sensitivity
# --------------------------------------------------------------------------
se = wb.create_sheet("Sensitivity")
se.column_dimensions["A"].width = 34
for col in "BCDEFGHIJKLMNOPQ":
    se.column_dimensions[col].width = 14
se["A1"] = "Each row recomputes the key unit costs and margins from the same inputs with its own levers."
header(se, 2, ["Scenario", "Model price x", "INR per USD", "Cache hit", "TTS chars/min", "Telephony x", "Chat msg cost INR", "Indic minute cost INR", "Gemini Live minute cost INR", "Starter margin 100%", "Growth margin 100%", "Scale margin 100%", "Voice India PAYG margin (Indic)", "Voice India pack-2000 margin (Indic)", "Voice global PAYG margin (overseas stack)", "Voice global PAYG margin (Gemini Live)", "Chat msg credits at 3x (min applied)"])
SCEN = [
    ("Base", 1, f"={R['fx']}", f"={R['cache_hit']}", f"={R['tts_cpm']}", 1),
    ("Model prices +50%", 1.5, f"={R['fx']}", f"={R['cache_hit']}", f"={R['tts_cpm']}", 1),
    ("INR at 90 per USD", 1, 90, f"={R['cache_hit']}", f"={R['tts_cpm']}", 1),
    ("INR at 100 per USD", 1, 100, f"={R['cache_hit']}", f"={R['tts_cpm']}", 1),
    ("Cache hit 50%", 1, f"={R['fx']}", 0.5, f"={R['tts_cpm']}", 1),
    ("Cache hit 90%", 1, f"={R['fx']}", 0.9, f"={R['tts_cpm']}", 1),
    ("TTS 350 chars/min", 1, f"={R['fx']}", f"={R['cache_hit']}", 350, 1),
    ("TTS 600 chars/min", 1, f"={R['fx']}", f"={R['cache_hit']}", 600, 1),
    ("Telephony +50%", 1, f"={R['fx']}", f"={R['cache_hit']}", f"={R['tts_cpm']}", 1.5),
    ("All adverse together", 1.5, 100, 0.5, 600, 1.5),
]
for i, (name, mm, fx, ch, cpm, tm) in enumerate(SCEN, 3):
    se.cell(row=i, column=1, value=name)
    se.cell(row=i, column=2, value=mm)
    se.cell(row=i, column=3, value=fx)
    se.cell(row=i, column=4, value=ch)
    se.cell(row=i, column=5, value=cpm)
    se.cell(row=i, column=6, value=tm)
    L = {"mm": f"$B${i}", "fx": f"$C${i}", "ch": f"$D${i}", "cpm": f"$E${i}", "tm": f"$F${i}"}
    chat_usd = " + ".join(chat("gpt41mini", R["history_tokens_med"], R["chat_out_med"], L).values())
    se.cell(row=i, column=7, value=f"=({chat_usd})*{L['fx']}")
    indic_usd = " + ".join({"STT": stt_usd_per_min(L), "LLM": voice_llm_usd_per_min("gpt41mini", L), "TTS": tts_usd_per_min(L), "Telephony": tel_usd_per_min(L)}.values())
    se.cell(row=i, column=8, value=f"=({indic_usd})*{L['fx']}")
    gl_usd = f"{realtime_usd_per_min('gemini_live', L)} + {tel_usd_per_min(L)}"
    se.cell(row=i, column=9, value=f"=({gl_usd})*{L['fx']}")
    builder_usd = llm_usd(R["builder_in"], R["builder_out"], "sonnet5", L)
    for j, (pname, inr, usd, credits, chat_d, builder, agents) in enumerate(PLANS):
        # 100% utilisation: chat/day x 30 x chat cost + builder x builder cost + credits x (credit value / multiple)
        cost = f"({chat_d}*30*G{i} + {builder}*({builder_usd})*{L['fx']} + {credits}*{BASE['credit_inr']}/{BASE['mult']})"
        se.cell(row=i, column=10 + j, value=f"=1-{cost}/({inr}*(1-{R['razorpay_mdr']}))")
    se.cell(row=i, column=13, value=f"=1-H{i}/(6*(1-{R['razorpay_mdr']}))")
    se.cell(row=i, column=14, value=f"=1-H{i}/(5*(1-{R['razorpay_mdr']}))")
    ov_usd = " + ".join({"STT": f"{R['deepgram_flux_min']}*{L['mm']}", "LLM": voice_llm_usd_per_min("gpt41mini", L), "TTS": tts_usd_per_min(L, "eleven"), "Telephony": tel_usd_per_min(L, "us")}.values())
    se.cell(row=i, column=15, value=f"=1-(({ov_usd})*{L['fx']})/(0.18*{L['fx']}*(1-{R['razorpay_mdr']}))")
    ovg_usd = f"{realtime_usd_per_min('gemini_live', L)} + {tel_usd_per_min(L, 'us')}"
    se.cell(row=i, column=16, value=f"=1-(({ovg_usd})*{L['fx']})/(0.18*{L['fx']}*(1-{R['razorpay_mdr']}))")
    se.cell(row=i, column=17, value=f"=MAX({BASE['minc']},G{i}*{BASE['mult']}/{BASE['credit_inr']})")

# Percent formats
from openpyxl.styles import numbers  # noqa: E402

for sheet, cols in ((uc, [UC_CREDITS and get_column_letter(8 + len(COMPONENTS))]), (pm, ["I", "L", "M", "O", "F", "H"]), (vm, ["E", "H", "D"]), (se, ["J", "K", "L", "M", "N", "O", "P"])):
    for col in cols:
        for cell in sheet[col]:
            if isinstance(cell.value, str) and cell.value.startswith("=1-"):
                cell.number_format = "0.0%"
for cell in se["D"]:
    if cell.row > 2:
        cell.number_format = "0%"

wb.save(OUT)
print(f"wrote {OUT}")
