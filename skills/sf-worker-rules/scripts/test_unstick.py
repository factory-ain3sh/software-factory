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
EVENT_AT = datetime(2026, 10, 7, 12, 43, 59, tzinfo=timezone.utc)
RECOVERY_TITLE = "Worker state publication needs recovery"

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

def terminal_marker_rows(computer):
    return [r for r in state["activities"] if r["status"] in ("completed", "failed", "canceled") and r.get("workerId") == f"template-computer:{computer}"]

if cmd == "db-get-workstream":
    done({"ok": True, "workstream": state["workstream"]})
if cmd == "db-list-events":
    assert opt("--workstream") == state["workstream"]["id"]
    limit = int(opt("--limit", "100"))
    events = sorted(state["events"], key=lambda e: e["createdAt"], reverse=True)
    done({"ok": True, "events": events[:limit]})
if cmd == "db-list-activities":
    assert opt("--workstream") == state["workstream"]["id"]
    limit = int(opt("--limit", "100"))
    status = opt("--status")
    rows = [r for r in state["activities"] if status is None or r["status"] == status]
    done({"ok": True, "activities": rows[:limit]})
if cmd == "db-add-event":
    if state.get("addEventFails"):
        fail("fake: add-event refused")
    event = {"id": f"evt-{len(state['events']) + 1}", "workstreamId": opt("--workstream"), "stage": opt("--stage"), "severity": opt("--severity", "info"), "title": opt("--title"), "detail": opt("--detail"), "read": False, "createdAt": "2026-10-07T13:00:00.000Z"}
    state["events"].append(event)
    done({"ok": True, "event": event})
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
    if not state.get("publishClearsNothing"):
        for r in terminal_marker_rows(computer):
            r["workerId"] = None
    state.setdefault("publishes", []).append({"computer": computer, "generation": state["generation"]})
    (wdir / ".portable-state.json").write_text(json.dumps({"generation": state["generation"], "baseFiles": marker["baseFiles"], "snapshotToken": state["snapshotToken"]}))
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


def activity(activity_id, completed_at, computer=COMPUTER, status="completed", title="Get the tool output bounds PR past its failing check"):
    return {
        "id": activity_id,
        "workstreamId": WS_ID,
        "kind": "steward",
        "status": status,
        "workerId": f"template-computer:{computer}",
        "completedAt": iso(completed_at),
        "changeTitle": title,
    }


