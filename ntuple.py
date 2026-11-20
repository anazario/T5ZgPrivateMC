#!/usr/bin/env python3
"""KUCMSNtupleizer CRAB tasks over the MiniAOD made by tmc.py, submitted in batches.

Runs in the KUCMSNtupleizer area (cmsenv there, then source crab-setup.sh):

  ./ntuple.py status [samples]            MiniAOD files finished / submitted / still to submit,
                                          and the job states of every ntuple batch
  ./ntuple.py submit [--dryrun] [samples] submit samples whose MiniAOD task is complete, as a new
                                          batch task per sample (--partial: also incomplete ones)
  ./ntuple.py killed sample request       mark a batch killed with `crab kill` in the ledger
  ./ntuple.py proceed [samples]           submit batches left by submit --dryrun

Samples are glob patterns on the names of `./tmc.py list` (default: all).

Book-keeping
  * Whole samples only: submit skips a sample until every job of its MiniAOD task has finished,
    so each sample's ntuples normally come from one batch.  --partial overrides this.
  * Each sample has a ledger, work/<sample>/ntuple/ledger.json, listing every batch (CRAB request
    name, MiniAOD files, state "dryrun", "submitted" or "killed").  submit takes the MiniAOD
    task's finished jobs, removes files of non-killed batches and submits the rest as batch b<k>,
    so a file is never submitted twice and none is skipped.
  * Files come from CRAB's finished jobs, not from an EOS listing: EOS can hold files of jobs
    CRAB still counts as running.  Every file is checked to exist on EOS.
  * All batches of a sample write to the same dataset tag, in the collection layout that
    KUCMSSkimmer/condor/skim_submit.py expects (it takes the tier from the collection name and
    the sample from the task directory, <collection>_<sample>):
    <OUT_LFN_BASE>/kucmsntuple_SMS_T5Zg_SVHPM100_<FullMini|FastMini>_v38/<sample>/
        kucmsntuple_SMS_T5Zg_SVHPM100_<FullMini|FastMini>_v38_<sample>/<timestamp per batch>/

Sandbox
  The KUCMSNtupleizer area also holds HiggsAnalysis/CombinedLimit (313 MB in its data/),
  CombineHarvester and LLPCombine.  CRAB tars every src/**/{data,interface,python} and all of
  lib/ and python/, so the sandbox goes over CRAB's limit.  submit replaces CRAB's tar filter
  (CRABClient.JobType.UserTarball.excludeFromTar, which already drops .git) with one that
  also drops those packages; nothing in the area is moved or deleted.  After each submission
  the tarball is checked for size and for leftover files of those packages.
"""
import argparse
import datetime
import fnmatch
import glob
import json
import os
import subprocess
import sys
import tarfile

import production as P
import recovery

ROOT = os.path.dirname(os.path.realpath(__file__))
WORK = os.path.join(ROOT, "work")
EOS = "root://cmseos.fnal.gov/"

# --------------------------------------------------------------------------- settings
NTUPLE_VERSION = "v38"   # KUCMSNtupleizer version printed by the group's multicrab scripts
NTUPLE_TAG = f"KUCMSNtuple_SVHPM100_{NTUPLE_VERSION}"  # dataset tag and request-name stem
NTUPLE_LFN_BASE = P.OUT_LFN_BASE  # next to the MiniAOD (private area, no clash with group ntuples)
# Tasks submitted before Nov 19 2026 wrote <OUT_LFN_BASE>/<sample>/KUCMSNtuple_SVHPM100_v38_
# <Fast|Full>Mini_v1/<timestamp>/; their outputs were moved into the collections below.
PSET = "llpgana_miniaod.py"       # in $CMSSW_BASE/src/KUCMSNtupleizer/KUCMSNtupleizer/test
# local test (Nov 2026, 2 files per chain): 3-5 min and 1.3-1.6 GB per MiniAOD file
FILES_PER_JOB = 10
MAX_MEMORY_MB = 2500
# Inputs are on cmseos; the T2s read them over xrootd.  FNALLPC alone ran none of the b1 jobs in
# an hour (and none of ~6400 MiniAOD jobs that listed it), so the US T2s whose stage-out to
# FNAL works are added (Nov 2026).
WHITELIST = P.SITES_US

COMMON = ["multicrab=True", "hasGenInfo=True", "eventFilter=SVHPMet100"]
ERA_PARAMS = {
    "2018": ["globalTag=106X_upgrade2018_realistic_v11_L1v1", "runera=Run2_2018"],
    "2022": ["globalTag=124X_mcRun3_2022_realistic_v12", "runera=Run3_2022"],
}
FAST_PARAMS = ["doFastSim=True"]

