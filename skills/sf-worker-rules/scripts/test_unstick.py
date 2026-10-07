"""Tests for unstick.py against a fake `droid` that models only what it needs.

Run: python3 -B test_unstick.py
"""

import base64
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
UNSTICK = HERE / "unstick.py"
WS_ID = "65732847-500e-4f2f-b836-da7e05498812"
SLUG = "pr-shepherd"
COMPUTER = "d375f7d6-368c-4eb3-b911-3ea61fc72fcd"
OTHER_COMPUTER = "7e608741-b124-41f9-a4dd-6d7fe577ccf0"
TOKEN = "************************************"
EVENT_AT = datetime(2026, 10, 7, 12, 43, 59, tzinfo=timezone.utc)
RECOVERY_TITLE = "Worker state publication needs recovery"
RELEASE_TITLE = "Work queue restarted after a finished run could not be saved"

FAKE_DROID = r'''#!/usr/bin/env python3
"""Fake `droid sf` backed by a JSON state file (FAKE_SF_STATE)."""
import base64, json, os, sys
from pathlib import Path

state_path = Path(os.environ["FAKE_SF_STATE"])
state = json.loads(state_path.read_text())
argv = sys.argv[1:]
assert argv[0] == "sf", argv
cmd, args = argv[1], argv[2:]
state.setdefault("calls", []).append({"cmd": cmd, "args": args, "home_override": os.environ.get("FACTORY_HOME_OVERRIDE"), "machine": os.environ.get("REMOTE_MACHINE_ID")})

def opt(name, default=None, repeat=False):
    values = [args[i + 1] for i, a in enumerate(args) if a == name]
    if repeat:
        return values
    return values[-1] if values else default

def done(payload, code=0):
    state_path.write_text(json.dumps(state))
    print(json.dumps(payload))
    sys.exit(code)

def fail(message):
    state_path.write_text(json.dumps(state))
    print(json.dumps({"ok": False, "error": message}), file=sys.stderr)
    sys.exit(1)

def page_limit():
    limit = int(opt("--limit", "100"))
    if limit < 1 or limit > 500:
        fail("SoftwareFactoryStoreOperationSchema: limit must be between 1 and 500")
    return limit

def terminal_marker_rows(computer):
    return [r for r in state["activities"] if r["status"] in ("completed", "failed", "canceled") and r.get("workerId") == f"template-computer:{computer}"]

def add_event(title, detail, stage, severity):
    event = {"id": f"evt-{len(state['events']) + 1}", "workstreamId": opt("--workstream"), "stage": stage, "severity": severity, "title": title, "detail": detail, "read": False, "createdAt": f"2026-10-07T13:00:{len(state['events']):02d}.000Z"}
    state["events"].append(event)
    return event

if cmd == "db-get-workstream":
    done({"ok": True, "workstream": state["workstream"]})
if cmd == "db-list-events":
    assert opt("--workstream") == state["workstream"]["id"]
    limit = page_limit()
    events = sorted(state["events"], key=lambda e: e["createdAt"], reverse=True)
    done({"ok": True, "events": events[:limit]})
if cmd == "db-list-activities":
    assert opt("--workstream") == state["workstream"]["id"]
    limit = page_limit()
    status = opt("--status")
    lists = sum(1 for c in state["calls"] if c["cmd"] == "db-list-activities")
    if state.get("newRowAfterListCalls") is not None and lists == state["newRowAfterListCalls"] + 1:
        state["activities"].append(state["newRow"])
    rows = sorted((r for r in state["activities"] if status is None or r["status"] == status), key=lambda r: r["createdAt"], reverse=True)
    done({"ok": True, "activities": rows[:limit]})
if cmd == "db-add-event":
    if state.get("addEventFails"):
        fail("fake: add-event refused")
    event = add_event(opt("--title"), opt("--detail"), opt("--stage"), opt("--severity", "info"))
    if state.get("addEventDuplicates"):
        add_event(opt("--title"), opt("--detail"), opt("--stage"), opt("--severity", "info"))
    done({"ok": True, "event": event})
if cmd == "db-delete-event":
    before = len(state["events"])
    state["events"] = [e for e in state["events"] if e["id"] != args[0]]
    if len(state["events"]) == before:
        fail("fake: Event not found")
    done({"ok": True, "deleted": args[0]})
if cmd == "db-mark-events-read":
    ids = opt("--id", repeat=True)
    marked = 0
    for e in state["events"]:
        if e["id"] in ids and not e["read"]:
            e["read"] = True
            marked += 1
    done({"ok": True, "marked": marked})
if cmd == "state-publish":
    if state.get("publishFails"):
        fail("fake: Portable workstream merge base is unavailable")
    home = os.environ.get("FACTORY_HOME_OVERRIDE")
    computer = os.environ.get("REMOTE_MACHINE_ID")
    if not home or not computer:
        fail("fake: publish needs FACTORY_HOME_OVERRIDE and REMOTE_MACHINE_ID")
    if not os.environ.get("FACTORY_API_KEY"):
        fail("fake: not authenticated")
    wdir = Path(home) / ".factory" / "software-factory" / "workstreams" / state["workstream"]["slug"]
    stamp = json.loads((wdir / "workstream.json").read_text()) if (wdir / "workstream.json").exists() else None
    state["lastOwnership"] = stamp
    if stamp is not None and stamp.get("workstreamId") != state["workstream"]["id"]:
        fail("fake: Workstream directory is owned by a different workstream (slug reuse)")
    marker = json.loads((wdir / ".portable-state.json").read_text())
    proposed = {}
    for root in ("memory", "scripts", "skills"):
        for p in sorted((wdir / root).rglob("*")) if (wdir / root).exists() else []:
            if p.is_file():
                proposed[p.relative_to(wdir).as_posix()] = {"bytes": base64.b64encode(p.read_bytes()).decode(), "executable": bool(p.stat().st_mode & 0o100)}
    base = {}
    for f in marker.get("baseFiles") or []:
        raw = base64.b64decode(f["content"]) if f.get("encoding") == "base64" else f["content"].encode()
        base[f["path"]] = {"bytes": base64.b64encode(raw).decode(), "executable": bool(f.get("executable"))}
    state["lastProposed"] = proposed
    if proposed != base:
        fail("fake: proposed files differ from the marker base; a no-op publish was required")
    if marker.get("snapshotToken") != state["snapshotToken"]:
        fail("fake: unknown snapshot token")
    state["generation"] += 1
    if state.get("newRowOnPublish"):
        state["activities"].append(state["newRowOnPublish"])
    if not state.get("publishClearsNothing"):
        for r in terminal_marker_rows(computer):
            r["workerId"] = None
    state.setdefault("publishes", []).append({"computer": computer, "generation": state["generation"]})
    (wdir / ".portable-state.json").write_text(json.dumps({"generation": state["generation"], "baseFiles": marker["baseFiles"], "snapshotToken": state["snapshotToken"]}))
    if state.get("publishFailsAfterCommit"):
        fail("fake: reading the current snapshot failed after the server committed")
    done({"ok": True, "generation": state["generation"], "files": marker["baseFiles"], "snapshotToken": state["snapshotToken"]})
fail(f"fake: unknown command {cmd}")
'''


def iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def recovery_event(event_id="rec-1", computer=COMPUTER, at=EVENT_AT, read=False):
    return {
        "id": event_id,
        "workstreamId": WS_ID,
        "severity": "error",
        "stage": "worker",
        "title": RECOVERY_TITLE,
        "detail": f"Portable state could not be published from Computer {computer}. The Computer was preserved for recovery.",
        "read": read,
        "createdAt": iso(at),
    }


def plain_event(event_id, at, title="Health: all quiet", detail=""):
    return {"id": event_id, "workstreamId": WS_ID, "severity": "info", "stage": "health", "title": title, "detail": detail, "read": False, "createdAt": iso(at)}


def notice_event(event_id, at, recovery_id="rec-1", computer=COMPUTER, title=RELEASE_TITLE):
    return plain_event(event_id, at, title=title, detail=f"...\nReference: Computer {computer}, recovery event {recovery_id}, SOF-391")


def activity(activity_id, completed_at, computer=COMPUTER, status="completed", title="Get the tool output bounds PR past its failing check", marker=True):
    row = {
        "id": activity_id,
        "workstreamId": WS_ID,
        "kind": "steward",
        "status": status,
        "createdAt": iso(completed_at - timedelta(minutes=10)),
        "completedAt": iso(completed_at),
        "changeTitle": title,
    }
    if marker:
        row["workerId"] = f"template-computer:{computer}"
    return row


def base_file(path, content, encoding="utf8", executable=False):
    raw = base64.b64decode(content) if encoding == "base64" else content.encode()
    return {"path": path, "content": content, "encoding": encoding, "executable": executable, "sizeBytes": len(raw), "fingerprint": hashlib.sha256(raw).hexdigest()}


BASE_FILES = [
    base_file("memory/worker.md", "# Worker memory\n\n- entry one\n"),
    base_file("scripts/setup-run.sh", "#!/usr/bin/env bash\necho hi\n", executable=True),
    base_file("scripts/blob.bin", base64.b64encode(bytes(range(256))).decode(), encoding="base64"),
    base_file("scripts/tool.bin", base64.b64encode(b"\x00\x01binary-exec\xff").decode(), encoding="base64", executable=True),
    base_file("skills/health/SKILL.md", "# Health\n"),
]


