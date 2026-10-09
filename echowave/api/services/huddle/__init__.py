"""The huddle: talking to an agent as a teammate, in its own thread.

The call button on an agent's thread opens a voice conversation with that
agent -- not as a caller hears it, but as its operator's colleague. It
answers about its own work (calls today, why one escalated, what it says
about something, its schedule, memory, skills and the workspace's files)
and proposes changes. A change is never made by voice: it becomes the same
edit card the agent's text chat posts (``services/workflow/self_edit``),
and only a click on that card publishes it.

Built from what exists rather than beside it:

* **Transport and minutes.** A huddle is a row in ``voice_sessions`` (one
  live voice conversation per person, either kind), on the same WebRTC
  signaling, speech services, turn-taking, interruption handling, captions
  and daily voice minutes as Talk (``services/voice/``). Only the brain
  differs: ``services/huddle/turn.py`` where Decibyl's would sit.
* **Transcript.** One ``huddle`` event on the agent's thread carries who
  said what, so there is a text record beside the cards it produced.
* **Teammate memory.** Short notes the agent keeps about how *this* person
  wants it to work, in the payload of that person's huddle events -- never
  in the agent's prompt, so nothing said in a huddle reaches a customer
  unless a card carrying it is published.

* **During a live call** (with ``live_supervision``), the huddle is that
  call's whisper channel, marked "This call only" (``live_call``): what the
  operator says or types goes to the call as a whisper, never to the caller.

Behind the ``huddle`` flag; off, every route is a 404 and the thread keeps
its old "Call me to test" link.
"""

FLAG = "huddle"