# packages of the area that the ntupleizer does not use
SANDBOX_DROP = ("HiggsAnalysis", "CombineHarvester", "LLPCombine")
SANDBOX_KEEP_SRC = ("KUCMSNtupleizer",)
SANDBOX_MAX_MB = 50


def collection(chain):
    """kucmsntuple_* collection of a chain: one per tier, both eras (skim_submit.py reads the
    tier from this name and the year from the sample name)."""
    return f"kucmsntuple_SMS_T5Zg_SVHPM100_{P.CHAINS[chain]['tag']}_{NTUPLE_VERSION}"


def py_cfg_params(chain, era):
    fast = P.CHAINS[chain]["tag"] == "FastMini"
    return COMMON + ERA_PARAMS[era] + (FAST_PARAMS if fast else [])


def select(patterns):
    out = [s for s in P.samples() if not patterns or any(fnmatch.fnmatch(s[0], p) for p in patterns)]
    if not out:
        sys.exit(f"no sample matches {patterns}")
    return out


def mini_project(name):
    return os.path.join(WORK, name, "crab_projects", f"crab_{name}_{P.VERSION}")


def ntuple_dir(name):
    return os.path.join(WORK, name, "ntuple")


def batch_project(name, request):
    return os.path.join(ntuple_dir(name), f"crab_{request}")


def crab(cmd, *args, **kw):
    from CRABAPI.RawCommand import crabCommand
    return crabCommand(cmd, *args, **kw)


def _crab_child(q, cmd, args, kw):
    try:
        q.put(("ok", crab(cmd, *args, **kw)))
    except Exception as e:  # noqa: BLE001 - reported by the parent
        q.put(("error", f"{type(e).__name__}: {e}"))


def crab_forked(cmd, *args, **kw):
    """crab submit/proceed in a forked child.  CRABAPI caches the CMSSW config of the first
    submission in the process and refuses a second one with other pyCfgParams ("A different
    CMSSW configuration was already cached"), so every submission needs a fresh process.
    The child inherits the slim-sandbox filter installed in the parent."""
    import multiprocessing as mp
    ctx = mp.get_context("fork")
    q = ctx.Queue()
    p = ctx.Process(target=_crab_child, args=(q, cmd, args, kw))
    p.start()
    status, res = q.get()
    p.join()
    if status != "ok":
        sys.exit(f"   crab {cmd} failed: {res}")
    return res


def eos_ls(lfn_dir):
    r = subprocess.run(["eos", EOS, "ls", lfn_dir], capture_output=True, text=True)
    return r.stdout.split() if r.returncode == 0 else []


# --------------------------------------------------------------------------- ledger
def ledger_path(name):
    return os.path.join(ntuple_dir(name), "ledger.json")


def load_ledger(name):
    try:
        with open(ledger_path(name)) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"sample": name, "batches": []}


def save_ledger(name, ledger):
    os.makedirs(ntuple_dir(name), exist_ok=True)
    tmp = ledger_path(name) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(ledger, f, indent=1)
    os.replace(tmp, ledger_path(name))


def ledger_files(ledger):
    """MiniAOD files in batches that will produce ntuples (killed batches don't)."""
    return {f for b in ledger["batches"] if b["state"] != "killed" for f in b["files"]}


# --------------------------------------------------------------------------- MiniAOD inputs
def mini_files(name, chain, point, ct, era):
    """(LFNs of finished jobs, number of jobs, {state: count} of unfinished jobs)."""
    st = crab("status", dir=mini_project(name))
    jobs = st.get("jobs")
    if not jobs:
        sys.exit(f"{name}: crab status returned no per-job states")
    # jobs rerun by a recovery task (tmc.py recover/adopt) count once their file was adopted
    adopted = recovery.adopted_jobs(WORK, name)
    finished = sorted({int(j) for j, v in jobs.items() if v["State"] == "finished"} | adopted)
    other = {}
    for j, v in jobs.items():
        if v["State"] != "finished" and int(j) not in adopted:
            other[v["State"]] = other.get(v["State"], 0) + 1
    base = f"{P.OUT_LFN_BASE}/{P.sample_name(point, ct, era)}/{P.CHAINS[chain]['tag']}_{P.VERSION}"
    stamps = [s for s in eos_ls(base) if s[:6].isdigit()]
    if len(stamps) != 1:
        sys.exit(f"{name}: expected one task directory in {base}, found {stamps}")
    lfns, missing, present = [], [], {}
    for j in finished:
        sub = f"{base}/{stamps[0]}/{j // 1000:04d}"
        if sub not in present:
            present[sub] = set(eos_ls(sub))
        f = f"MiniAOD_{j}.root"
        (lfns if f in present[sub] else missing).append(f"{sub}/{f}")
    if missing:
        sys.exit(f"{name}: {len(missing)} finished jobs have no file on EOS, e.g. {missing[0]}")
    return lfns, len(jobs), other


