"""Book-keeping for MiniAOD recovery tasks (tmc.py recover / adopt), shared with ntuple.py.

A MiniAOD task whose last jobs hang is killed, and its unfinished jobs are rerun as a recovery
task, batch r<k>.  Recovery job i stands in for original job N: run_chain.sh reads recover.txt
("<i> <N> <events per job>") and gives the job lumi N and events (N-1)*E+1 ... N*E, exactly as
CRAB numbers original job N (firstLuminosityBlock and firstEvent are untracked, so the PSetIDs
don't change).  `tmc.py adopt` checks each finished recovery file and moves it to the original
task directory as MiniAOD_<N>.root, so every sample keeps one directory with MiniAOD_1..n.

Ledger: work/<sample>/recovery.json
    {"sample": ..., "killed": <time the original task was killed>,
     "batches": [{"k", "request", "map": {"<i>": N}, "events_per_job", "runtime_min",
                  "state": "submitted" | "done" (all adopted), "time"}],
     "adopted": {"<N>": {"batch", "job", "from", "to", "events", "time"}}}
"""
import datetime
import json
import os
import re

ACTIVE_STATES = {"unsubmitted", "idle", "running", "transferring", "cooloff", "toRetry", "held"}


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def ledger_path(work, name):
    return os.path.join(work, name, "recovery.json")


def load(work, name):
    try:
        with open(ledger_path(work, name)) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"sample": name, "killed": None, "batches": [], "adopted": {}}


def save(work, name, ledger):
    path = ledger_path(work, name)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(ledger, f, indent=1)
    os.replace(tmp, path)


def adopted_jobs(work, name):
    """Original job numbers whose MiniAOD came from a recovery task (now in the original dir)."""
    return {int(n) for n in load(work, name)["adopted"]}


def pending_jobs(ledger):
    """Original jobs assigned to a recovery batch and not adopted yet."""
    return {int(n) for b in ledger["batches"] if b["state"] == "submitted"
            for n in b["map"].values()} - {int(n) for n in ledger["adopted"]}


def expected_events(job, events_per_job):
    """Event numbers CRAB gives original job `job` of an EventBased PrivateMC task."""
    first = (job - 1) * events_per_job + 1
    return first, first + events_per_job - 1


def parse_edmfileutil_events(text):
    """[(run, lumi, event)] of the events in `edmFileUtil -e` output (Run/Lumi rows excluded)."""
    out = []
    for line in text.splitlines():
        m = re.match(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+\d+\s*$", line)
        if m and int(m.group(3)) != 0:
            out.append(tuple(int(x) for x in m.groups()))
    return out


def check_numbering(events, job, events_per_job):
    """Problems with a file meant to be original job `job`: run 1, only lumi `job`, event
    numbers inside the job's range, no duplicates.  [] means it is good."""
    if not events:
        return ["no events"]
    problems = []
    runs = {r for r, l, e in events}
    lumis = {l for r, l, e in events}
    if runs != {1}:
        problems.append(f"runs {sorted(runs)}, expected [1]")
    if lumis != {job}:
        problems.append(f"lumis {sorted(lumis)[:5]}, expected [{job}]")
    lo, hi = expected_events(job, events_per_job)
    nums = [e for r, l, e in events]
    outside = [e for e in nums if not lo <= e <= hi]
    if outside:
        problems.append(f"{len(outside)} events outside {lo}-{hi}, e.g. {outside[0]}")
    if len(set(nums)) != len(nums):
        problems.append("duplicate event numbers")
    return problems
