---
name: sf-worker-rules
description: Operating rules for headless Software Factory workstream runs on Ainesh's behalf (intake, triage, investigate, implement, steward, steward sweep, health). Use at the start of every such run, before reading sources or changing anything.
---

# Software Factory Worker Rules

No human is present, so these rules stand in for the judgment Ainesh would apply mid-run. The workstream's skill owns *what* to do; these rules own *how carefully*. The defect report below applies in every stage, even stages that otherwise forbid posting outside the workstream.

## Act

| Moment | Action |
|---|---|
| Run starts | Run the workstream's `scripts/setup-run.sh`. State in one sentence what this run must achieve and the observable condition that proves it. |
| Reading state | Read through the workstream's reader script when it has one, and act only on what it printed. |
| Planning a fix | Write the success test first: the exact command or reader line that must flip. |
| A non-trivial diff is ready | Get an adversarial review from the `astra` droid on the diff, the plan, and the success test. Fix or rebut every finding with evidence before pushing. Non-trivial is anything beyond a mechanical change (format-only, lockfile regeneration, a clean merge of the base). |
| After every outward write | Re-read the target and confirm the write landed (rule 4). |
| Saving workstream files | Worker runs (investigate, implement, steward) never run `droid sf state-publish`: the backend saves their files after the run, and a self-publish breaks that save. Scheduled stages (intake, triage, health, steward sweep) publish once, at the end. Health and the steward sweep then run `unstick.py` ([references/unstick.md](references/unstick.md)). |
| Run ends | Re-read state against the sentence from run start: each condition done or not, with evidence. Unfinished work gets a concrete next step in memory. |

## Rules

1. **Unknown is not clean.** A failed, partial, or errored read is a failure to report, never "nothing to do". Never hide stderr (`2>/dev/null`, `|| true`) on a read you act on.
2. **One reader per state.** Every stage reads through the same reader. If it is wrong or missing a field, fix the reader (rule 8); never query around it.
3. **Name exact things:** PR numbers, check names, `file:line`, thread URLs, SHAs. Never "some checks fail".
4. **Done means re-read.** A push is done when the remote head equals your SHA; a thread when it reads resolved; a rerun when the check shows queued or running; a message when it appears in the history. When verification fails, re-read before retrying: never resend a write that may have landed.
5. **Never redefine success after seeing the result.** Report the success test you wrote as passed or failed.
6. **`memory/` holds only stage memory and the files a workstream skill names there.** Run evidence, drafts, and checkpoints go in the run's directory under `/tmp`; a retry rebuilds from the store and memory entries, never from a file an earlier attempt left in `memory/`.
7. **Findings end with an action:** who should do what, with the URL. Report a blocked item once (check existing events first), not every run.
8. **Fix the harness first.** When a script, skill, or memory note misled you, correct `scripts/` in this run if the fix is small and validated on live data; otherwise record a warning event naming the defect and the fix. Never quietly work around it.
9. **Cost never vetoes needed work.** Never skip a needed rerun, review, or validation to save time or tokens; do skip work that is not needed.
10. **Decide routine calls yourself; stop on material ones.** When a material contract or review verdict is genuinely uncertain, stop that item and record the decision for Ainesh, unless the workstream's `scripts/` procedure has a Decisions section: then settle it as that section says and never wait. Never call AskUser.
11. **Never undo a reviewer's state.** Never dismiss an approval, re-request review from someone who approved, or claim an approval is stale or auto-merge is armed without reading the current state.
12. **Diagnose CI from logs.** Read the failing job's log and runner state before editing code or calling a failure flaky. Runner loss is not a test timeout.
13. **Never defer a real issue.** "Follow-up PR", "out of scope", "tracked separately", and TODOs are not answers to a real problem, even one the change did not introduce. Fix it in this run's change, or record the decision only Ainesh can make (or that the Decisions section settles). Disagree only with evidence that the problem is not real.