# --------------------------------------------------------------------------- sandbox
def install_slim_sandbox():
    import CRABClient.JobType.UserTarball as UT
    crab_filter = UT.excludeFromTar

    def slim(ti):
        ti = crab_filter(ti)
        if ti is None:
            return None
        parts = ti.name.split("/")  # CMSSW_X/<top>/...
        top = parts[1] if len(parts) > 1 else ""
        if top == "src" and len(parts) > 2 and parts[2] not in SANDBOX_KEEP_SRC:
            return None
        if top == "python" and len(parts) > 2 and parts[2] in SANDBOX_DROP:
            return None
        if top == "lib" and any(p in parts[-1] for p in SANDBOX_DROP):
            return None
        return ti

    UT.excludeFromTar = slim


def check_sandbox(project):
    tars = [t for t in glob.glob(os.path.join(project, "inputs", "*")) if tarfile.is_tarfile(t)]
    if not tars:
        sys.exit(f"   no sandbox found in {project}/inputs to check")
    for t in tars:
        mb = os.path.getsize(t) / 2**20
        names = tarfile.open(t).getnames()
        leaked = [n for n in names if any(f"/{p}" in n for p in SANDBOX_DROP)]
        print(f"   {os.path.basename(t)}: {mb:.1f} MB, {len(names)} entries, files of dropped packages: {len(leaked)}")
        if leaked or mb > SANDBOX_MAX_MB:
            sys.exit(f"   sandbox check failed: {leaked[:3]}")


# --------------------------------------------------------------------------- commands
def crab_config(name, chain, point, ct, era, request, filelist):
    from CRABClient.UserUtilities import config
    c = config()
    c.General.requestName = request
    c.General.workArea = ntuple_dir(name)
    c.General.transferOutputs = True
    c.General.transferLogs = False
    c.JobType.pluginName = "Analysis"
    c.JobType.psetName = os.path.join(os.environ["CMSSW_BASE"], "src/KUCMSNtupleizer/KUCMSNtupleizer/test", PSET)
    c.JobType.pyCfgParams = py_cfg_params(chain, era)
    c.JobType.maxMemoryMB = MAX_MEMORY_MB
    c.JobType.allowUndistributedCMSSW = True
    c.Data.userInputFiles = [EOS + l for l in open(filelist).read().split()]
    c.Data.outputPrimaryDataset = P.sample_name(point, ct, era)
    c.Data.splitting = "FileBased"
    c.Data.unitsPerJob = FILES_PER_JOB
    c.Data.publication = False
    c.Data.outputDatasetTag = f"{collection(chain)}_{P.sample_name(point, ct, era)}"
    c.Data.outLFNDirBase = f"{NTUPLE_LFN_BASE}/{collection(chain)}"
    c.Site.storageSite = P.STORAGE_SITE
    c.Site.whitelist = WHITELIST
    return c


