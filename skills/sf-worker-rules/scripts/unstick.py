"""Release a Software Factory work queue halted by a failed final state save.

When the backend cannot save a finished run's workstream files, that run's
activity stays terminal with `workerId = template-computer:<computerId>`, the
coordinator stops claiming work for the workstream, and an error event names
the Computer. Only a content publication from that Computer clears the marker.

This tool publishes a base-equal proposal under that Computer's identity from a
private copy of the local marker's base files, so the server merge changes
nothing and the marker clears. It reads `.portable-state.json` and
`workstream.json` from the real workstream directory and writes nothing there.

Supported topology: every template run gets a fresh Computer. The server
clears every terminal marker of the named Computer at publish time, not the
rows checked here, so on a reused Computer a run that finishes between the
check and the publish could lose its final save. The pre-publish re-check only
narrows that window; binding the clear to activity ids needs the backend.

Usage: unstick.py --workstream <id>

Prints one JSON line with `released`, `ambiguous`, `stillBlocked`, and
`pending` (markers with no recovery event yet). Exit 0 when nothing remains
blocked and every release was announced, 1 otherwise.
"""

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

RECOVERY_TITLE = "Worker state publication needs recovery"
COMPUTER_IN_DETAIL = re.compile(r"from Computer ([0-9a-f-]{36})")
WORKER_PREFIX = "template-computer:"
TERMINAL_STATUSES = ("completed", "failed", "canceled")
PORTABLE_ROOTS = ("memory", "scripts", "skills")
FACTORY_DIR = ".factory"
# A marker older than this relative to its recovery event may belong to an
# earlier run on a reused Computer, whose finalizer could still be live.
MAX_BLOCK_AGE = timedelta(minutes=30)
RELEASE_TITLE = "Work queue restarted after a finished run could not be saved"
# The coordinator halts claims only when a marker sits within the newest
# MAX_TOTAL_ACTIVITY_CONCURRENCY (64) rows per terminal status, newest first
# (activityCoordinator.ts hasPendingPortableStatePublication); older markers
# do not halt anything, so the same window is read here.
HALT_WINDOW = 64
# The store caps every list at 500 rows and the CLI exposes no offset.
EVENT_PAGE = 500


class SfError(Exception):
    pass


