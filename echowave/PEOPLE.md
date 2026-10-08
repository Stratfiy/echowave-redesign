# People: synced contacts with context

A person's contacts, kept current, so Decibyl knows who someone is and what
last happened with them. Behind the flag `people` (`PEOPLE_ENABLED`), off by
default, honouring per-workspace overrides from the staff console. Off, the
routes are 404s, Decibyl is not handed the lookup or told the rule, nothing is
recorded from calls, mail or meetings, and the sweeps do nothing.

The product is a web app (also on phones) and an Electron app, so a browser
cannot read a phone's whole address book. Contacts come from what can be read
honestly: the person's own Google and Microsoft connections, a file they
export, the Android Chrome contact picker one tap at a time, and what Decibyl
itself does for them.

## Each contact

`people` (`api/db/people_models.py`): name, phones (E.164, India the default
country), emails (lower-case), company, relation, sources (`google`,
`microsoft`, `vcard`, `csv`, `picker`, `manual`, `decibyl`), the brief and who
wrote it (`decibyl` or `you`), and the last interaction time. Beside it:
`person_handles` (numbers and addresses for lookups), `person_sources` (the
provider's id, so a re-sync updates rather than adds), `person_interactions`
(channel, direction, when, one line; unique per owner and `ref`, so a retried
job records once), `people_syncs`, `person_merges`, `person_shares`,
`people_settings`.

## Sources

* **Google and Outlook** (`services/people/providers.py`, `sync.py`): through
  the person's **own** Composio connection only -- never the workspace's,
  because a shared mailbox's address book is not anyone's contacts. Composio's
  proxy makes one GET with the connected account, so no token reaches us.
  Google: People API `people/me/connections` with sync tokens; an expired
  token (410) reads everything again. Microsoft: Graph `me/contacts/delta`
  with delta links and `@removed`. A contact removed at the provider goes
  unless another source or Decibyl's own history keeps it. Re-sync every six
  hours (`resync_people`, hourly). States: not connected (a connect chip on
  the People screen, opening the provider's sign-in in a new tab), needs
  setup (per-person connections off, or Composio unset), syncing, synced
  with counts, error with the provider's reason -- never "0 contacts" for a
  failed read.
* **Files** (`services/people/imports.py`): vCard 2.1/3.0/4.0 (folded lines,
  groups, quoted-printable) and CSV with Google/Outlook/phone headers. 5 MB,
  5,000 contacts.
* **The phone's picker**: `navigator.contacts.select` on Android Chrome; the
  button is not shown where the API does not exist.
* **Decibyl itself** (`services/people/interactions.record`, never raises):
  - a call placed for the person (`call_for_me.execute`, and the finished
    call's outcome from the post-call task, same `run:<id>` line),
  - mail in and out of their Decibyl address (`email_identity.receive`/`send`),
  - a mail they approved from a connected app (`actions` `run_tool`),
  - a document sent on WhatsApp or email (`actions` `send_document`),
  - a meeting they captured, per named participant (`meetings.processing`).

  Not recorded: inbound calls to a business number and campaign calls, where
  no one member is the party to the conversation; and an unknown WhatsApp
  sender, who is a stranger by design (IDENTITY.md).

## Duplicates

Same contact or a duplicate? The same when the provider says so (its id is on
file) or when the name matches and they share a number or address --
importing one vCard twice changes nothing. A different name sharing a number
or address keeps both and opens a merge suggestion; nothing merges until the
person presses Merge. Keep both is remembered.

## The brief

"Who they are to you, what is open", at most three sentences
(`services/people/briefs.py`). An interaction sets `brief_due_at` once per
window (`PEOPLE_BRIEF_DEBOUNCE_SECONDS`, 10 minutes); `write_due_briefs`
(every two minutes) writes what is due on the workspace's everyday model.
The model is sent the name, company, relation, the current brief and the
last eight interaction lines with channel and date -- **no number or
address**; any that a line carries is masked first (a test reads the
prompt). An edited brief (`brief_by` = `you`) is the base of the next
rewrite. No model: "needs setup", tried again in an hour, never a fake.

## Privacy

* Every query names `organization_id` and `owner_user_id`. A colleague's
  contact id is answered as not found, the way a wrong tenant is.
* Sharing is explicit, one contact to one colleague in the same workspace,
  and shows the card only (name, numbers, addresses, company) -- never the
  brief or the history.
* Erasure and export are in the privacy center (`services/settings/privacy`,
  store `people`): contacts with briefs and interactions are exported, and
  deleted with their handles, sources, merges, shares, sync state and
  settings.
* Agents read briefs only when the person allows it (People screen, off
  until chosen): `call_for_me` passes `callee_brief` to the call; the brief
  of the person's own contact for that number, nothing else. The brief then
  travels with that call (its context and whatever the agent says), so anyone
  who can open the call's record can read it -- the setting says so.
* Chat: `lookup_person` and a context section for people named in the
  question, for the person asking only; numbers and addresses masked unless
  the model asks for them to draft or send. What Decibyl says in a *shared*
  thread is governed by `decibyl_private_threads`; turning that on with
  `people` is recommended.

The two-person check: `api/tests/test_people_privacy.py` (every flag on, B
reads every GET route without a path parameter in the internal OpenAPI;
rolled back) and `scripts/people_privacy_check.py` against a running stack
(reads `ui/openapi.internal.json`).

## Running it locally

```
python -m api.tests.support.people_fakes --port 9200 --seed <user ids>
PEOPLE_ENABLED=true PEOPLE_FAKE_PROVIDER_URL=http://127.0.0.1:9200 \
PEOPLE_BRIEF_WRITER=fake   # in api/.env, local or test only
```

## Needs a human

* **Google**: the Composio Gmail auth config (or `PEOPLE_GOOGLE_TOOLKIT`'s)
  must request `https://www.googleapis.com/auth/contacts.readonly`. It is a
  sensitive scope: Google's OAuth verification must list it, with the
  justification and a demo video. Existing connections need to connect again
  to grant it.
* **Microsoft**: `Contacts.Read` on the Outlook auth config.
* **Per-person connections** (`connections_per_person`) on wherever People is
  on: contacts only sync from a person's own connection.
* Check the provider calls against live test accounts: the request shapes
  are the providers' documented ones and are verified here only against the
  fake (`api/tests/support/people_fakes.py`), including Composio's proxy
  endpoint (`/api/v3/tools/execute/proxy`).

## Migration and rollback

`20261009people` (revises `20261008voice`): eight new tables, nothing
existing changed. Turn the flag off and the behaviour is gone at once; rows
stay. `alembic downgrade 20261008voice` drops the tables.
