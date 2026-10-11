"""A marketing manager for a small business: the week's posts, planned and
written, every Monday.

The poster designer and the ad creative maker make one image when asked.
What an owner of a shop, a clinic or a coaching class actually lacks is the
person who decides *what* to post this week, writes it, notices that
Diwali is in ten days, and has the offer message ready before the owner
remembers to ask. That is this agent.

**It runs on the clock** (``scheduled``): every Monday it plans the week --
three to five posts per channel the business uses, each with the caption,
the hashtags, the call to action and the best time to post -- plus one
promotion message the owner can broadcast, and posters for the two posts
that need a picture most. The plan lands on the agent's thread in full
(``routine_runner.MAX_REPORT``), and the owner asks for changes there.

**It does not wait on images.** ``offers_images``: while
``image_generation`` is on it makes the posters; while it is off it writes
each poster's brief instead, and the plan is still the plan.

**It never posts and never sends by itself.** Every send goes through an
approval card (``approve_sends``), and the plan says what to post where,
for the owner or their staff to post. Posting straight to Instagram on the
business's behalf is a different product with a different failure mode.

**It never makes up an offer.** The rule ``creative.CREATIVE_GUARDRAILS``
holds for a poster holds for a caption too: a price, a discount or a claim
is one the business gave, or one it can find in what the business taught
Decibyl (its website, its files). Otherwise the post says "[your offer]"
and the plan asks.

The occasions calendar is written into the prompt rather than computed,
because a hired agent's prompt is a copy taken at hire: a list computed at
import would be frozen at the day it was hired anyway. So it is a list with
an end, and the prompt says what to do past it (look the date up on the
web), and which dates move with the moon (check locally).
"""

from __future__ import annotations

from api.services.agent_templates._base import (
    AgentTemplate,
    CallDirection,
    ScheduleShape,
    TemplateEdge,
    TemplateNode,
)
from api.services.agent_templates.creative import CREATIVE_GUARDRAILS

#: When the plan arrives. Parsed by ``schedule_from_words`` into the routine
#: the hire creates (switched off until a test run, like every routine).
RUNS = "every Monday at 9:30am"

#: The days a business in India plans posts around, from today to the end of
#: 2027. Fixed-date days are exact; the rest follow the lunar calendar and
#: are marked "around" -- the prompt tells the agent to confirm those.
OCCASIONS: tuple[tuple[str, str], ...] = (
    ("around 20 Oct 2026", "Dussehra (end of Navratri)"),
    ("around 29 Oct 2026", "Karwa Chauth"),
    ("31 Oct 2026", "Halloween"),
    ("6 Nov 2026", "Dhanteras"),
    ("8 Nov 2026", "Diwali"),
    ("around 10-11 Nov 2026", "Bhai Dooj"),
    ("14 Nov 2026", "Children's Day"),
    ("around 15 Nov 2026", "Chhath Puja"),
    ("24 Nov 2026", "Guru Nanak Jayanti"),
    ("27 Nov 2026", "Black Friday"),
    ("30 Nov 2026", "Cyber Monday"),
    ("25 Dec 2026", "Christmas"),
    ("31 Dec 2026", "New Year's Eve"),
    ("1 Jan 2027", "New Year"),
    ("13 Jan 2027", "Lohri"),
    ("14 Jan 2027", "Makar Sankranti and Pongal"),
    ("26 Jan 2027", "Republic Day"),
    ("14 Feb 2027", "Valentine's Day"),
    ("around 6 Mar 2027", "Maha Shivratri"),
    ("8 Mar 2027", "International Women's Day"),
    ("around 10 Mar 2027", "Eid al-Fitr (set by the moon sighting)"),
    ("around 22-23 Mar 2027", "Holi"),
    ("around 7 Apr 2027", "Ugadi and Gudi Padwa"),
    ("around 14 Apr 2027", "Baisakhi, Tamil New Year and Vishu"),
    ("around 15 Apr 2027", "Ram Navami"),
    ("9 May 2027", "Mother's Day"),
    ("around 17 May 2027", "Eid al-Adha (set by the moon sighting)"),
    ("20 Jun 2027", "Father's Day"),
    ("21 Jun 2027", "International Yoga Day"),
    ("1 Aug 2027", "Friendship Day"),
    ("15 Aug 2027", "Independence Day"),
    ("around 17 Aug 2027", "Raksha Bandhan"),
    ("around 25 Aug 2027", "Janmashtami"),
    ("around 4 Sep 2027", "Ganesh Chaturthi"),
    ("around 12 Sep 2027", "Onam"),
    ("around 30 Sep 2027", "Navratri begins"),
    ("around 9 Oct 2027", "Dussehra"),
    ("around 29 Oct 2027", "Diwali"),
    ("25 Dec 2027", "Christmas"),
)

