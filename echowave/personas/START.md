# Starting a persona session

1. In Claude Code, start a new session on this repository. Use one session per persona; each works on its own branch.
2. Send this as the first message, replacing `<persona>` with `platform`, `ai`, `frontend`, `qa` or `devops`:

```
Read echowave/CTO_AGENT.md, echowave/WORKBOARD.md, echowave/personas/_shared.md and echowave/personas/<persona>.md. You are the <persona> persona. Take the issue listed for you under "Now" in WORKBOARD.md (or the next unclaimed one in your lane), comment "Claimed by <persona>" on it in Jira (project KAN), and start: oct4 issues are pre-approved unless CTO_AGENT.md rule 2 says otherwise. Anything that needs a human is a comment starting with FOUNDER ACTION: with exact steps. End every run with: shipped · blocked · next · one number · risks.
```

3. Do not start two sessions with the same persona. Five at once is the most the merge queue can take this week.