def cmd_submit(args):
    if "CMSSW_BASE" not in os.environ or not os.path.isdir(
            os.path.join(os.environ["CMSSW_BASE"], "src/KUCMSNtupleizer")):
        sys.exit("cmsenv in the KUCMSNtupleizer area first")
    install_slim_sandbox()
    for name, chain, point, ct, era in select(args.samples):
        print(f"== {name}")
        ledger = load_ledger(name)
        if any(b["state"] == "dryrun" for b in ledger["batches"]):
            print("   a --dryrun batch is waiting for `proceed`; skipping")
            continue
        lfns, njobs, other = mini_files(name, chain, point, ct, era)
        new = [l for l in lfns if l not in ledger_files(ledger)]
        print(f"   MiniAOD {len(lfns)}/{njobs} finished {other or ''}; already submitted "
              f"{len(lfns) - len(new)}; new {len(new)}")
        if other and not args.partial:
            print("   MiniAOD task not complete, nothing submitted (--partial to submit anyway)")
            continue
        if len(new) < args.min_files:
            print(f"   fewer than {args.min_files} new files, nothing submitted")
            continue
        request = f"{NTUPLE_TAG}_{name}_b{len(ledger['batches']) + 1}"
        if len(request) > 100:
            sys.exit(f"request name too long: {request}")
        if os.path.exists(batch_project(name, request)):
            sys.exit(f"   {batch_project(name, request)} exists but is not in the ledger: "
                     "a failed submission? check it and remove it")
        os.makedirs(ntuple_dir(name), exist_ok=True)
        filelist = os.path.join(ntuple_dir(name), f"{request}.txt")
        with open(filelist, "w") as f:
            f.write("\n".join(new) + "\n")
        print(f"   batch {request}: {(len(new) + FILES_PER_JOB - 1) // FILES_PER_JOB} jobs; "
              f"{' '.join(py_cfg_params(chain, era))}")
        cfg = crab_config(name, chain, point, ct, era, request, filelist)
        res = crab_forked("submit", *(["--dryrun"] if args.dryrun else []), config=cfg)
        if not os.path.isdir(batch_project(name, request)):
            sys.exit(f"   crab submit made no project directory: {res}")
        # recorded right after CRAB accepted it, before anything else can fail
        ledger["batches"].append({
            "request": request, "files": new, "files_per_job": FILES_PER_JOB,
            "state": "dryrun" if args.dryrun else "submitted",
            "time": datetime.datetime.now().isoformat(timespec="seconds"),
        })
        save_ledger(name, ledger)
        check_sandbox(batch_project(name, request))


def cmd_proceed(args):
    for name, chain, point, ct, era in select(args.samples):
        ledger = load_ledger(name)
        for b in ledger["batches"]:
            if b["state"] == "dryrun":
                print(f"== {b['request']}")
                crab_forked("proceed", dir=batch_project(name, b["request"]))
                b["state"] = "submitted"
                save_ledger(name, ledger)


def cmd_killed(args):
    ledger = load_ledger(args.sample)
    hits = [b for b in ledger["batches"] if b["request"] == args.request]
    if not hits:
        sys.exit(f"{args.request} is not in the ledger of {args.sample}")
    hits[0]["state"] = "killed"
    hits[0]["killed"] = datetime.datetime.now().isoformat(timespec="seconds")
    save_ledger(args.sample, ledger)
    print(f"{args.request}: marked killed; its {len(hits[0]['files'])} files count as not submitted")


def cmd_status(args):
    tot = {"mini": 0, "submitted": 0, "todo": 0}
    for name, chain, point, ct, era in select(args.samples):
        lfns, njobs, other = mini_files(name, chain, point, ct, era)
        ledger = load_ledger(name)
        done = ledger_files(ledger)
        todo = len([l for l in lfns if l not in done])
        tot["mini"] += len(lfns); tot["submitted"] += len(done); tot["todo"] += todo
        print(f"{name:60s} MiniAOD {len(lfns):3d}/{njobs:3d}  submitted {len(done):3d}  to submit {todo:3d}")
        for b in ledger["batches"]:
            if b["state"] in ("dryrun", "killed"):
                print(f"   {b['request'].rsplit('_', 1)[1]}: {b['state']}")
                continue
            st = crab("status", dir=batch_project(name, b["request"]))
            counts = {}
            for v in (st.get("jobs") or {}).values():
                counts[v["State"]] = counts.get(v["State"], 0) + 1
            print(f"   {b['request'].rsplit('_', 1)[1]}: {len(b['files'])} files, {st.get('status')} {counts}")
    print(f"TOTAL MiniAOD finished {tot['mini']}, submitted {tot['submitted']}, to submit {tot['todo']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status"); p.add_argument("samples", nargs="*"); p.set_defaults(f=cmd_status)
    p = sub.add_parser("submit"); p.add_argument("samples", nargs="*")
    p.add_argument("--dryrun", action="store_true", help="upload the sandbox and split, submit nothing")
    p.add_argument("--min-files", type=int, default=1, help="skip samples with fewer new files")
    p.add_argument("--partial", action="store_true", help="also submit samples whose MiniAOD is incomplete")
    p.set_defaults(f=cmd_submit)
    p = sub.add_parser("killed"); p.add_argument("sample"); p.add_argument("request")
    p.set_defaults(f=cmd_killed)
    p = sub.add_parser("proceed"); p.add_argument("samples", nargs="*"); p.set_defaults(f=cmd_proceed)
    args = ap.parse_args()
    args.f(args)


if __name__ == "__main__":
    main()