#: Where the list ends, said in the prompt so the agent knows when to look.
OCCASIONS_END = "31 Dec 2027"


def occasions_block() -> str:
    lines = "\n".join(f"- {when}: {name}" for when, name in OCCASIONS)
    return (
        "Occasions customers in India shop and celebrate around (the "
        "business's own region and customers decide which matter; a "
        'date marked "around" follows the lunar calendar -- check it on '
        "the web before you name a day in a post):\n"
        f"{lines}\n"
        f"For a week after {OCCASIONS_END}, or a regional festival not on "
        "this list, look the date up on the web; never name a date you have "
        "not checked."
    )


MARKETING_GUARDRAILS = CREATIVE_GUARDRAILS + [
    "Never post, publish or send anything yourself unless a person approves "
    "it on the card. The plan says what to post where; the owner or their "
    "staff posts it.",
    "A price, discount, offer, stock level or result in a caption is one "
    "the business gave you or one written in its own knowledge (its "
    'website, its files). Otherwise write "[your offer]" in its place and '
    "ask for it in the plan's last line.",
    "Never copy another business's post, slogan or image, and never name a "
    "competitor in a post.",
    'No health, money or exam-result promise in any post ("cures", '
    '"guaranteed returns", "100% selection"), whatever the business says.',
    "A promotion message goes only to customers who agreed to hear from the "
    "business, and it always says how to stop getting them.",
]

_COMPLIANCE = [
    "Every claim, price and offer in a post must be one the business will "
    "honour (Consumer Protection Act 2019, misleading advertisements, and "
    "the ASCI code for influencer and health claims). The agent leaves a "
    "gap rather than fill one in, but a person reads every post before it "
    "goes out.",
    "A promotional WhatsApp or SMS broadcast goes only to customers who "
    "opted in (WhatsApp Business policy; TRAI's commercial communication "
    "rules for SMS). The agent drafts it; sending it is approved on a card.",
    "Images are made by the provider the workspace connected, on that "
    "provider's terms, and only while image generation is on.",
]

_PLAN = (
    "You are the marketing manager for {{business_name}}. Every Monday you "
    "plan and write the week's marketing, so the owner only has to read, "
    "approve and post. You write to the owner, not to customers.\n\n"
    "What you know about the business: it sells {{what_you_sell}}; its "
    "customers are {{customers}}; it posts on {{channels}}; posts are written "
    "in {{post_language}}. Read what the business taught you -- its website "
    "and files, in your knowledge -- for products, prices and offers, and "
    "use only what is there or what the owner told you.\n\n"
    "Each run, in order:\n"
    "1. Look ahead four weeks from today's date for occasions that matter to "
    "these customers (the list below), and on the web for anything local "
    "and current: a trend, a season, a big match, school exams. Pick at "
    "most two to build the week around, and say why in one line each.\n"
    "2. Plan 3 to 5 posts for each channel in {{channels}}, spread across "
    "the week. Mix the kinds: a product or service in use, a customer "
    "question answered, behind the scenes, an offer only if there is one, "
    "and one post that asks customers something. For each post write:\n"
    "   - the day and the best time to post it (evenings 7-9pm and "
    "lunchtime work for most local businesses; say so if you are guessing),\n"
    "   - the channel and format (feed post, reel idea with its first three "
    "seconds, story, WhatsApp status, Google Business Profile update),\n"
    "   - the caption in full, in {{post_language}}, ready to paste, with "
    "the call to action (call, WhatsApp, visit, book, order),\n"
    "   - 5 to 8 hashtags: a few local (the city or area), a few for the "
    "trade, none that are banned or unrelated.\n"
    "3. Write one promotion message the owner can send to customers who "
    "opted in, on WhatsApp or email: short, one offer, one action, and a "
    'line saying how to stop getting these ("Reply STOP"). If there is no '
    'offer to promote this week, write a "we are here for you" message '
    "about the occasion instead, with no offer in it.\n"
    "4. Pictures: for the two posts that most need one, make 2 options each "
    "with make_images (every word on the image in business_name, headline "
    "and lines, exactly as it should read). If make_images is not in this "
    "conversation, write each one's brief instead: the size, the few words "
    "on it, the subject, the colours and the mood.\n"
    "5. The plan's Google Sheet, if one was given: {{plan_sheet}}. If there "
    "is one and Google Sheets is connected, write one row per post: date, time, channel, "
    'format, caption, hashtags, status "to post". If none was given, skip '
    "this step and say nothing about it.\n\n"
    "Your last message is the plan, in full, in this order: the week's "
    "theme in one line; the posts, grouped by day; the promotion message; "
    "the pictures (on this thread) or their briefs; and last, in one line, "
    "anything you need from the owner -- a price, a photo, whether an "
    "offer is still on. Plain text, short lines, no tables.\n\n"
    "When the owner replies on this thread: rewrite the posts they name, in "
    "the change they ask for; make a picture again with edit_image_id when "
    "they want one changed; and if they ask you to send the promotion, send "
    "it through the connected app so it comes to them as a card to approve. "
    "Never say something was posted or sent before the tool says so.\n\n"
    "If {{business_name}} has taught you nothing yet and the owner gave no "
    "products, plan the week from {{what_you_sell}} alone and end by asking "
    "for their website or price list, so next week's plan can be specific."
)

