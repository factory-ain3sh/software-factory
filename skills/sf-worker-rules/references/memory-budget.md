# Keeping workstream memory small

Every run reads its stage's whole memory file before it starts, so each durable note costs every later run, and a stale one misleads them. Writers keep inflow small; health owns consolidation for every stage of its workstream.

## Writing a durable note (every stage)

- Write one only for a lesson that would change what a later run does and that the skills, scripts, and worker rules do not already say. Run progress, a PR's status, and what this run did belong in the run entry, never in a durable note.
- Read the stage's notes first. When one already covers the topic, replace it with a merged note (append the new note, retire the old with `Merged into <new id>`) instead of adding a sibling.

## Consolidating (health, every run)

Budget per memory file: at most **30 active durable notes** for `worker`, **15** for any other stage, not counting exempt notes (below). Count active notes with:

```bash
awk '/^### Durable note:/{n++; getline; getline; if ($0 ~ /^> Retired:/) r++} END{print n-r}' memory/<stage>.md
```

- **Under budget:** retire a note only on evidence that it is wrong or no longer applies, and merge notes that give overlapping guidance.
- **Over budget:** consolidate back under it in this run. A note keeps its place only if it is still true, non-obvious, and would change what a run does. Retire, naming the reason: run- or PR-specific state with no general lesson; content the skills, scripts, or worker rules already hold (name the file); claims a newer note or current code contradicts; duplicates. Merge what remains by topic into `consolidated-<date>-<topic>` notes phrased for action ("When X, do Y, because Z (evidence, date)"), keeping exact commands, field names, thresholds, and measured numbers, then retire each original with `Merged into consolidated-<date>-<topic>`.
- A rule every run needs belongs in the skills, not in memory: record a `warning` event titled "A memory note belongs in the skills" naming the note, and keep the note until a person moves it. Never edit skills from health.
- Exempt from the budget and from consolidation: notes a skill names as a ledger (for example `pending-<changeId>` and `verdict-<changeId>` verification notes) and notes recording Ainesh's decisions (`decision-*`), except that a newer decision supersedes an older one.
- Never replace the `## Notes` preamble: it holds Ainesh's decisions. Never retire a note to silence a finding instead of resolving it.
- Use the memory contract in the health skill for the retire and merge commands.

## How to consolidate

Consolidation is skill maintenance, not housekeeping. Load these skills first and apply them to the notes as a set:

- **harness-optimization:** for each cluster of related notes, ask what made runs need them (a skill gap, a misleading instruction, a tool defect) and name that owning layer, instead of keeping a pile of workarounds.
- **single-canon:** one owner per fact. A fact the skills, scripts, or worker rules already own leaves memory; each topic ends as one note.
- **patch-coherence:** notes that describe competing workarounds or explanations for the same symptom are reconciled to the one that holds now.
- **update-skill:** surviving notes are action-first and verified against the current scripts and code; every retired note maps to its new home or a stated reason, so nothing load-bearing is lost.
- **retrospective:** before writing, review the whole set of changes as a stranger would: dead weight, drift, and slop go.

Health runs on `gpt-6-astra` at high effort, and the consolidation stays in that session.
