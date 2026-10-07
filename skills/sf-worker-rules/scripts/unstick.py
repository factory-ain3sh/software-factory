"""Release a Software Factory work queue halted by a failed final state save.

When the backend cannot save a finished run's workstream files, that run's
activity stays terminal with `workerId = template-computer:<computerId>`, the
coordinator stops claiming work for the workstream, and an error event names
the Computer. Only a content publication from that Computer clears the marker.

This tool publishes a base-equal proposal under that Computer's identity from a
private copy of the local marker's base files, so the server merge changes
nothing and the marker clears. It never reads or writes the real workstream
directory beyond reading `.portable-state.json`.

Usage: unstick.py --workstream <id>

Prints one JSON line: released, ambiguous, stillBlocked. Exit 0 when nothing
remains blocked and every release was announced, 1 otherwise.
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
EVENT_PAGE = 200


class SfError(Exception):
    pass


def sf(*args, env=None):
    proc = subprocess.run(["droid", "sf", *args], capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        output = (proc.stderr or proc.stdout).strip()[-800:]
        raise SfError(f"droid sf {args[0]} failed: {output}")
    return json.loads(proc.stdout)


def when(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def stamp(moment):
    return moment.strftime("%Y-%m-%d %H:%M UTC")


def list_events(workstream_id):
    events = sf("db-list-events", "--workstream", workstream_id, "--limit", str(EVENT_PAGE))["events"]
    return sorted(events, key=lambda event: event["createdAt"], reverse=True)


def list_terminal(workstream_id, status):
    # The list has no cursor: grow the page until it comes back short, so a
    # marker row past the first page cannot be mistaken for a cleared one.
    limit = 200
    while True:
        rows = sf("db-list-activities", "--workstream", workstream_id, "--status", status, "--limit", str(limit))["activities"]
        if len(rows) < limit:
            return rows
        if limit >= 3200:
            raise SfError(f"more than {limit} {status} activities; refusing to decide from a partial list")
        limit *= 2


def marker_rows(workstream_id):
    by_computer = {}
    for status in TERMINAL_STATUSES:
        for row in list_terminal(workstream_id, status):
            worker = row.get("workerId") or ""
            if worker.startswith(WORKER_PREFIX):
                by_computer.setdefault(worker[len(WORKER_PREFIX):], []).append(row)
    return by_computer


def newest_recovery_events(events):
    newest = {}
    for event in events:
        if event["title"] != RECOVERY_TITLE:
            continue
        match = COMPUTER_IN_DETAIL.search(event.get("detail") or "")
        if match and match.group(1) not in newest:
            newest[match.group(1)] = event
    return newest


def rejection(rows, event):
    """Why this event cannot authorize releasing these rows, or None."""
    event_at = when(event["createdAt"])
    for row in rows:
        if not row.get("completedAt"):
            return f"activity {row['id']} has no completion time"
        done_at = when(row["completedAt"])
        if done_at > event_at:
            return f"activity {row['id']} finished after the recovery event"
        if event_at - done_at > MAX_BLOCK_AGE:
            return f"activity {row['id']} finished more than 30 minutes before the recovery event"
    return None


def materialize(marker, workstream_id, slug, scratch):
    workstream_dir = scratch / FACTORY_DIR / "software-factory" / "workstreams" / slug
    workstream_dir.mkdir(parents=True)
    for entry in marker["baseFiles"]:
        parts = entry["path"].split("/")
        if parts[0] not in PORTABLE_ROOTS or any(part in ("", ".", "..") for part in parts):
            raise SfError(f"refusing base file path {entry['path']!r}")
        target = workstream_dir.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        if entry.get("encoding") == "base64":
            data = base64.b64decode(entry["content"])
        else:
            data = entry["content"].encode()
        target.write_bytes(data)
        target.chmod(0o755 if entry.get("executable") else 0o644)
    (workstream_dir / ".portable-state.json").write_text(json.dumps(marker))
    (workstream_dir / "workstream.json").write_text(json.dumps({"workstreamId": workstream_id, "slug": slug}))


def publish_as(workstream_id, slug, computer):
    marker_path = Path.home() / FACTORY_DIR / "software-factory" / "workstreams" / slug / ".portable-state.json"
    marker = json.loads(marker_path.read_text())
    if not marker.get("baseFiles") or not marker.get("snapshotToken"):
        raise SfError(f"{marker_path} has no base snapshot; publish this workstream once first")
    # FACTORY_HOME_OVERRIDE hides stored credentials along with everything
    # else under the real Factory home, so only an API key can authenticate.
    if not os.environ.get("FACTORY_API_KEY"):
        raise SfError("FACTORY_API_KEY is not set; state-publish cannot authenticate from a scratch Factory home")
    state_root = Path.home() / FACTORY_DIR / "state"
    state_root.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="sf-unstick-", dir=state_root))
    try:
        materialize(marker, workstream_id, slug, scratch)
        env = dict(os.environ, FACTORY_HOME_OVERRIDE=str(scratch), REMOTE_MACHINE_ID=computer)
        return sf("state-publish", "--workstream", workstream_id, env=env)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def release_detail(computer, rows, event, generation):
    started = stamp(min(when(row["completedAt"]) for row in rows))
    titles = ", ".join(sorted({row.get("changeTitle") or row["id"] for row in rows}))
    activities = ", ".join(f"activity {row['id']}" for row in rows)
    return (
        f"New work was blocked from starting since {started} because the platform could not save a finished run's files ({titles}). "
        "The block is released, so queued work runs again. What that run wrote to its workstream files after it started (its memory entry, any script or skill edit) may be lost unless the run saved them itself; everything it reported in its result is kept. No action needed unless it repeats.\n"
        f"Reference: Computer {computer}, {activities}, recovery event {event['id']}, generation {generation}, SOF-391"
    )


def resumed_detail(computer, event):
    return (
        f"New work was blocked from starting around {stamp(when(event['createdAt']))} because the platform could not save a finished run's files. "
        "The block is released, so queued work runs again. What that run wrote to its workstream files after it started (its memory entry, any script or skill edit) may be lost unless the run saved them itself; everything it reported in its result is kept. No action needed unless it repeats.\n"
        f"Reference: Computer {computer}, recovery event {event['id']}, SOF-391"
    )


def announce(workstream_id, event, detail):
    """Add the release notice once, then mark the recovery event read."""
    reference = f"recovery event {event['id']}"
    if not any(reference in (other.get("detail") or "") for other in list_events(workstream_id)):
        sf("db-add-event", "--workstream", workstream_id, "--stage", "health", "--severity", "info", "--title", RELEASE_TITLE, "--detail", detail)
    if not event.get("read"):
        sf("db-mark-events-read", "--workstream", workstream_id, "--id", event["id"])


def run(workstream):
    report = {"released": [], "ambiguous": [], "stillBlocked": []}
    row = sf("db-get-workstream", workstream)["workstream"]
    if not row.get("executionTemplateId"):
        raise SfError("workstream does not run on template Computers")
    workstream_id, slug = row["id"], row["slug"]
    rows_by_computer = marker_rows(workstream_id)
    for computer, event in newest_recovery_events(list_events(workstream_id)).items():
        rows = rows_by_computer.get(computer, [])
        entry = {"computer": computer, "recoveryEvent": event["id"], "activities": [r["id"] for r in rows]}
        if not rows:
            # An earlier run cleared the marker but never announced it.
            if not event.get("read"):
                announce(workstream_id, event, resumed_detail(computer, event))
                report["released"].append({**entry, "notified": True})
            continue
        why = rejection(rows, event)
        if why:
            report["ambiguous"].append({**entry, "reason": why})
            continue
        try:
            result = publish_as(workstream_id, slug, computer)
            remaining = [r["id"] for r in marker_rows(workstream_id).get(computer, [])]
            if remaining:
                raise SfError(f"marker rows remain after publishing: {remaining}")
        except SfError as error:
            report["stillBlocked"].append({**entry, "reason": str(error)})
            continue
        entry["generation"] = result.get("generation")
        try:
            announce(workstream_id, event, release_detail(computer, rows, event, entry["generation"]))
            report["released"].append({**entry, "notified": True})
        except SfError as error:
            report["released"].append({**entry, "notified": False, "reason": str(error)})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--workstream", required=True, help="workstream id or slug")
    workstream = parser.parse_args().workstream
    try:
        report = run(workstream)
    except SfError as error:
        print(json.dumps({"released": [], "ambiguous": [], "stillBlocked": [], "error": str(error)}))
        return 1
    print(json.dumps(report))
    unfinished = report["ambiguous"] or report["stillBlocked"] or any(not r["notified"] for r in report["released"])
    return 1 if unfinished else 0


if __name__ == "__main__":
    sys.exit(main())