def sf(*args, env=None):
    proc = subprocess.run(["droid", "sf", *args], capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        output = (proc.stderr or proc.stdout).strip()[-800:]
        raise SfError(f"droid sf {args[0]} failed: {output}")
    return json.loads(proc.stdout)


def when(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        raise SfError(f"unreadable timestamp {value!r}")


def stamp(moment):
    return moment.strftime("%Y-%m-%d %H:%M UTC")


def list_events(workstream_id):
    """Newest events first, and whether the page holds the whole history."""
    events = sf("db-list-events", "--workstream", workstream_id, "--limit", str(EVENT_PAGE))["events"]
    events.sort(key=lambda event: event["createdAt"], reverse=True)
    return events, len(events) < EVENT_PAGE


def terminal_rows(workstream_id):
    rows = []
    for status in TERMINAL_STATUSES:
        rows += sf("db-list-activities", "--workstream", workstream_id, "--status", status, "--limit", str(HALT_WINDOW))["activities"]
    return rows


def markers(rows):
    by_computer = {}
    for row in rows:
        worker = row.get("workerId") or ""
        if worker.startswith(WORKER_PREFIX):
            by_computer.setdefault(worker[len(WORKER_PREFIX):], []).append(row)
    return by_computer


def marker_ids(rows, computer):
    return sorted(row["id"] for row in markers(rows).get(computer, []))


def newest_recovery_events(events):
    newest = {}
    for event in events:
        if event["title"] != RECOVERY_TITLE:
            continue
        match = COMPUTER_IN_DETAIL.search(event.get("detail") or "")
        if match and match.group(1) not in newest:
            newest[match.group(1)] = event
    return newest


def notices_for(events, event):
    token = re.compile(rf"recovery event {re.escape(event['id'])}(?![0-9A-Za-z-])")
    return [other for other in events if other["title"] == RELEASE_TITLE and token.search(other.get("detail") or "")]


def rejection(rows, event):
    """Why this event cannot authorize releasing these rows, or None."""
    try:
        event_at = when(event["createdAt"])
        for row in rows:
            if not row.get("completedAt"):
                return f"activity {row['id']} has no completion time"
            done_at = when(row["completedAt"])
            if done_at > event_at:
                return f"activity {row['id']} finished after the recovery event"
            if event_at - done_at > MAX_BLOCK_AGE:
                return f"activity {row['id']} finished more than 30 minutes before the recovery event"
    except SfError as error:
        return str(error)
    return None


def local_workstream_dir(slug):
    return Path.home() / FACTORY_DIR / "software-factory" / "workstreams" / slug


def read_local_json(path, what):
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError:
        raise SfError(f"local {what} {path} is missing; publish this workstream once first")
    except (OSError, ValueError) as error:
        raise SfError(f"local {what} {path} is unreadable: {error}")
    if not isinstance(value, dict):
        raise SfError(f"local {what} {path} is not a JSON object")
    return value


def local_snapshot(workstream_id, slug):
    """The real directory's marker and ownership stamp, checked to belong to this workstream."""
    directory = local_workstream_dir(slug)
    ownership = read_local_json(directory / "workstream.json", "ownership stamp")
    if ownership.get("workstreamId") != workstream_id:
        raise SfError(f"{directory} is owned by workstream {ownership.get('workstreamId')!r}, not {workstream_id} (slug reuse); refusing to publish its snapshot")
    marker = read_local_json(directory / ".portable-state.json", "marker")
    if not isinstance(marker.get("baseFiles"), list) or not marker["baseFiles"] or not marker.get("snapshotToken"):
        raise SfError(f"local marker {directory / '.portable-state.json'} has no base snapshot; publish this workstream once first")
    return marker, ownership


def materialize(marker, ownership, slug, scratch):
    workstream_dir = scratch / FACTORY_DIR / "software-factory" / "workstreams" / slug
    workstream_dir.mkdir(parents=True)
    for entry in marker["baseFiles"]:
        try:
            parts = entry["path"].split("/")
            if parts[0] not in PORTABLE_ROOTS or any(part in ("", ".", "..") for part in parts):
                raise SfError(f"refusing base file path {entry['path']!r}")
            if entry.get("encoding") == "base64":
                data = base64.b64decode(entry["content"], validate=True)
            else:
                data = entry["content"].encode()
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise SfError(f"local marker has a malformed base file entry: {error!r}")
        target = workstream_dir.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o755 if entry.get("executable") else 0o644)
    (workstream_dir / ".portable-state.json").write_text(json.dumps(marker))
    (workstream_dir / "workstream.json").write_text(json.dumps(ownership))


def publish_as(workstream_id, slug, computer):
    marker, ownership = local_snapshot(workstream_id, slug)
    # FACTORY_HOME_OVERRIDE hides stored credentials along with everything
    # else under the real Factory home, so only an API key can authenticate.
    if not os.environ.get("FACTORY_API_KEY"):
        raise SfError("FACTORY_API_KEY is not set; state-publish cannot authenticate from a scratch Factory home")
    state_root = Path.home() / FACTORY_DIR / "state"
    state_root.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="sf-unstick-", dir=state_root))
    try:
        materialize(marker, ownership, slug, scratch)
        env = dict(os.environ, FACTORY_HOME_OVERRIDE=str(scratch), REMOTE_MACHINE_ID=computer)
        return sf("state-publish", "--workstream", workstream_id, env=env)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


RELEASE_BODY = (
    "That run's save is now recorded as complete (its publication marker is cleared), so it no longer blocks new work from starting. "
    "What it wrote to its workstream files after it started (its memory entry, any script or skill edit) may be lost unless the run saved them itself; "
    "everything it reported in its result is kept. No action needed unless it repeats.\n"
)


def release_detail(computer, rows, event, generation):
    started = stamp(min(when(row["completedAt"]) for row in rows))
    titles = ", ".join(sorted({row.get("changeTitle") or row["id"] for row in rows}))
    activities = ", ".join(f"activity {row['id']}" for row in rows)
    generation_note = f", generation {generation}" if generation is not None else ""
    return (
        f"New work was blocked from starting since {started} because the platform could not save a finished run's files ({titles}). "
        + RELEASE_BODY
        + f"Reference: Computer {computer}, {activities}, recovery event {event['id']}{generation_note}, SOF-391"
    )


def resumed_detail(computer, event):
    return (
        f"New work was blocked from starting around {stamp(when(event['createdAt']))} because the platform could not save a finished run's files. "
        + RELEASE_BODY
        + f"Reference: Computer {computer}, recovery event {event['id']}, SOF-391"
    )