def tree_digest(root):
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*")):
        rel = path.relative_to(root).as_posix().encode()
        mode = stat.S_IMODE(path.lstat().st_mode)
        digest.update(rel + b"\0" + str(mode).encode() + b"\0")
        if path.is_file():
            digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


class Harness:
    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="unstick-test-"))
        self.home = self.root / "home"
        self.bin = self.root / "bin"
        self.bin.mkdir(parents=True)
        fake = self.bin / "droid"
        fake.write_text(FAKE_DROID)
        fake.chmod(0o755)
        self.state_path = self.root / "state.json"
        self.local_dir = self.home / ".factory" / "software-factory" / "workstreams" / SLUG
        self.local_dir.mkdir(parents=True)
        self.marker = {"generation": 63, "baseFiles": BASE_FILES, "snapshotToken": TOKEN}
        (self.local_dir / ".portable-state.json").write_text(json.dumps(self.marker))
        (self.local_dir / "workstream.json").write_text(json.dumps({"workstreamId": WS_ID, "slug": SLUG}))
        for entry in BASE_FILES:
            target = self.local_dir / entry["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            raw = base64.b64decode(entry["content"]) if entry["encoding"] == "base64" else entry["content"].encode()
            target.write_bytes(raw)
            if entry["executable"]:
                target.chmod(0o755)
        self.state = {
            "workstream": {"id": WS_ID, "slug": SLUG, "executionTemplateId": "438d7916-278f-4e4a-8c0c-135db41ef20b"},
            "events": [],
            "activities": [],
            "generation": 83,
            "snapshotToken": TOKEN,
        }

    def run(self, api_key="test-key"):
        self.state_path.write_text(json.dumps(self.state))
        env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "HOME": str(self.home),
            "FAKE_SF_STATE": str(self.state_path),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        if api_key:
            env["FACTORY_API_KEY"] = api_key
        proc = subprocess.run([sys.executable, "-B", str(UNSTICK), "--workstream", WS_ID], capture_output=True, text=True, env=env)
        self.state = json.loads(self.state_path.read_text())
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        assert len(lines) == 1, f"expected one JSON line, got {proc.stdout!r} stderr={proc.stderr!r}"
        return proc.returncode, json.loads(lines[0]), proc.stderr

    def event(self, event_id):
        return next(e for e in self.state["events"] if e["id"] == event_id)

    def notices(self):
        return [e for e in self.state["events"] if e["title"] == RELEASE_TITLE]

    def marker_rows(self):
        return sorted(a["id"] for a in self.state["activities"] if a.get("workerId"))

    def calls(self, cmd=None):
        return [c for c in self.state.get("calls", []) if cmd is None or c["cmd"] == cmd]

    def scratch_dirs(self):
        return list((self.home / ".factory" / "state").glob("sf-unstick-*")) if (self.home / ".factory" / "state").exists() else []

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


EMPTY = {"released": [], "ambiguous": [], "stillBlocked": [], "pending": []}


class UnstickTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.addCleanup(self.h.cleanup)
        self.addCleanup(self.assert_no_pycache)

    def assert_no_pycache(self):
        self.assertEqual([], list(HERE.rglob("__pycache__")))

    def seed(self, *rows, read=False):
        self.h.state["events"] = [recovery_event(read=read)]
        self.h.state["activities"] = list(rows) or [activity("act-1", EVENT_AT - timedelta(minutes=5))]

    # ── release journey ──────────────────────────────────────────────────

    def test_release_clears_marker_announces_then_marks_read(self):
        self.seed(
            activity("act-1", EVENT_AT - timedelta(minutes=5)),
            activity("act-2", EVENT_AT - timedelta(minutes=12), status="failed", title="Other change"),
        )
        code, report, stderr = self.h.run()
        self.assertEqual(0, code, (report, stderr))
        self.assertEqual(1, len(report["released"]))
        released = report["released"][0]
        self.assertEqual(COMPUTER, released["computer"])
        self.assertEqual(["act-1", "act-2"], released["activities"])
        self.assertEqual(84, released["generation"])
        self.assertTrue(released["notified"])
        self.assertEqual([], report["ambiguous"] + report["stillBlocked"] + report["pending"])
        self.assertEqual([], self.h.marker_rows())
        self.assertEqual([{"computer": COMPUTER, "generation": 84}], self.h.state["publishes"])
        publish_call = self.h.calls("state-publish")[0]
        self.assertEqual(COMPUTER, publish_call["machine"])
        self.assertTrue(publish_call["home_override"].startswith(str(self.h.home / ".factory" / "state")), publish_call)
        self.assertFalse(Path(publish_call["home_override"]).exists(), "scratch home must be removed")
        self.assertEqual({"workstreamId": WS_ID, "slug": SLUG}, self.h.state["lastOwnership"])
        notices = self.h.notices()
        self.assertEqual(1, len(notices))
        detail = notices[0]["detail"]
        self.assertIn("since 2026-10-07 12:31 UTC", detail)
        self.assertIn("Get the tool output bounds PR past its failing check", detail)
        self.assertIn("Other change", detail)
        self.assertIn("may be lost", detail)
        self.assertIn("no longer blocks new work", detail)
        self.assertNotIn("queued work runs again", detail)
        for ref in (f"Computer {COMPUTER}", "activity act-1", "activity act-2", "recovery event rec-1", "generation 84", "SOF-391"):
            self.assertIn(ref, detail)
        self.assertEqual("info", notices[0]["severity"])
        self.assertTrue(self.h.event("rec-1")["read"])
        order = [c["cmd"] for c in self.h.calls() if c["cmd"] in ("state-publish", "db-add-event", "db-mark-events-read")]
        self.assertEqual(["state-publish", "db-add-event", "db-mark-events-read"], order)

    def test_local_dir_untouched_even_with_unpublished_edits(self):
        (self.h.local_dir / "memory" / "worker.md").write_text("# Worker memory\n\n- entry one\n- unpublished local entry\n")
        (self.h.local_dir / "skills" / "new").mkdir()
        (self.h.local_dir / "skills" / "new" / "SKILL.md").write_text("# unpublished skill\n")
        (self.h.local_dir / "scripts" / "blob.bin").unlink()
        before = tree_digest(self.h.local_dir)
        self.seed()
        code, report, stderr = self.h.run()
        self.assertEqual(0, code, (report, stderr))
        self.assertEqual([], self.h.marker_rows())
        self.assertEqual(before, tree_digest(self.h.local_dir))
        proposed = self.h.state["lastProposed"]
        self.assertEqual({f["path"] for f in BASE_FILES}, set(proposed))
        self.assertEqual("# Worker memory\n\n- entry one\n", base64.b64decode(proposed["memory/worker.md"]["bytes"]).decode())

    def test_base64_and_executable_files_materialize_exactly(self):
        self.seed()
        code, report, stderr = self.h.run()
        self.assertEqual(0, code, (report, stderr))
        proposed = self.h.state["lastProposed"]
        self.assertEqual(bytes(range(256)), base64.b64decode(proposed["scripts/blob.bin"]["bytes"]))
        self.assertFalse(proposed["scripts/blob.bin"]["executable"])
        self.assertEqual(b"\x00\x01binary-exec\xff", base64.b64decode(proposed["scripts/tool.bin"]["bytes"]))
        self.assertTrue(proposed["scripts/tool.bin"]["executable"])
        self.assertTrue(proposed["scripts/setup-run.sh"]["executable"])
        self.assertFalse(proposed["memory/worker.md"]["executable"])

    # ── authorization guards ─────────────────────────────────────────────

    def test_row_completed_after_event_is_ambiguous(self):
        self.seed(activity("act-1", EVENT_AT - timedelta(minutes=5)), activity("act-2", EVENT_AT + timedelta(seconds=1)))
        code, report, stderr = self.h.run()
        self.assertEqual(1, code)
        self.assertEqual([], report["released"])
        self.assertIn("act-2 finished after the recovery event", report["ambiguous"][0]["reason"])
        self.assertEqual([], self.h.calls("state-publish") + self.h.calls("db-add-event"))
        self.assertFalse(self.h.event("rec-1")["read"])
        self.assertEqual(["act-1", "act-2"], self.h.marker_rows())

    def test_row_completed_long_before_event_is_ambiguous(self):
        self.seed(activity("act-1", EVENT_AT - timedelta(minutes=30, seconds=1)))
        code, report, stderr = self.h.run()
        self.assertEqual(1, code)
        self.assertIn("more than 30 minutes before", report["ambiguous"][0]["reason"])
        self.assertEqual([], self.h.calls("state-publish"))
        self.assertFalse(self.h.event("rec-1")["read"])

    def test_row_exactly_thirty_minutes_before_event_is_released(self):
        self.seed(activity("act-1", EVENT_AT - timedelta(minutes=30)))
        code, report, stderr = self.h.run()
        self.assertEqual(0, code, (report, stderr))
        self.assertEqual(1, len(report["released"]))

    def test_row_without_completion_time_is_ambiguous(self):
        row = activity("act-1", EVENT_AT - timedelta(minutes=5))
        del row["completedAt"]
        self.seed(row)
        code, report, stderr = self.h.run()
        self.assertEqual(1, code, (report, stderr))
        self.assertIn("no completion time", report["ambiguous"][0]["reason"])
        self.assertEqual([], self.h.calls("state-publish"))

    def test_row_with_unreadable_completion_time_is_ambiguous_not_a_crash(self):
        row = activity("act-1", EVENT_AT - timedelta(minutes=5))
        row["completedAt"] = "yesterday-ish"
        self.seed(row)
        code, report, stderr = self.h.run()
        self.assertEqual(1, code, (report, stderr))
        self.assertIn("unreadable timestamp", report["ambiguous"][0]["reason"])
        self.assertEqual([], self.h.calls("state-publish"))

    def test_only_newest_recovery_event_for_a_computer_authorizes(self):
        old = recovery_event("rec-old", at=EVENT_AT - timedelta(hours=3), read=True)
        self.h.state["events"] = [old, recovery_event("rec-new")]
        self.h.state["activities"] = [activity("act-old", EVENT_AT - timedelta(hours=3, minutes=5))]
        code, report, stderr = self.h.run()
        self.assertEqual(1, code)
        self.assertEqual("rec-new", report["ambiguous"][0]["recoveryEvent"])
        self.assertEqual([], self.h.calls("state-publish"))

    def test_marker_rows_that_appear_while_authorizing_abort_the_publish(self):
        # U5 (Computer reuse): the server clears every terminal marker of the
        # Computer at publish time, so the authorized set must still be the
        # whole set right before publishing.
        self.seed(activity("act-old", EVENT_AT - timedelta(minutes=5)))
        self.h.state["newRowAfterListCalls"] = 3
        self.h.state["newRow"] = activity("act-new", EVENT_AT + timedelta(minutes=1))
        code, report, stderr = self.h.run()
        self.assertEqual(1, code, (report, stderr))
        self.assertEqual([], self.h.calls("state-publish"))
        self.assertEqual([], report["released"])
        self.assertIn("changed while authorizing", report["stillBlocked"][0]["reason"])
        self.assertEqual(["act-new", "act-old"], self.h.marker_rows())
        self.assertFalse(self.h.event("rec-1")["read"])
        self.assertEqual([], self.h.scratch_dirs())

    def test_row_that_finishes_during_the_publish_is_reported_not_hidden(self):
        # U5 (Computer reuse): nothing client-side can stop the server from
        # clearing a marker that appears mid-publish, so the release must say
        # so and exit 1 instead of reporting a clean success.
        self.seed(activity("act-old", EVENT_AT - timedelta(minutes=5)))
        self.h.state["newRowOnPublish"] = activity("act-new", EVENT_AT + timedelta(minutes=1))
        code, report, stderr = self.h.run()
        self.assertEqual(1, code, (report, stderr))
        released = report["released"][0]
        self.assertEqual(["act-old"], released["activities"])
        self.assertEqual(["act-new"], released["clearedUnverified"])
        self.assertTrue(released["notified"])
        self.assertEqual([], self.h.marker_rows())
        self.assertEqual(1, len(self.h.notices()))

    # ── ownership of the local snapshot (U1) ─────────────────────────────

    def test_snapshot_owned_by_another_workstream_is_refused(self):
        (self.h.local_dir / "workstream.json").write_text(json.dumps({"workstreamId": "0f973529-90f7-40bf-99e8-59f84dd968b2", "slug": SLUG}))
        self.seed()
        code, report, stderr = self.h.run()
        self.assertEqual(1, code, (report, stderr))
        self.assertEqual([], self.h.calls("state-publish"))
        self.assertIn("slug reuse", report["stillBlocked"][0]["reason"])
        self.assertIn("0f973529-90f7-40bf-99e8-59f84dd968b2", report["stillBlocked"][0]["reason"])
        self.assertEqual(["act-1"], self.h.marker_rows())
        self.assertEqual([], self.h.scratch_dirs())

    def test_snapshot_without_ownership_stamp_is_refused(self):
        (self.h.local_dir / "workstream.json").unlink()
        self.seed()
        code, report, stderr = self.h.run()
        self.assertEqual(1, code, (report, stderr))
        self.assertEqual([], self.h.calls("state-publish"))
        self.assertIn("ownership stamp", report["stillBlocked"][0]["reason"])

    def test_scratch_carries_the_real_ownership_stamp_verbatim(self):
        stamp = {"workstreamId": WS_ID, "slug": SLUG, "extra": "kept"}
        (self.h.local_dir / "workstream.json").write_text(json.dumps(stamp))
        self.seed()
        code, report, stderr = self.h.run()
        self.assertEqual(0, code, (report, stderr))
        self.assertEqual(stamp, self.h.state["lastOwnership"])

    # ── publish outcomes ─────────────────────────────────────────────────

    def test_failed_publish_leaves_event_unread_and_reports_still_blocked(self):
        self.h.state["publishFails"] = True
        self.seed()
        code, report, stderr = self.h.run()
        self.assertEqual(1, code)
        self.assertEqual([], report["released"])
        self.assertIn("merge base is unavailable", report["stillBlocked"][0]["reason"])
        self.assertEqual([], self.h.calls("db-add-event"))
        self.assertFalse(self.h.event("rec-1")["read"])
        self.assertEqual(["act-1"], self.h.marker_rows())
        self.assertEqual([], self.h.scratch_dirs())

    def test_publish_that_fails_after_the_server_committed_is_a_release(self):
        # U4: the marker state decides, not the CLI's exit code.
        self.h.state["publishFailsAfterCommit"] = True
        self.seed()
        code, report, stderr = self.h.run()
        self.assertEqual(0, code, (report, stderr))
        self.assertEqual([], self.h.marker_rows())
        released = report["released"][0]
        self.assertTrue(released["notified"])
        self.assertIsNone(released["generation"])
        self.assertIn("after the server committed", released["publishError"])
        self.assertEqual(1, len(self.h.notices()))
        self.assertNotIn("generation", self.h.notices()[0]["detail"].split("Reference:")[1])
        self.assertTrue(self.h.event("rec-1")["read"])

    def test_publish_that_clears_nothing_is_still_blocked_and_not_announced(self):
        self.h.state["publishClearsNothing"] = True
        self.seed()
        code, report, stderr = self.h.run()
        self.assertEqual(1, code)
        self.assertEqual(1, len(self.h.calls("state-publish")))
        self.assertEqual([], report["released"])
        self.assertIn("marker rows remain", report["stillBlocked"][0]["reason"])
        self.assertEqual([], self.h.calls("db-add-event"))
        self.assertFalse(self.h.event("rec-1")["read"])

    def test_missing_api_key_fails_closed_before_publishing(self):
        self.seed()
        code, report, stderr = self.h.run(api_key=None)
        self.assertEqual(1, code)
        self.assertIn("FACTORY_API_KEY", report["stillBlocked"][0]["reason"])
        self.assertEqual([], self.h.calls("state-publish"))

    # ── local marker failures stay inside the JSON protocol (U6) ─────────

    def test_missing_marker_is_reported_in_the_json_line(self):
        (self.h.local_dir / ".portable-state.json").unlink()
        self.seed()
        code, report, stderr = self.h.run()
        self.assertEqual(1, code)
        self.assertNotIn("Traceback", stderr)
        reason = report["stillBlocked"][0]["reason"]
        self.assertIn("local marker", reason)
        self.assertIn("is missing", reason)
        self.assertEqual([], self.h.calls("state-publish"))
        self.assertFalse(self.h.event("rec-1")["read"])

    def test_malformed_marker_is_reported_in_the_json_line(self):
        (self.h.local_dir / ".portable-state.json").write_text("{not json")
        self.seed()
        code, report, stderr = self.h.run()
        self.assertEqual(1, code)
        self.assertNotIn("Traceback", stderr)
        self.assertIn("unreadable", report["stillBlocked"][0]["reason"])
        self.assertEqual([], self.h.calls("state-publish"))

    def test_marker_without_base_snapshot_is_refused_before_publishing(self):
        (self.h.local_dir / ".portable-state.json").write_text(json.dumps({"generation": 63}))
        self.seed()
        code, report, stderr = self.h.run()
        self.assertEqual(1, code)
        self.assertIn("no base snapshot", report["stillBlocked"][0]["reason"])
        self.assertEqual([], self.h.calls("state-publish"))

    def test_malformed_base_entry_is_refused_before_publishing(self):
        marker = dict(self.h.marker, baseFiles=BASE_FILES + [{"path": "scripts/x.bin", "content": "not*base64", "encoding": "base64"}])
        (self.h.local_dir / ".portable-state.json").write_text(json.dumps(marker))
        self.seed()
        code, report, stderr = self.h.run()
        self.assertEqual(1, code)
        self.assertNotIn("Traceback", stderr)
        self.assertIn("malformed base file entry", report["stillBlocked"][0]["reason"])
        self.assertEqual([], self.h.calls("state-publish"))
        self.assertEqual([], self.h.scratch_dirs())

    def test_unsafe_base_path_is_refused_before_publishing(self):
        marker = dict(self.h.marker, baseFiles=BASE_FILES + [{"path": "scripts/../../escape.txt", "content": "x", "encoding": "utf8", "executable": False}])
        (self.h.local_dir / ".portable-state.json").write_text(json.dumps(marker))
        self.seed()
        code, report, stderr = self.h.run()
        self.assertEqual(1, code)
        self.assertIn("refusing base file path", report["stillBlocked"][0]["reason"])
        self.assertEqual([], self.h.calls("state-publish"))
        self.assertFalse((self.h.home / ".factory" / "escape.txt").exists())
        self.assertEqual([], self.h.scratch_dirs())

    def test_platform_read_failure_is_an_error_not_a_quiet_run(self):
        self.h.state["workstream"]["executionTemplateId"] = None
        code, report, stderr = self.h.run()
        self.assertEqual(1, code)
        self.assertIn("template Computers", report["error"])

    def test_unexpected_exception_still_prints_the_json_line(self):
        self.h.state["workstream"] = "not-an-object"
        code, report, stderr = self.h.run()
        self.assertEqual(1, code)
        self.assertNotIn("Traceback", stderr)
        self.assertIn("error", report)

    # ── accounting for every marker (U2, U3) ─────────────────────────────

    def test_no_recovery_event_means_no_release_and_a_pending_entry(self):
        self.h.state["events"] = [plain_event("evt-x", EVENT_AT)]
        self.h.state["activities"] = [activity("act-1", EVENT_AT - timedelta(minutes=5))]
        code, report, stderr = self.h.run()
        self.assertEqual(1, code, (report, stderr))
        self.assertEqual([], report["released"] + report["ambiguous"] + report["stillBlocked"])
        self.assertEqual([{"computer": COMPUTER, "activities": ["act-1"], "reason": "no recovery event yet; the platform may still be saving that run"}], report["pending"])
        self.assertEqual([], self.h.calls("state-publish") + self.h.calls("db-add-event") + self.h.calls("db-mark-events-read"))
        self.assertEqual(["act-1"], self.h.marker_rows())

    def test_release_with_another_computer_still_marked_is_not_a_clear_queue(self):
        self.seed(activity("act-1", EVENT_AT - timedelta(minutes=5)), activity("act-other", EVENT_AT, computer=OTHER_COMPUTER))
        code, report, stderr = self.h.run()
        self.assertEqual(1, code, (report, stderr))
        self.assertEqual(1, len(report["released"]))
        self.assertEqual(OTHER_COMPUTER, report["pending"][0]["computer"])
        self.assertEqual(["act-other"], self.h.marker_rows())
        self.assertNotIn("queued work runs again", self.h.notices()[0]["detail"])

    def test_recovery_event_beyond_the_event_page_is_an_incomplete_read(self):
        self.seed()
        self.h.state["events"] += [plain_event(f"filler-{i}", EVENT_AT + timedelta(seconds=i + 1)) for i in range(500)]
        code, report, stderr = self.h.run()
        self.assertEqual(1, code, (report, stderr))
        self.assertEqual([], report["released"] + report["pending"])
        self.assertIn("newest 500 events", report["stillBlocked"][0]["reason"])
        self.assertEqual(["act-1"], report["stillBlocked"][0]["activities"])
        self.assertEqual([], self.h.calls("state-publish"))
        self.assertEqual(["500"], [c["args"][-1] for c in self.h.calls("db-list-events")])
        self.assertEqual(["act-1"], self.h.marker_rows())

    def test_full_event_page_with_the_recovery_event_inside_still_releases(self):
        self.seed()
        self.h.state["events"] += [plain_event(f"filler-{i}", EVENT_AT - timedelta(hours=1, seconds=i)) for i in range(499)]
        code, report, stderr = self.h.run()
        self.assertEqual(0, code, (report, stderr))
        self.assertEqual(1, len(report["released"]))

    def test_activity_reads_mirror_the_backend_halt_window(self):
        # U3: the store caps lists at 500 and the coordinator only looks at
        # the newest 64 rows per terminal status, so that is all that is read.
        self.seed(*[activity(f"old-{i}", EVENT_AT - timedelta(days=1, minutes=i), marker=False) for i in range(399)], activity("act-1", EVENT_AT - timedelta(minutes=5)))
        code, report, stderr = self.h.run()
        self.assertEqual(0, code, (report, stderr))
        self.assertEqual(1, len(report["released"]))
        self.assertEqual({"64"}, {c["args"][-1] for c in self.h.calls("db-list-activities")})

    def test_marker_outside_the_halt_window_does_not_halt_and_is_ignored(self):
        self.seed(*[activity(f"new-{i}", EVENT_AT + timedelta(minutes=i + 1), marker=False) for i in range(64)], activity("act-buried", EVENT_AT - timedelta(minutes=5)))
        code, report, stderr = self.h.run()
        self.assertEqual(0, code, (report, stderr))
        self.assertEqual([], self.h.calls("state-publish"))
        self.assertEqual(["act-buried"], self.h.marker_rows())
        self.assertEqual(1, len(report["released"]), "the unread recovery event with no visible rows gets its notice")

    # ── notices (U4) ─────────────────────────────────────────────────────

    def test_cleared_marker_with_unread_event_gets_announced_once(self):
        self.h.state["events"] = [recovery_event()]
        code, report, stderr = self.h.run()
        self.assertEqual(0, code, (report, stderr))
        self.assertEqual([], self.h.calls("state-publish"))
        self.assertEqual(1, len(self.h.notices()))
        self.assertIn("recovery event rec-1", self.h.notices()[0]["detail"])
        self.assertTrue(self.h.event("rec-1")["read"])
        self.assertEqual([], report["released"][0]["activities"])
        code, report, stderr = self.h.run()
        self.assertEqual(0, code)
        self.assertEqual(EMPTY, report)
        self.assertEqual(1, len(self.h.notices()))

    def test_cleared_marker_with_read_event_and_no_notice_is_still_announced(self):
        self.h.state["events"] = [recovery_event(read=True)]
        code, report, stderr = self.h.run()
        self.assertEqual(0, code, (report, stderr))
        self.assertEqual(1, len(self.h.notices()))
        self.assertEqual([], self.h.calls("db-mark-events-read"))
        code, report, stderr = self.h.run()
        self.assertEqual(EMPTY, report)
        self.assertEqual(1, len(self.h.notices()))

    def test_failed_notice_for_a_read_event_is_retried_next_run(self):
        self.h.state["addEventFails"] = True
        self.seed(read=True)
        code, report, stderr = self.h.run()
        self.assertEqual(1, code)
        self.assertEqual([], self.h.marker_rows())
        self.assertFalse(report["released"][0]["notified"])
        self.h.state["addEventFails"] = False
        code, report, stderr = self.h.run()
        self.assertEqual(0, code, (report, stderr))
        self.assertEqual(1, len(self.h.notices()))

    def test_existing_notice_is_not_duplicated_but_event_is_marked_read(self):
        self.h.state["events"] = [recovery_event(), notice_event("evt-info", EVENT_AT + timedelta(minutes=4))]
        code, report, stderr = self.h.run()
        self.assertEqual(0, code)
        self.assertEqual([], self.h.calls("db-add-event"))
        self.assertTrue(self.h.event("rec-1")["read"])

    def test_notice_identity_needs_the_title_and_the_exact_event_id(self):
        self.h.state["events"] = [
            recovery_event(),
            plain_event("mention", EVENT_AT + timedelta(minutes=1), title="Recovery investigation", detail="Still investigating recovery event rec-1"),
            notice_event("other-notice", EVENT_AT + timedelta(minutes=2), recovery_id="rec-10"),
        ]
        code, report, stderr = self.h.run()
        self.assertEqual(0, code, (report, stderr))
        self.assertEqual(1, len(self.h.calls("db-add-event")))
        self.assertEqual(2, len(self.h.notices()))
        self.assertTrue(self.h.event("rec-1")["read"])

    def test_concurrent_duplicate_notices_converge_to_the_oldest(self):
        self.h.state["addEventDuplicates"] = True
        self.seed()
        code, report, stderr = self.h.run()
        self.assertEqual(0, code, (report, stderr))
        notices = self.h.notices()
        self.assertEqual(1, len(notices))
        self.assertEqual("evt-2", notices[0]["id"])
        self.assertEqual(["evt-3"], [c["args"][0] for c in self.h.calls("db-delete-event")])
        self.assertTrue(self.h.event("rec-1")["read"])

    def test_preexisting_duplicate_notices_are_reduced_to_one(self):
        self.h.state["events"] = [
            recovery_event(read=True),
            notice_event("n-newer", EVENT_AT + timedelta(minutes=5)),
            notice_event("n-older", EVENT_AT + timedelta(minutes=4)),
            notice_event("n-other", EVENT_AT + timedelta(minutes=6), recovery_id="rec-2", computer=OTHER_COMPUTER),
        ]
        code, report, stderr = self.h.run()
        self.assertEqual(0, code, (report, stderr))
        self.assertEqual([], self.h.calls("db-add-event"))
        self.assertEqual({"n-older", "n-other"}, {n["id"] for n in self.h.notices()})

    def test_failed_notice_for_a_cleared_marker_is_recorded_not_fatal(self):
        self.h.state["addEventFails"] = True
        self.h.state["events"] = [recovery_event(), recovery_event("rec-2", computer=OTHER_COMPUTER, at=EVENT_AT + timedelta(minutes=1))]
        self.h.state["activities"] = [activity("act-2", EVENT_AT - timedelta(minutes=4), computer=OTHER_COMPUTER)]
        code, report, stderr = self.h.run()
        self.assertEqual(1, code, (report, stderr))
        self.assertNotIn("error", report)
        self.assertEqual({COMPUTER: False, OTHER_COMPUTER: False}, {r["computer"]: r["notified"] for r in report["released"]})
        self.assertEqual([], self.h.marker_rows(), "the second Computer was still released")
        self.assertFalse(self.h.event("rec-1")["read"])
        self.assertFalse(self.h.event("rec-2")["read"])

    def test_failed_announce_keeps_event_unread_and_exits_one(self):
        self.h.state["addEventFails"] = True
        self.seed()
        code, report, stderr = self.h.run()
        self.assertEqual(1, code)
        self.assertEqual([], self.h.marker_rows())
        self.assertFalse(report["released"][0]["notified"])
        self.assertFalse(self.h.event("rec-1")["read"])
        self.assertEqual([], self.h.calls("db-mark-events-read"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
