#!/usr/bin/env python3
"""KUCMSNtupleizer CRAB tasks over the MiniAOD made by tmc.py, submitted in batches.

Needs cmsenv in the KUCMSNtupleizer area and crab-setup.sh.  Without them ntuple.py re-runs
itself in a shell that sets both up (ensure_env): the area is $KUCMS_AREA, or else the
NTUPLE_RELEASE area next to this directory (../NTUPLE_RELEASE or ../*/NTUPLE_RELEASE).

  ./ntuple.py status [samples]            MiniAOD files finished / submitted / still to submit,
                                          and the job states of every ntuple batch
  ./ntuple.py submit [--dryrun] [samples] submit samples whose MiniAOD task is complete, as a new
                                          batch task per sample (--partial: also incomplete ones)
  ./ntuple.py killed sample request       mark a batch killed with `crab kill` in the ledger
  ./ntuple.py proceed [samples]           submit batches left by submit --dryrun

Samples are glob patterns on the names of `./tmc.py list` and of the central samples already
submitted (default: all).  With --inputs FILE, they select among the samples of FILE only.

Central samples (--inputs FILE, e.g. inputs/splitSUSY_UL18.txt)
  FILE lists centrally produced MiniAOD datasets, one per line (format: read_inputs).  Each
  becomes the sample <name>_<tier>, e.g. splitSUSY_M2000_100_ctau1p0_2018_FullMini.  Its input is
  the DAS dataset (Data.inputDataset, CENTRAL_EVENTS_PER_JOB events per job, read over xrootd at
  WHITELIST).  It is submitted once as a whole (batch b1; again only after that batch is marked
  killed).  The ledger records the input and its DAS file list, so status/proceed/killed find
  the sample without FILE.  Output goes to the collection kucmsntuple_SMS_<model>_SVHPM100_
  <tier>_v38, in <collection>/<DAS primary dataset>/<collection>_<sample>/<timestamp>/.

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
import re
import shlex
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
NTUPLE_RELEASE = "CMSSW_15_1_0_patch5"  # the KUCMSNtupleizer area; $KUCMS_AREA overrides
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

# --------------------------------------------------------------------------- central samples
CENTRAL = "central"  # chain field of a central sample in select()
# Central files hold 1k-15k events (ours 200-450), in 1000-event lumis.  One file per job made
# 2-10 h jobs in the dry run (Nov 2026, CRAB's estimate from 11 events: 3.8 s/event), so jobs are
# split by events instead: at most ~2 h each.
CENTRAL_EVENTS_PER_JOB = 2000
TIERS = ("FullMini", "FastMini")
CAMPAIGN_ERA = ((r"Summer20UL18", "2018"), (r"Run3Summer22(?!EE)", "2022"))


def read_inputs(path):
    """Central samples of an --inputs file, as {work name: input record}.

    One DAS MiniAOD dataset per line, optionally followed by the sample name (default: the
    primary dataset up to "_Tune"/"_13TeV", plus _<era>).  "key: value" lines set model, tier
    or era for the lines after them: model goes into the collection name (default: the primary
    dataset up to its first "_"), tier is FullMini (default) or FastMini, era defaults to the
    one of the campaign.  "#" starts a comment."""
    opts, out = {}, {}
    for n, line in enumerate(open(path), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if not line.startswith("/") and ":" in line:
            k, v = (x.strip() for x in line.split(":", 1))
            if k not in ("model", "tier", "era"):
                sys.exit(f"{path}:{n}: unknown setting {k}")
            opts[k] = v
            continue
        words = line.split()
        dataset = words[0]
        parts = dataset.split("/")
        if len(parts) != 4 or parts[3] != "MINIAODSIM":
            sys.exit(f"{path}:{n}: not a MINIAODSIM dataset: {dataset}")
        pd, campaign = parts[1], parts[2]
        era = opts.get("era") or next((e for pat, e in CAMPAIGN_ERA if re.search(pat, campaign)), None)
        if era not in ERA_PARAMS:
            sys.exit(f"{path}:{n}: era {era} of {campaign} has no ERA_PARAMS (set it with 'era:')")
        tier = opts.get("tier", "FullMini")
        if tier not in TIERS:
            sys.exit(f"{path}:{n}: tier must be one of {TIERS}")
        sample = words[1] if len(words) > 1 else re.split(r"_Tune|_13TeV", pd)[0] + f"_{era}"
        inp = {"dataset": dataset, "sample": sample, "era": era, "tier": tier,
               "model": opts.get("model", pd.split("_")[0])}
        name = f"{sample}_{tier}"
        if name in out and out[name] != inp:
            sys.exit(f"{path}:{n}: {name} appears twice with different inputs")
        out[name] = inp
    if not out:
        sys.exit(f"{path}: no datasets")
    return out


def central_samples(inputs_file=None):
    """(work name, CENTRAL, input record, None, era) of the central samples: those with a
    ledger (recorded at submission) and those of the --inputs file."""
    found = {}
    for path in glob.glob(os.path.join(WORK, "*", "ntuple", "ledger.json")):
        led = json.load(open(path))
        if "input" in led:
            found[led["sample"]] = led["input"]
    if inputs_file:
        for name, inp in read_inputs(inputs_file).items():
            if name in found and found[name] != inp:
                sys.exit(f"{name}: --inputs gives {inp}, its ledger records {found[name]}")
            found[name] = inp
    return [(name, CENTRAL, inp, None, inp["era"]) for name, inp in sorted(found.items())]


def tier_of(chain, inp=None):
    return inp["tier"] if chain == CENTRAL else P.CHAINS[chain]["tag"]


def collection(chain, inp=None):
    """kucmsntuple_* collection of a chain: one per tier, both eras (skim_submit.py reads the
    tier from this name and the year from the sample name)."""
    model = f"SMS_{inp['model']}" if chain == CENTRAL else "SMS_T5Zg"
    return f"kucmsntuple_{model}_SVHPM100_{tier_of(chain, inp)}_{NTUPLE_VERSION}"


def py_cfg_params(chain, era, inp=None):
    fast = tier_of(chain, inp) == "FastMini"
    return COMMON + ERA_PARAMS[era] + (FAST_PARAMS if fast else [])


def select(patterns, inputs_file=None):
    """Private samples and central ones; with --inputs, only the samples of that file."""
    pool = central_samples(inputs_file)
    if inputs_file:
        names = set(read_inputs(inputs_file))
        pool = [s for s in pool if s[0] in names]
    else:
        pool = P.samples() + pool
    out = [s for s in pool if not patterns or any(fnmatch.fnmatch(s[0], p) for p in patterns)]
    if not out:
        sys.exit(f"no sample matches {patterns}")
    return out


def mini_project(name):
    return os.path.join(WORK, name, "crab_projects", f"crab_{name}_{P.VERSION}")


def ntuple_dir(name):
    return os.path.join(WORK, name, "ntuple")


def batch_project(name, request):
    return os.path.join(ntuple_dir(name), f"crab_{request}")


def env_ready():
    base = os.environ.get("CMSSW_BASE", "")
    if not base or not os.path.isdir(os.path.join(base, "src/KUCMSNtupleizer")):
        return False
    try:
        import CRABAPI.RawCommand  # noqa: F401
    except Exception:  # noqa: BLE001 - any failure means crab-setup.sh is missing or broken
        return False
    return True


def ntuple_area():
    if os.environ.get("KUCMS_AREA"):
        return os.environ["KUCMS_AREA"]
    up = os.path.dirname(ROOT)
    hits = [d for d in glob.glob(os.path.join(up, NTUPLE_RELEASE)) + glob.glob(os.path.join(up, "*", NTUPLE_RELEASE))
            if os.path.isdir(os.path.join(d, "src/KUCMSNtupleizer"))]
    if len(hits) != 1:
        sys.exit(f"found {len(hits)} {NTUPLE_RELEASE} areas with src/KUCMSNtupleizer next to {ROOT} {hits}; "
                 f"set KUCMS_AREA=/path/to/{NTUPLE_RELEASE} or cmsenv there and source crab-setup.sh")
    return hits[0]


def ensure_env():
    """Re-run this command after cmsenv in the KUCMSNtupleizer area and crab-setup.sh."""
    if env_ready():
        return
    if os.environ.get("NTUPLE_PY_REEXEC"):
        sys.exit("cmsenv + crab-setup.sh did not give a working CRAB client with KUCMSNtupleizer; "
                 "set them up by hand to see the error")
    area = ntuple_area()
    print(f"(setting up {area}: cmsenv + crab-setup.sh)", file=sys.stderr)
    script = "\n".join([
        "source /cvmfs/cms.cern.ch/cmsset_default.sh",
        f"cd {shlex.quote(os.path.join(area, 'src'))}",
        "eval `scramv1 runtime -sh`",
        "source /cvmfs/cms.cern.ch/common/crab-setup.sh",
        f"cd {shlex.quote(os.getcwd())}",
        "export NTUPLE_PY_REEXEC=1",
        'exec python3 "$@"',
    ])
    sys.stdout.flush()
    os.execvp("bash", ["bash", "-c", script, "bash", os.path.realpath(__file__)] + sys.argv[1:])


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


def das_files(dataset):
    """{LFN: events} of a DAS dataset (VALID files only, DAS's default)."""
    r = subprocess.run(["dasgoclient", f"-query=file dataset={dataset} | grep file.name, file.nevents"],
                       capture_output=True, text=True)
    files = dict((w[0], int(w[1])) for w in (l.split() for l in r.stdout.splitlines()) if len(w) == 2)
    if r.returncode or not files:
        sys.exit(f"   no files for {dataset} in DAS: {r.stderr.strip()}")
    return dict(sorted(files.items()))


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
def crab_config(name, chain, era, request, sample, splitting, units, inp=None):
    """Settings shared by private and central samples; the caller sets the input."""
    from CRABClient.UserUtilities import config
    c = config()
    c.General.requestName = request
    c.General.workArea = ntuple_dir(name)
    c.General.transferOutputs = True
    c.General.transferLogs = False
    c.JobType.pluginName = "Analysis"
    c.JobType.psetName = os.path.join(os.environ["CMSSW_BASE"], "src/KUCMSNtupleizer/KUCMSNtupleizer/test", PSET)
    c.JobType.pyCfgParams = py_cfg_params(chain, era, inp)
    c.JobType.maxMemoryMB = MAX_MEMORY_MB
    c.JobType.allowUndistributedCMSSW = True
    c.Data.splitting = splitting
    c.Data.unitsPerJob = units
    c.Data.publication = False
    c.Data.outputDatasetTag = f"{collection(chain, inp)}_{sample}"
    c.Data.outLFNDirBase = f"{NTUPLE_LFN_BASE}/{collection(chain, inp)}"
    c.Site.storageSite = P.STORAGE_SITE
    c.Site.whitelist = WHITELIST
    return c


def new_request(name, ledger):
    """Request name of the sample's next batch, after checking no stale project is in the way."""
    request = f"{NTUPLE_TAG}_{name}_b{len(ledger['batches']) + 1}"
    if len(request) > 100:
        sys.exit(f"request name too long: {request}")
    if os.path.exists(batch_project(name, request)):
        sys.exit(f"   {batch_project(name, request)} exists but is not in the ledger: "
                 "a failed submission? check it and remove it")
    return request


def submit_batch(args, name, ledger, request, cfg, files, **split):
    """split: files_per_job=n or events_per_job=n, as recorded in the ledger."""
    res = crab_forked("submit", *(["--dryrun"] if args.dryrun else []), config=cfg)
    if not os.path.isdir(batch_project(name, request)):
        sys.exit(f"   crab submit made no project directory: {res}")
    # recorded right after CRAB accepted it, before anything else can fail
    ledger["batches"].append({
        "request": request, "files": files, **split,
        "state": "dryrun" if args.dryrun else "submitted",
        "time": datetime.datetime.now().isoformat(timespec="seconds"),
    })
    save_ledger(name, ledger)
    check_sandbox(batch_project(name, request))


def submit_central(args, name, inp, ledger):
    live = [b["request"] for b in ledger["batches"] if b["state"] != "killed"]
    if live:
        print(f"   already submitted as {live[0]}; nothing to do (mark it killed to resubmit)")
        return
    files = das_files(inp["dataset"])
    request = new_request(name, ledger)
    nev = sum(files.values())
    print(f"   {inp['dataset']}\n   batch {request}: {len(files)} files, {nev} events, "
          f"~{-(-nev // CENTRAL_EVENTS_PER_JOB)} jobs; {' '.join(py_cfg_params(CENTRAL, inp['era'], inp))}")
    cfg = crab_config(name, CENTRAL, inp["era"], request, inp["sample"],
                      "EventAwareLumiBased", CENTRAL_EVENTS_PER_JOB, inp)
    cfg.Data.inputDataset = inp["dataset"]
    cfg.Data.inputDBS = "global"
    cfg.Data.ignoreLocality = True  # run at WHITELIST, read over xrootd, as for the private files
    ledger["input"] = inp  # makes the sample known to status/proceed/killed without --inputs
    os.makedirs(ntuple_dir(name), exist_ok=True)
    submit_batch(args, name, ledger, request, cfg, list(files), events_per_job=CENTRAL_EVENTS_PER_JOB)


def cmd_submit(args):
    if "CMSSW_BASE" not in os.environ or not os.path.isdir(
            os.path.join(os.environ["CMSSW_BASE"], "src/KUCMSNtupleizer")):
        sys.exit("cmsenv in the KUCMSNtupleizer area first")
    install_slim_sandbox()
    for name, chain, point, ct, era in select(args.samples, args.inputs):
        print(f"== {name}")
        ledger = load_ledger(name)
        if any(b["state"] == "dryrun" for b in ledger["batches"]):
            print("   a --dryrun batch is waiting for `proceed`; skipping")
            continue
        if chain == CENTRAL:  # point = input record
            submit_central(args, name, point, ledger)
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
        request = new_request(name, ledger)
        os.makedirs(ntuple_dir(name), exist_ok=True)
        filelist = os.path.join(ntuple_dir(name), f"{request}.txt")
        with open(filelist, "w") as f:
            f.write("\n".join(new) + "\n")
        print(f"   batch {request}: {(len(new) + FILES_PER_JOB - 1) // FILES_PER_JOB} jobs; "
              f"{' '.join(py_cfg_params(chain, era))}")
        sample = P.sample_name(point, ct, era)
        cfg = crab_config(name, chain, era, request, sample, "FileBased", FILES_PER_JOB)
        cfg.Data.userInputFiles = [EOS + l for l in open(filelist).read().split()]
        cfg.Data.outputPrimaryDataset = sample
        submit_batch(args, name, ledger, request, cfg, new, files_per_job=FILES_PER_JOB)


def cmd_proceed(args):
    for name, chain, point, ct, era in select(args.samples, args.inputs):
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
    for name, chain, point, ct, era in select(args.samples, args.inputs):
        ledger = load_ledger(name)
        done = ledger_files(ledger)
        if chain == CENTRAL:
            print(f"{name:60s} DAS input        submitted {len(done):3d}  {point['dataset'].split('/')[2]}")
        else:
            lfns, njobs, other = mini_files(name, chain, point, ct, era)
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
    inputs_help = "file of central MiniAOD datasets (see 'Central samples'); only its samples"
    p = sub.add_parser("status"); p.add_argument("samples", nargs="*"); p.set_defaults(f=cmd_status)
    p.add_argument("--inputs", help=inputs_help)
    p = sub.add_parser("submit"); p.add_argument("samples", nargs="*")
    p.add_argument("--inputs", help=inputs_help)
    p.add_argument("--dryrun", action="store_true", help="upload the sandbox and split, submit nothing")
    p.add_argument("--min-files", type=int, default=1, help="skip samples with fewer new files")
    p.add_argument("--partial", action="store_true", help="also submit samples whose MiniAOD is incomplete")
    p.set_defaults(f=cmd_submit)
    p = sub.add_parser("killed"); p.add_argument("sample"); p.add_argument("request")
    p.set_defaults(f=cmd_killed)
    p = sub.add_parser("proceed"); p.add_argument("samples", nargs="*"); p.set_defaults(f=cmd_proceed)
    p.add_argument("--inputs", help=inputs_help)
    args = ap.parse_args()
    ensure_env()
    args.f(args)


if __name__ == "__main__":
    main()