def announce(workstream_id, event, detail, events=None):
    """Leave exactly one release notice for the recovery event, then mark it read.

    Returns whether a notice was added or the event was marked read.
    """
    if events is None:
        events, _ = list_events(workstream_id)
    added = False
    if not notices_for(events, event):
        sf("db-add-event", "--workstream", workstream_id, "--stage", "health", "--severity", "info", "--title", RELEASE_TITLE, "--detail", detail)
        events, _ = list_events(workstream_id)
        added = True
    # Two stages can release the same Computer at once and the store has no
    # idempotency key, so every actor keeps the oldest notice and deletes the
    # rest; a delete that loses the race to another actor is already done.
    duplicates = sorted(notices_for(events, event), key=lambda notice: (notice["createdAt"], notice["id"]))[1:]
    for duplicate in duplicates:
        try:
            sf("db-delete-event", duplicate["id"])
        except SfError:
            pass
    if not event.get("read"):
        sf("db-mark-events-read", "--workstream", workstream_id, "--id", event["id"])
        return True
    return added


def run(workstream):
    report = {"released": [], "ambiguous": [], "stillBlocked": [], "pending": []}
    row = sf("db-get-workstream", workstream)["workstream"]
    if not row.get("executionTemplateId"):
        raise SfError("workstream does not run on template Computers")
    workstream_id, slug = row["id"], row["slug"]
    events, events_complete = list_events(workstream_id)
    recovery = newest_recovery_events(events)
    rows_by_computer = markers(terminal_rows(workstream_id))
    for computer, rows in rows_by_computer.items():
        if computer in recovery:
            continue
        entry = {"computer": computer, "activities": [r["id"] for r in rows]}
        if events_complete:
            report["pending"].append({**entry, "reason": "no recovery event yet; the platform may still be saving that run"})
        else:
            report["stillBlocked"].append({**entry, "reason": f"no recovery event within the newest {EVENT_PAGE} events; the read is incomplete"})
    for computer, event in recovery.items():
        rows = rows_by_computer.get(computer, [])
        entry = {"computer": computer, "recoveryEvent": event["id"], "activities": sorted(r["id"] for r in rows)}
        if not rows:
            # A release that was never announced, or announced but never
            # marked read, finishes here; the inbox's read flag is not proof
            # of delivery, so a notice is owed until one exists.
            try:
                if announce(workstream_id, event, resumed_detail(computer, event), events):
                    report["released"].append({**entry, "notified": True})
            except SfError as error:
                report["released"].append({**entry, "notified": False, "reason": str(error)})
            continue
        why = rejection(rows, event)
        if why:
            report["ambiguous"].append({**entry, "reason": why})
            continue
        before = terminal_rows(workstream_id)
        if marker_ids(before, computer) != entry["activities"]:
            report["stillBlocked"].append({**entry, "reason": f"marker rows for Computer {computer} changed while authorizing: {marker_ids(before, computer)}"})
            continue
        generation, publish_error = None, None
        try:
            generation = publish_as(workstream_id, slug, computer).get("generation")
        except SfError as error:
            # The CLI can fail after the server committed and cleared the
            # marker; the marker state decides, not the exit code.
            publish_error = str(error)
        after = terminal_rows(workstream_id)
        remaining = marker_ids(after, computer)
        if remaining:
            report["stillBlocked"].append({**entry, "reason": publish_error or f"marker rows remain after publishing: {remaining}"})
            continue
        entry["generation"] = generation
        if publish_error:
            entry["publishError"] = publish_error
        # The server clears every terminal marker of the Computer, not the
        # authorized ids: a row that finished during the publish and lost its
        # marker may have lost its final save (Computer reuse only).
        before_ids = {row["id"] for row in before}
        swept = sorted(row["id"] for row in after if row["id"] not in before_ids and not row.get("workerId"))
        if swept:
            entry["clearedUnverified"] = swept
        try:
            announce(workstream_id, event, release_detail(computer, rows, event, generation))
            report["released"].append({**entry, "notified": True})
        except SfError as error:
            report["released"].append({**entry, "notified": False, "reason": str(error)})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--workstream", required=True, help="workstream id or slug")
    workstream = parser.parse_args().workstream
    empty = {"released": [], "ambiguous": [], "stillBlocked": [], "pending": []}
    try:
        report = run(workstream)
    except SfError as error:
        print(json.dumps({**empty, "error": str(error)}))
        return 1
    except Exception as error:  # the one JSON line is the contract, even for surprises
        print(json.dumps({**empty, "error": f"{type(error).__name__}: {error}"}))
        return 1
    print(json.dumps(report))
    blocked = report["ambiguous"] or report["stillBlocked"] or report["pending"] or any(not r["notified"] or "clearedUnverified" in r for r in report["released"])
    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