BASE_FILES = [
    {"path": "memory/worker.md", "content": "# Worker memory\n\n- entry one\n", "encoding": "utf8", "executable": False},
    {"path": "scripts/setup-run.sh", "content": "#!/usr/bin/env bash\necho hi\n", "encoding": "utf8", "executable": True},
    {"path": "scripts/blob.bin", "content": base64.b64encode(bytes(range(256))).decode(), "encoding": "base64", "executable": False},
    {"path": "scripts/tool.bin", "content": base64.b64encode(b"\x00\x01binary-exec\xff").decode(), "encoding": "base64", "executable": True},
    {"path": "skills/health/SKILL.md", "content": "# Health\n", "encoding": "utf8", "executable": False},
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
        self.marker = {"generation": 63, "baseFiles": BASE_FILES, "snapshotToken": "tok-63"}
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
            "snapshotToken": "tok-63",
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

    def marker_rows(self):
        return [a["id"] for a in self.state["activities"] if a.get("workerId")]

    def calls(self, cmd=None):
        return [c for c in self.state.get("calls", []) if cmd is None or c["cmd"] == cmd]

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


class UnstickTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.addCleanup(self.h.cleanup)
        self.addCleanup(self.assert_no_pycache)

    def assert_no_pycache(self):
        self.assertEqual([], list(HERE.rglob("__pycache__")))

    def test_release_clears_marker_announces_then_marks_read(self):
        self.h.state["events"] = [recovery_event()]
        self.h.state["activities"] = [
            activity("act-1", EVENT_AT - timedelta(minutes=5)),
            activity("act-2", EVENT_AT - timedelta(minutes=12), status="failed", title="Other change"),
            activity("act-3", EVENT_AT - timedelta(minutes=5), computer="other-computer-0000-0000-000000000000"),
        ]
        code, report, stderr = self.h.run()
        self.assertEqual(0, code, stderr)
        self.assertEqual(1, len(report["released"]))
        released = report["released"][0]
        self.assertEqual(COMPUTER, released["computer"])
        self.assertEqual({"act-1", "act-2"}, set(released["activities"]))
        self.assertEqual(84, released["generation"])
        self.assertTrue(released["notified"])
        self.assertEqual([], report["ambiguous"])
        self.assertEqual([], report["stillBlocked"])
        self.assertEqual(["act-3"], self.h.marker_rows())
        publishes = self.h.state["publishes"]
        self.assertEqual([{"computer": COMPUTER, "generation": 84}], publishes)
        publish_call = self.h.calls("state-publish")[0]
        self.assertEqual(COMPUTER, publish_call["machine"])
        self.assertTrue(publish_call["home_override"].startswith(str(self.h.home / ".factory" / "state")), publish_call)
        self.assertFalse(Path(publish_call["home_override"]).exists(), "scratch home must be removed")
        info = [e for e in self.h.state["events"] if e["title"] == "Work queue restarted after a finished run could not be saved"]
        self.assertEqual(1, len(info))
        detail = info[0]["detail"]
        self.assertIn("since 2026-10-07 12:31 UTC", detail)
        self.assertIn("Get the tool output bounds PR past its failing check", detail)
        self.assertIn("Other change", detail)
        self.assertIn("may be lost", detail)
        for ref in (f"Computer {COMPUTER}", "activity act-1", "activity act-2", "recovery event rec-1", "generation 84", "SOF-391"):
            self.assertIn(ref, detail)
        self.assertEqual("info", info[0]["severity"])
        self.assertTrue(self.h.event("rec-1")["read"])
        order = [c["cmd"] for c in self.h.calls() if c["cmd"] in ("state-publish", "db-add-event", "db-mark-events-read")]
        self.assertEqual(["state-publish", "db-add-event", "db-mark-events-read"], order)

    def test_local_dir_untouched_even_with_unpublished_edits(self):
        (self.h.local_dir / "memory" / "worker.md").write_text("# Worker memory\n\n- entry one\n- unpublished local entry\n")
        (self.h.local_dir / "skills" / "new" ).mkdir()
        (self.h.local_dir / "skills" / "new" / "SKILL.md").write_text("# unpublished skill\n")
        (self.h.local_dir / "scripts" / "blob.bin").unlink()
        before = tree_digest(self.h.local_dir)
        self.h.state["events"] = [recovery_event()]
        self.h.state["activities"] = [activity("act-1", EVENT_AT - timedelta(minutes=5))]
        code, report, stderr = self.h.run()
        self.assertEqual(0, code, (report, stderr))
        self.assertEqual([], self.h.marker_rows())
        self.assertEqual(before, tree_digest(self.h.local_dir))
        proposed = self.h.state["lastProposed"]
        self.assertEqual({f["path"] for f in BASE_FILES}, set(proposed))
        self.assertEqual("# Worker memory\n\n- entry one\n", base64.b64decode(proposed["memory/worker.md"]["bytes"]).decode())

    def test_base64_and_executable_files_materialize_exactly(self):
        self.h.state["events"] = [recovery_event()]
        self.h.state["activities"] = [activity("act-1", EVENT_AT - timedelta(minutes=1))]
        code, report, stderr = self.h.run()
        self.assertEqual(0, code, (report, stderr))
        proposed = self.h.state["lastProposed"]
        self.assertEqual(bytes(range(256)), base64.b64decode(proposed["scripts/blob.bin"]["bytes"]))
        self.assertFalse(proposed["scripts/blob.bin"]["executable"])
        self.assertEqual(b"\x00\x01binary-exec\xff", base64.b64decode(proposed["scripts/tool.bin"]["bytes"]))
        self.assertTrue(proposed["scripts/tool.bin"]["executable"])
        self.assertTrue(proposed["scripts/setup-run.sh"]["executable"])
        self.assertFalse(proposed["memory/worker.md"]["executable"])

    def test_row_completed_after_event_is_ambiguous(self):
        self.h.state["events"] = [recovery_event()]
        self.h.state["activities"] = [
            activity("act-1", EVENT_AT - timedelta(minutes=5)),
            activity("act-2", EVENT_AT + timedelta(seconds=1)),
        ]
        code, report, stderr = self.h.run()
        self.assertEqual(1, code)
        self.assertEqual([], report["released"])
        self.assertEqual(1, len(report["ambiguous"]))
        self.assertIn("act-2 finished after the recovery event", report["ambiguous"][0]["reason"])
        self.assertEqual([], self.h.calls("state-publish"))
        self.assertEqual([], self.h.calls("db-add-event"))
        self.assertFalse(self.h.event("rec-1")["read"])
        self.assertEqual(["act-1", "act-2"], self.h.marker_rows())

    def test_row_completed_long_before_event_is_ambiguous(self):
        self.h.state["events"] = [recovery_event()]
        self.h.state["activities"] = [activity("act-1", EVENT_AT - timedelta(minutes=30, seconds=1))]
        code, report, stderr = self.h.run()
        self.assertEqual(1, code)
        self.assertEqual([], report["released"])
        self.assertIn("more than 30 minutes before", report["ambiguous"][0]["reason"])
        self.assertEqual([], self.h.calls("state-publish"))
        self.assertFalse(self.h.event("rec-1")["read"])

    def test_row_exactly_thirty_minutes_before_event_is_released(self):
        self.h.state["events"] = [recovery_event()]
        self.h.state["activities"] = [activity("act-1", EVENT_AT - timedelta(minutes=30))]
        code, report, stderr = self.h.run()
        self.assertEqual(0, code, (report, stderr))
        self.assertEqual(1, len(report["released"]))

    def test_only_newest_recovery_event_for_a_computer_authorizes(self):
        old = recovery_event("rec-old", at=EVENT_AT - timedelta(hours=3), read=True)
        self.h.state["events"] = [old, recovery_event("rec-new")]
        self.h.state["activities"] = [activity("act-old", EVENT_AT - timedelta(hours=3, minutes=5))]
        code, report, stderr = self.h.run()
        self.assertEqual(1, code)
        self.assertEqual("rec-new", report["ambiguous"][0]["recoveryEvent"])
        self.assertEqual([], self.h.calls("state-publish"))

    def test_failed_publish_leaves_event_unread_and_reports_still_blocked(self):
        self.h.state["publishFails"] = True
        self.h.state["events"] = [recovery_event()]
        self.h.state["activities"] = [activity("act-1", EVENT_AT - timedelta(minutes=5))]
        code, report, stderr = self.h.run()
        self.assertEqual(1, code)
        self.assertEqual([], report["released"])
        self.assertEqual(1, len(report["stillBlocked"]))
        self.assertIn("merge base is unavailable", report["stillBlocked"][0]["reason"])
        self.assertEqual([], self.h.calls("db-add-event"))
        self.assertFalse(self.h.event("rec-1")["read"])
        self.assertEqual(["act-1"], self.h.marker_rows())
        self.assertEqual([], list((self.h.home / ".factory" / "state").glob("sf-unstick-*")))

    def test_missing_api_key_fails_closed_before_publishing(self):
        self.h.state["events"] = [recovery_event()]
        self.h.state["activities"] = [activity("act-1", EVENT_AT - timedelta(minutes=5))]
        code, report, stderr = self.h.run(api_key=None)
        self.assertEqual(1, code)
        self.assertIn("FACTORY_API_KEY", report["stillBlocked"][0]["reason"])
        self.assertEqual([], self.h.calls("state-publish"))
        self.assertEqual(["act-1"], self.h.marker_rows())

    def test_no_recovery_event_means_no_action(self):
        self.h.state["events"] = [{"id": "evt-x", "workstreamId": WS_ID, "severity": "info", "stage": "health", "title": "Health: all quiet", "detail": "", "read": False, "createdAt": iso(EVENT_AT)}]
        self.h.state["activities"] = [activity("act-1", EVENT_AT - timedelta(minutes=5))]
        code, report, stderr = self.h.run()
        self.assertEqual(0, code, (report, stderr))
        self.assertEqual({"released": [], "ambiguous": [], "stillBlocked": []}, report)
        self.assertEqual([], self.h.calls("state-publish"))
        self.assertEqual([], self.h.calls("db-add-event"))
        self.assertEqual([], self.h.calls("db-mark-events-read"))
        self.assertEqual(["act-1"], self.h.marker_rows())

    def test_cleared_marker_with_unread_event_gets_announced_once(self):
        self.h.state["events"] = [recovery_event()]
        code, report, stderr = self.h.run()
        self.assertEqual(0, code, (report, stderr))
        self.assertEqual([], self.h.calls("state-publish"))
        info = [e for e in self.h.state["events"] if e["title"] == "Work queue restarted after a finished run could not be saved"]
        self.assertEqual(1, len(info))
        self.assertIn("recovery event rec-1", info[0]["detail"])
        self.assertTrue(self.h.event("rec-1")["read"])
        self.assertEqual([], report["released"][0]["activities"])
        code, report, stderr = self.h.run()
        self.assertEqual(0, code)
        self.assertEqual({"released": [], "ambiguous": [], "stillBlocked": []}, report)
        self.assertEqual(1, len([e for e in self.h.state["events"] if e["title"] == "Work queue restarted after a finished run could not be saved"]))

    def test_cleared_marker_with_read_event_is_left_alone(self):
        self.h.state["events"] = [recovery_event(read=True)]
        code, report, stderr = self.h.run()
        self.assertEqual(0, code)
        self.assertEqual({"released": [], "ambiguous": [], "stillBlocked": []}, report)
        self.assertEqual([], self.h.calls("db-add-event"))

    def test_existing_notice_is_not_duplicated_but_event_is_marked_read(self):
        notice = {"id": "evt-info", "workstreamId": WS_ID, "severity": "info", "stage": "health", "title": "Work queue restarted after a finished run could not be saved", "detail": f"...\nReference: Computer {COMPUTER}, recovery event rec-1, SOF-391", "read": False, "createdAt": iso(EVENT_AT + timedelta(minutes=4))}
        self.h.state["events"] = [recovery_event(), notice]
        code, report, stderr = self.h.run()
        self.assertEqual(0, code)
        self.assertEqual([], self.h.calls("db-add-event"))
        self.assertTrue(self.h.event("rec-1")["read"])

    def test_failed_announce_keeps_event_unread_and_exits_one(self):
        self.h.state["addEventFails"] = True
        self.h.state["events"] = [recovery_event()]
        self.h.state["activities"] = [activity("act-1", EVENT_AT - timedelta(minutes=5))]
        code, report, stderr = self.h.run()
        self.assertEqual(1, code)
        self.assertEqual([], self.h.marker_rows())
        self.assertFalse(report["released"][0]["notified"])
        self.assertFalse(self.h.event("rec-1")["read"])
        self.assertEqual([], self.h.calls("db-mark-events-read"))

    def test_marker_rows_beyond_first_page_are_still_seen(self):
        self.h.state["events"] = [recovery_event()]
        filler = [{"id": f"old-{i}", "workstreamId": WS_ID, "kind": "steward", "status": "completed", "completedAt": iso(EVENT_AT - timedelta(days=1))} for i in range(200)]
        self.h.state["activities"] = filler + [activity("act-late", EVENT_AT + timedelta(minutes=1))]
        code, report, stderr = self.h.run()
        self.assertEqual(1, code)
        self.assertEqual(["act-late"], report["ambiguous"][0]["activities"])
        self.assertEqual([], self.h.calls("state-publish"))

    def test_row_without_completion_time_is_ambiguous(self):
        self.h.state["events"] = [recovery_event()]
        row = activity("act-1", EVENT_AT - timedelta(minutes=5))
        del row["completedAt"]
        self.h.state["activities"] = [row]
        code, report, stderr = self.h.run()
        self.assertEqual(1, code, (report, stderr))
        self.assertIn("no completion time", report["ambiguous"][0]["reason"])
        self.assertEqual([], self.h.calls("state-publish"))
        self.assertFalse(self.h.event("rec-1")["read"])

    def test_publish_that_clears_nothing_is_still_blocked_and_not_announced(self):
        self.h.state["publishClearsNothing"] = True
        self.h.state["events"] = [recovery_event()]
        self.h.state["activities"] = [activity("act-1", EVENT_AT - timedelta(minutes=5))]
        code, report, stderr = self.h.run()
        self.assertEqual(1, code)
        self.assertEqual(1, len(self.h.calls("state-publish")))
        self.assertEqual([], report["released"])
        self.assertIn("marker rows remain", report["stillBlocked"][0]["reason"])
        self.assertEqual([], self.h.calls("db-add-event"))
        self.assertFalse(self.h.event("rec-1")["read"])

    def test_marker_without_base_snapshot_is_refused_before_publishing(self):
        (self.h.local_dir / ".portable-state.json").write_text(json.dumps({"generation": 63}))
        self.h.state["events"] = [recovery_event()]
        self.h.state["activities"] = [activity("act-1", EVENT_AT - timedelta(minutes=5))]
        code, report, stderr = self.h.run()
        self.assertEqual(1, code)
        self.assertIn("no base snapshot", report["stillBlocked"][0]["reason"])
        self.assertEqual([], self.h.calls("state-publish"))
        self.assertEqual(["act-1"], self.h.marker_rows())

    def test_unsafe_base_path_is_refused_before_publishing(self):
        marker = dict(self.h.marker, baseFiles=BASE_FILES + [{"path": "scripts/../../escape.txt", "content": "x", "encoding": "utf8", "executable": False}])
        (self.h.local_dir / ".portable-state.json").write_text(json.dumps(marker))
        self.h.state["events"] = [recovery_event()]
        self.h.state["activities"] = [activity("act-1", EVENT_AT - timedelta(minutes=5))]
        code, report, stderr = self.h.run()
        self.assertEqual(1, code)
        self.assertIn("refusing base file path", report["stillBlocked"][0]["reason"])
        self.assertEqual([], self.h.calls("state-publish"))
        self.assertFalse((self.h.home / ".factory" / "escape.txt").exists())
        self.assertEqual([], list((self.h.home / ".factory" / "state").glob("sf-unstick-*")))

    def test_read_failure_is_an_error_not_a_quiet_run(self):
        self.h.state["workstream"]["executionTemplateId"] = None
        code, report, stderr = self.h.run()
        self.assertEqual(1, code)
        self.assertIn("template Computers", report["error"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