## When a run opens a new PR

1. Search open and recently merged PRs touching the same files or goal, and read their diffs, before claiming the work is new.
2. Branch fresh from the default branch, record the base SHA, and compare later diffs against it, not a moving remote ref.
3. Migrate every consumer of the mechanism you change together, and delete the competing path instead of keeping both.
4. Prove a bug fix red-first: the test fails before the fix and passes after.
5. Write the body from a cited fact sheet and claims-check it against the diff; update it whenever a later fix invalidates a claim.
6. Under **Related Issue**, carry the change link the run prompt supplies (`app.factory.ai/software-factory/changes/<id>`) and the line `Opened by Ainesh's <workstream name> workstream.` PR Shepherd recognizes workstream PRs by that link.
7. Open it ready for review, and request reviewers once it is finished, unless the workstream skill says otherwise: `~/.agents/skills/slack-cli/ops/review-request.md` owns the readiness gate and whom to ask. PR Shepherd DMs Ainesh once only approval remains. Never request factory-ain3sh.

## When a run measures a target

1. Write the hypothesis, metric, controls, repetitions, and decision rule before observing results.
2. Measure the claimed target directly. Fewer characters, a passing probe, or one anecdote is not a latency, eval, or cost win.
3. Validate the harness against current source before trusting its score; correct a wrong fixture openly and keep the superseded result.

## When Software Factory itself misbehaves

Report a defect when Software Factory breaks its own contract: the coordinator, claims and requeues, state publish and memory sync, `droid sf` commands, template Computers, generated skills, automations, or the review UI. A mistake written only in this workstream's `scripts/`, skills, or memory is rule 8; a bug in the target repo is the workstream's work; an upstream outage or a guardrail working as designed is not a defect. Reporting is the required disposition for an incidental platform defect; never fold its repair into an unrelated change. Follow [references/defect-report.md](references/defect-report.md) step by step, then continue the run by the safest path the defect leaves open.

## Headless facts (ain3sh-dev template)

- `gh` and git act as factory-ain3sh, so every push, reply, and merge appears as Ainesh. Load **voice** before writing anything a person reads.
- Each run gets a fresh Computer whose repositories are checked out under `~/repos/`. Work in those checkouts directly, as the run prompt says; never create worktrees. After switching to a branch whose dependencies differ, run `npm run bootstrap` in `~/repos/factory-mono`.
- `scripts/setup-run.sh` installs Ainesh's subagent roster with built-in model ids into `~/.factory/droids/`. His laptop droids point at BYOK models that do not exist here, and an unresolvable droid model silently falls back to the session model.
- `stack` is not installed; restack with `git rebase --onto` per **sync-target**.
- Skills marked `disable-model-invocation` (address-review, implement, post-review, explain-diff) cannot load through the Skill tool: read `~/.agents/skills/<name>/SKILL.md` and its references directly.
- Laptop-only paths in `~/.agents` docs (`/home/ain3sh/...`, `ssh factory-dev-box`, `dsx`, `slck`) do not exist here.

## Failure map

| Symptom | Action |
|---|---|
| The reader script exits non-zero | Stop acting on that item and report the error text. |
| A droid is missing or runs on the wrong model | Rerun `scripts/setup-run.sh`; if still missing, use built-in subagent types and record a warning event. |
| A write's verification fails | Re-read the target; resend only when the read proves it did not land. |
| `unstick.py` exits 1 | Put its JSON line in the run's final message; [references/unstick.md](references/unstick.md) explains each field. Never release a marker by hand. |
| A `droid sf` command or another platform surface misbehaves | [references/defect-report.md](references/defect-report.md). |

## References

Load on demand; do not reabsorb into this file.
- [references/defect-report.md](references/defect-report.md): evidence, dedupe, filing, announcing, and verifying a Software Factory defect report.
- [references/unstick.md](references/unstick.md): what `unstick.py` releases and how to read its exit-1 JSON.
