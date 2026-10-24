# Reporting a Software Factory defect

Use when a run sees Software Factory break its own contract (see the worker rules for what counts). Every step re-reads before it writes, because runs in other workstreams can hit the same defect at the same time.

1. **Gather evidence.** Expected and observed behavior; the exact command,
   error, and output; UTC times; `droid --version`; the workstream, activity,
   change, and Computer ids. Trace the failing path at the latest dev:
   `git -C ~/repos/factory-mono fetch origin dev`, then read files with
   `git -C ~/repos/factory-mono show origin/dev:<path>` (the checkout itself
   may be behind) and cite `file:line` with the SHA. When the source is
   unreachable or the cause stays unconfirmed, report anyway and say what is
   missing; never guess a cause. Keep secrets, tokens, and customer data out.
2. **Dedupe.** Load the tools with ToolSearch
   (`select:linear__get_issues,linear__get_comments,linear__create_issue,linear__create_comment,linear__get_issue,slack__post_message,slack__get_conversation_history`).
   Read this workstream's events
   (`droid sf db-list-events --workstream <id> --limit 200`) and stage memory
   for prior defect reports, including incomplete reports. Search SOF
   titles with `linear__get_issues` (`team_key` `SOF`, no `open_only`, no
   date cutoff) using two or three distinctive terms, and read likely matches.
   A failed search is not "no match". Search once more right before filing:
   runs in other workstreams can hit the same defect at the same time.
   - Incomplete prior report: reconcile it before applying the open-match
     rule. Re-read the known issue and the channel history; perform only the
     writes confirmed missing, then verify and record (step 5). A failed or
     incomplete read does not prove absence. Never create another ticket to
     retry its announcement.
   - Open match: do not file. Comment with `linear__create_comment` only with
     evidence the ticket and its comments (`linear__get_comments`) lack, such
     as another workstream or a new failure mode; a new run id or time is not
     new evidence. No Slack post. Name the ticket in the memory entry, and
     record an event only when you commented.
   - Completed match: check whether its fix reached the failing runtime. If
     not, reuse that ticket and note the pending rollout in memory. File a
     linked recurrence only when the fix reached that runtime or new evidence
     shows the closure was wrong.
   - Canceled match: read its reason and follow any duplicate link. File only
     when the current evidence is not covered by that disposition.
3. **File.** `linear__create_issue`: team SOF
   `1d671026-0e0b-4680-9107-55d1febc18f9`, state Pod Triage
   `0686fb8e-2323-435b-8306-3f5c266fef04`, label Bug
   `8f83f0f9-b056-4143-a09d-94d55e5b4474`, subscriber Ainesh
   `e18b7267-8ef6-48ec-a07c-2e0345b5e756`, no assignee, no priority, and the
   project that owns the failing surface:

   | Surface | Project |
   |---|---|
   | Coordinator, claims, state publish, template Computers, automations (default) | Runtime Environment `a11aa65f-c948-47d6-a16d-dfa601ef710e` |
   | `droid sf db-*` commands and the signal, change, event, and memory data | Data Model `db647d59-129e-426a-928e-b0b9a8fb5bcc` |
   | Workstream creation, generated skills, loop behavior | Loop Quality `724f0680-8e2e-4a2f-b164-1d025e7e6266` |
   | Desktop or web review UI and inbox | Workstream Dashboard `9f51a9f9-d4a7-4d44-a220-13ab8cec6a8d` |
   | Access, privacy, service accounts | Ownership & Permissions `b85c5fe1-1492-4908-880d-2fc4dc2b5952` |

   The title states the observed effect. The body has Problem, Observed,
   Impact, Code path (the SHA, or "not established" with what is missing),
   Proposed direction, Acceptance criteria, and Related, then ends with
   `Reported by the <workstream name> workstream (<workstream id>) on
   Ainesh's behalf.` SOF-391 is the model.
4. **Announce.** `slack__post_message` to #product-workstreams `C0B8NS7K4V8`:
   `Filed <issue URL|SOF-N>: <the effect in one sentence>.`, then one short
   paragraph starting `<@U0AGMAQGPC2>'s <workstream name> workstream hit it`
   with what happened, any confirmed cause, and the workaround. Write as the
   Factory bot, not as Ainesh.
5. **Verify and record.** Re-read the issue with `linear__get_issue`: team,
   state, project, Bug label, no assignee, no priority (subscribers are not
   readable there; the create call's `subscriber_ids` stands). Re-read the
   message with `slack__get_conversation_history` (`oldest` and `latest` set
   to its `ts`, `inclusive` true) and confirm it holds the issue URL. Then
   record
   `droid sf db-add-event --workstream <id> --stage <stage> --severity <severity> --title "Filed SOF-N: <title>" --detail "<impact on this workstream>. <issue URL>  Reference: Slack message <ts>"`,
   with stage `intake` or `triage` for those runs, `worker` for investigate,
   implement, and steward, and `health` for health and the steward sweep;
   severity `error` when the workstream cannot make progress, `warning`
   when action is needed but other work can continue, `info` otherwise. Name
   the ticket in this run's memory entry.
6. **Carry on.** Continue the run's task by the safest path the defect leaves
   open, and say which path in the memory entry.

If a tool is unavailable, or a write fails or remains unverified after
rereading, record one warning titled
`Software Factory defect report incomplete: <title>`. Its detail holds the
draft, every confirmed issue or Slack reference, and the next unverified step;
the next run reconciles it through step 2. Never repeat a write whose outcome
remains unknown. When `droid sf` itself is broken and no event can be
recorded, put those same recovery details in the run's final message.