_CLOSE = (
    "The owner has the week's plan and has approved it or stopped for now. "
    "Say in one sentence that next Monday's plan comes at the same time and "
    "that they can ask for changes here any time. Do not end on a question."
)


def marketing_manager() -> AgentTemplate:
    from api.services.agent_templates.catalogue import _QUIET, _QUIET_GUARDRAILS

    return AgentTemplate(
        id="marketing_manager",
        name="Marketing manager",
        vertical="Shops, restaurants, clinics, salons, gyms and coaching classes",
        industry="Any business",
        function="Make content",
        direction=CallDirection.scheduled,
        summary=(
            "Every Monday, plans and writes the week's posts for Instagram, "
            "Facebook, WhatsApp status and Google, with captions, hashtags, "
            "posting times and posters, around the festivals coming up -- "
            "plus one promotion message ready to approve. Never an offer "
            "you did not give."
        ),
        languages=[
            "English",
            "Hindi",
            "Tamil",
            "Telugu",
            "Kannada",
            "Marathi",
            "Bengali",
            "Gujarati",
        ],
        stack=_QUIET,
        schedule_shape=ScheduleShape(
            runs=RUNS,
            typical_items_per_run=15,
            typical_runs_per_month=4,
        ),
        template_variables={
            "business_name": "The business, as customers know it",
            "what_you_sell": "What the business sells, and what it is known for",
            "customers": "Who the customers are and where they are",
            "channels": "Where the business posts",
            "post_language": "The language posts are written in",
            "plan_sheet": "A Google Sheet each week's posts are written to",
        },
        optional_variables=["plan_sheet"],
        needs_web=True,
        offers_images=True,
        apps=["googlesheets", "gmail", "mailchimp"],
        approve_sends=True,
        nodes=[
            TemplateNode(
                type="startCall",
                name="Plan the week",
                prompt=_PLAN + "\n\n" + occasions_block(),
                extract={
                    "theme": "The week's theme or occasion",
                    "posts": "How many posts are in the plan",
                    "needs_from_owner": "What the plan is waiting on from the owner",
                },
            ),
            TemplateNode(type="endCall", name="Close", prompt=_CLOSE),
        ],
        edges=[
            TemplateEdge(
                source="Plan the week",
                target="Close",
                label="done",
                condition=(
                    "The owner has approved the week's plan or said they are "
                    "done for this week"
                ),
            ),
        ],
        # Nobody is on the other end of a Monday run: the quiet house rules
        # (read by default, say where a figure came from) come first.
        guardrails=_QUIET_GUARDRAILS + MARKETING_GUARDRAILS,
        compliance_notes=_COMPLIANCE,
        example_requests=[
            "a marketing agent for my business",
            "plan my social media posts every week",
            "write Instagram and Facebook posts for my shop",
            "a Diwali marketing plan with posters",
            "marketing manager",
        ],
    )


def templates() -> tuple[AgentTemplate, ...]:
    return (marketing_manager(),)


__all__ = [
    "MARKETING_GUARDRAILS",
    "OCCASIONS",
    "RUNS",
    "marketing_manager",
    "occasions_block",
    "templates",
]
