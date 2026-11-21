#!/usr/bin/env python3
"""Private MC production: set up releases, make configs, test locally, submit to CRAB.

Runs on a cmslpc-el9 login node with the standard library only.  Every CMSSW command is
executed inside the container that matches the release (cmssw-el7 / cmssw-el8).  What is produced
comes from a sample set (sampleset.py): samples/t5zg.py unless --samples FILE or $TMC_SAMPLES.

    ./tmc.py [--samples FILE] COMMAND ...
    ./tmc.py list
    ./tmc.py setup [CHAIN ...]             # cmsrel the releases of the set's chains, install fragments, scram b
    ./tmc.py configs [SAMPLE ...]          # cmsDriver -> <work>/<sample>/ (all samples by default)
    ./tmc.py test SAMPLE [-n 40]           # run the whole chain locally on n LHE events
    ./tmc.py jobtest SAMPLE [-n 40]        # run job/run_chain.sh as a CRAB worker node would
    ./tmc.py check FILE CHAIN              # compare MiniAOD provenance with the expected PSetIDs
    ./tmc.py submit [SAMPLE ...] [--dryrun] [--pilot N]   # --pilot N: only N jobs, separate task
    ./tmc.py proceed [SAMPLE ...] [--pilot]   # submit a task that was uploaded with --dryrun
    ./tmc.py status [SAMPLE ...] [--pilot]
    ./tmc.py recover SAMPLE --kill [--dryrun]  # kill a task whose last jobs hang
    ./tmc.py recover SAMPLE [--dryrun]         # rerun its unfinished jobs as batch r<k>
    ./tmc.py adopt [SAMPLE ...] [--dryrun]     # move finished recovery files into the task dir

SAMPLE may be a full sample name or any unique substring of it.  Recovery: see recovery.py.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

import chains as C
import recovery
import sampleset

EOS_URL = "root://cmseos.fnal.gov/"

# Physical path: at the LPC ~/nobackup is a symlink chain into /uscms_data/dN, which the
# cmssw-elX containers don't mount by default, so ROOT is bound into every container (run_in).
ROOT = os.path.dirname(os.path.realpath(__file__))
RELEASES = os.path.join(ROOT, "releases")
S = None     # the sample set (main); its work directory is S.work
CONTAINER = {"el8": "cmssw-el8", "slc7": "cmssw-el7"}


# ----------------------------------------------------------------------------- helpers
def container_for(arch):
    for prefix, cmd in CONTAINER.items():
        if arch.startswith(prefix):
            return cmd
    sys.exit(f"no container known for {arch}")


def run_in(release, arch, commands, cwd, env_release=True, log=None):
    """Run shell commands in the container of `arch`, optionally inside `release`'s cmsenv."""
    lines = ["set -e", "source /cvmfs/cms.cern.ch/cmsset_default.sh", f"export SCRAM_ARCH={arch}"]
    if env_release:
        lines += [f"cd {RELEASES}/{release}/src", "eval $(scram runtime -sh)"]
    lines += [f"cd {cwd}"] + list(commands)
    fd, script = tempfile.mkstemp(prefix=".tmc_", suffix=".sh", dir=cwd)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    try:
        out = open(log, "w") if log else None
        rc = subprocess.run([container_for(arch), "-B", ROOT, "--", "bash", script], cwd=cwd,
                            stdout=out, stderr=subprocess.STDOUT if out else None).returncode
        if out:
            out.close()
    finally:
        os.remove(script)
    return rc


def select(patterns):
    allsamples = S.samples
    if not patterns:
        return allsamples
    chosen = []
    for pat in patterns:
        hits = [s for s in allsamples if s.name == pat] or [s for s in allsamples if pat in s.name]
        if len(hits) != 1:
            sys.exit(f"'{pat}' matches {len(hits)} samples: {[h.name for h in hits]}")
        chosen.append(hits[0])
    return chosen


def gen_release(chain):
    s = C.CHAINS[chain]["steps"][0]
    return s["release"], s["arch"]


def tools_release(chain):
    """Release for edmProvDump / edmFileUtil on a chain's MiniAOD (13_0_13 reads 10_6 to 13_0)."""
    return C.CHAINS[chain].get("tools_release", C.R2022_PAT)


def step_files(chain):
    """[(n, cfg, infile, outfile)] for a chain."""
    steps = C.CHAINS[chain]["steps"]
    out = []
    for i in range(1, len(steps) + 1):
        outfile = "MiniAOD.root" if i == len(steps) else f"step{i}.root"
        infile = None if i == 1 else f"step{i - 1}.root"
        out.append((i, f"step{i}_cfg.py", infile, outfile))
    return out


# ----------------------------------------------------------------------------- commands
def cmd_list(args):
    print(f"sample set {S.path}: output {S.out_lfn_base}, work {S.work}")
    for s in S.samples:
        njobs = -(-s.total_events // C.CHAINS[s.chain]["events_per_job"])
        print(f"{s.chain:10s} {njobs:4d} jobs  {s.name}")


def cmd_setup(args):
    os.makedirs(RELEASES, exist_ok=True)
    chains = args.chains or S.chains()
    bad = [c for c in chains if c not in C.CHAINS]
    if bad:
        sys.exit(f"unknown chains {bad}; known: {list(C.CHAINS)}")
    releases = {}
    for c in chains:
        for s in C.CHAINS[c]["steps"]:
            releases[s["release"]] = s["arch"]
        rel, arch = tools_release(c)   # edmFileUtil / edmProvDump of the chain's MiniAOD (check, jobtest)
        releases.setdefault(rel, arch)
    for rel, arch in releases.items():
        if os.path.isdir(os.path.join(RELEASES, rel)):
            print(f"{rel}: exists")
            continue
        print(f"{rel}: creating ({arch})")
        if run_in(rel, arch, [f"scram -a {arch} project {rel}"], RELEASES, env_release=False):
            sys.exit(f"scram project {rel} failed")
    # fragments go into the GEN release of each chain, then scram b
    built = set()
    for s in S.samples:
        if s.chain not in chains:
            continue
        rel, arch = gen_release(s.chain)
        d = os.path.join(RELEASES, rel, "src", "Configuration", "GenProduction", "python")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, s.fragment_name + ".py"), "w") as f:
            f.write(s.fragment_text)
        built.add((rel, arch))
    # The fragments use run_generic_tarball_xrootd.sh, which 10_6_X releases don't ship.  Add it
    # (janguian's copy, as used for the existing 2018 samples) next to the release's own
    # run_generic_tarball_cvmfs.sh, which it sources from its own directory.  CRAB ships src/*/*/data.
    lhe = "GeneratorInterface/LHEInterface/data"
    add_script = (
        f"if [ ! -e $CMSSW_RELEASE_BASE/src/{lhe}/run_generic_tarball_xrootd.sh ]; then"
        f" mkdir -p $CMSSW_BASE/src/{lhe}"
        f" && cp {ROOT}/genscripts/run_generic_tarball_xrootd.sh $CMSSW_BASE/src/{lhe}/"
        f" && cp $CMSSW_RELEASE_BASE/src/{lhe}/run_generic_tarball_cvmfs.sh $CMSSW_BASE/src/{lhe}/"
        f" && echo '   added run_generic_tarball_xrootd.sh'; fi")
    for rel, arch in sorted(built):
        print(f"{rel}: scram b")
        if run_in(rel, arch, [add_script, "scram b -j 8 > scram_build.log 2>&1 || (tail -20 scram_build.log; exit 1)"],
                  os.path.join(RELEASES, rel, "src")):
            sys.exit(f"scram b failed in {rel}")
    print("setup done")


def cmd_configs(args):
    for smp in select(args.samples):
        c = C.CHAINS[smp.chain]
        d = os.path.join(S.work, smp.name)
        os.makedirs(d, exist_ok=True)
        print(f"== {smp.name} ({smp.chain})")
        chain_lines = []
        for (n, cfg, infile, outfile), s in zip(step_files(smp.chain), c["steps"]):
            a = s["args"].format(frag=smp.fragment_name)
            a += f" --python_filename {cfg} --fileout file:{outfile} --no_exec"
            if c.get("threads", 1) > 1:
                a += f" --nThreads {c['threads']}"
            a += " -n 10" if n == 1 else f" --filein file:{infile} -n -1"
            log = os.path.join(d, f"cmsDriver_step{n}.log")
            if run_in(s["release"], s["arch"], ["cmsDriver.py " + a], d, log=log):
                sys.exit(f"cmsDriver step {n} failed, see {log}")
            chain_lines.append(f"{n} {s['release']} {s['arch']} {cfg} {infile or '-'}")
            print(f"   step {n}: {cfg} ({s['release']})")
        with open(os.path.join(d, "chain.txt"), "w") as f:
            f.write("\n".join(chain_lines) + "\n")
        shutil.copy(os.path.join(ROOT, "job", "run_chain.sh"), d)
        write_crab_config(d, smp)


def request_name(name, pilot, recover=None):
    return (f"{name}_{S.version}" + ("_pilot" if pilot else "")
            + (f"_r{recover}" if recover else ""))


def crab_config_name(pilot, recover=None):
    if recover:
        return f"crabConfig_r{recover}.py"
    return "crabConfig_pilot.py" if pilot else "crabConfig.py"


def recover_dir(d, k):
    return os.path.join(d, "recover", f"r{k}")


def write_crab_config(d, smp, pilot=0, recover=None):
    """pilot=N: only N jobs, under a separate request name and output dataset tag.
    recover={"k", "njobs", "runtime_min"}: recovery batch r<k> (see recovery.py), which ships
    recover/r<k>/recover.txt and the run_chain.sh copied next to it."""
    c = C.CHAINS[smp.chain]
    # CRAB ignores the blacklist for sites matched by a whitelist wildcard (see chains.py)
    bad = [s for s in c["whitelist"] if "*" in s or s in C.SITE_BLACKLIST]
    if bad:
        sys.exit(f"{smp.chain}: whitelist must list explicit, non-blacklisted sites; got {bad}")
    cfgs = [cfg for n, cfg, i, o in step_files(smp.chain)]
    total = pilot * c["events_per_job"] if pilot else smp.total_events
    tag = f"{c['tag']}_{S.version}" + ("_pilot" if pilot else "")
    inputs, script, runtime, k = ["chain.txt"] + cfgs[1:], "run_chain.sh", 2000, None
    if recover:
        k = recover["k"]
        total = recover["njobs"] * c["events_per_job"]
        tag += f"_r{k}"
        rdir = os.path.relpath(recover_dir(d, k), d)
        inputs.append(os.path.join(rdir, "recover.txt"))
        script = os.path.join(rdir, "run_chain.sh")
        runtime = recover["runtime_min"]
    text = f"""from CRABClient.UserUtilities import config
config = config()

config.General.requestName = '{request_name(smp.name, pilot, k)}'
config.General.workArea = 'crab_projects'
config.General.transferOutputs = True
config.General.transferLogs = False

config.JobType.pluginName = 'PrivateMC'
config.JobType.psetName = '{cfgs[0]}'
config.JobType.scriptExe = '{script}'
config.JobType.inputFiles = {inputs!r}
config.JobType.outputFiles = ['MiniAOD.root']
config.JobType.disableAutomaticOutputCollection = True
config.JobType.allowUndistributedCMSSW = True
config.JobType.numCores = {c.get('threads', 1)}
config.JobType.maxMemoryMB = {c['max_memory_mb']}
config.JobType.maxJobRuntimeMin = {runtime}

config.Data.outputPrimaryDataset = '{smp.dataset}'
config.Data.splitting = 'EventBased'
config.Data.unitsPerJob = {c['events_per_job']}
config.Data.totalUnits = {total}
config.Data.publication = False
config.Data.outputDatasetTag = '{tag}'
config.Data.outLFNDirBase = '{S.out_lfn_base}'

config.Site.storageSite = '{C.STORAGE_SITE}'
config.Site.whitelist = {c['whitelist']!r}
config.Site.blacklist = {C.SITE_BLACKLIST!r}
"""
    with open(os.path.join(d, crab_config_name(pilot, k)), "w") as f:
        f.write(text)


def cmd_test(args):
    smp, = select([args.sample])
    chain, d = smp.chain, os.path.join(S.work, smp.name)
    if not os.path.exists(os.path.join(d, "chain.txt")):
        sys.exit("run ./tmc.py configs first")
    t = os.path.join(d, "test")
    shutil.rmtree(t, ignore_errors=True)
    os.makedirs(t)
    report = []
    for (n, cfg, infile, outfile), s in zip(step_files(chain), C.CHAINS[chain]["steps"]):
        shutil.copy(os.path.join(d, cfg), t)
        if n == 1:  # override the event count, as CRAB does per job
            with open(os.path.join(t, cfg), "a") as f:
                f.write(f"\nprocess.maxEvents.input = cms.untracked.int32({args.n})\n")
                f.write("if hasattr(process, 'externalLHEProducer'):\n")
                f.write(f"    process.externalLHEProducer.nEvents = cms.untracked.uint32({args.n})\n")
        log = os.path.join(t, f"step{n}.log")
        t0 = time.time()
        rc = run_in(s["release"], s["arch"], [f"cmsRun -j fjr_step{n}.xml {cfg}"], t, log=log)
        dt = time.time() - t0
        fjr = os.path.join(t, f"fjr_step{n}.xml")
        rss = re.findall(r'Name="PeakValueRss" Value="([\d.]+)"',
                         open(fjr, errors="replace").read() if os.path.exists(fjr) else "")
        rss = [f"{float(r):.0f}" for r in rss]
        size = os.path.getsize(os.path.join(t, outfile)) if os.path.exists(os.path.join(t, outfile)) else 0
        report.append((n, s["release"], rc, dt, rss[-1] if rss else "?", size))
        print(f"step {n} {s['release']:22s} rc={rc} {dt:7.0f} s  peak RSS {rss[-1] if rss else '?'} MB  "
              f"{outfile} {size / 1e6:.1f} MB")
        if rc:
            sys.exit(f"step {n} failed, see {log}")
    nev = events_in(t, "MiniAOD.root", C.CHAINS[chain]["steps"][-1])
    total = sum(r[3] for r in report)
    print(f"\n{args.n} LHE events -> {nev} MiniAOD events; total {total:.0f} s"
          + (f" = {total / nev:.0f} s per MiniAOD event" if nev else ""))
    if nev:
        print(f"MiniAOD size {report[-1][5] / nev / 1e3:.0f} kB per event")
    cmd_check(argparse.Namespace(file=os.path.join(t, "MiniAOD.root"), chain=chain))


def cmd_jobtest(args):
    """Run job/run_chain.sh as a CRAB worker node would: only the shipped files, in a fresh
    directory, starting from the GEN release's environment (the CRAB sandbox).  Releases of later
    steps are set up from CVMFS inside the job, so this tests the release switching."""
    smp, = select([args.sample])
    chain, d = smp.chain, os.path.join(S.work, smp.name)
    if not os.path.exists(os.path.join(d, "chain.txt")):
        sys.exit("run ./tmc.py configs first")
    t = os.path.join(d, "jobtest")
    shutil.rmtree(t, ignore_errors=True)
    os.makedirs(t)
    files = step_files(chain)
    # CRAB turns psetName into PSet.py and sets the per-job event count
    with open(os.path.join(d, files[0][1])) as f:
        pset = f.read()
    pset += f"\nprocess.maxEvents.input = cms.untracked.int32({args.n})\n"
    pset += "if hasattr(process, 'externalLHEProducer'):\n"
    pset += f"    process.externalLHEProducer.nEvents = cms.untracked.uint32({args.n})\n"
    with open(os.path.join(t, "PSet.py"), "w") as f:
        f.write(pset)
    for fname in ["chain.txt"] + [cfg for n, cfg, i, o in files[1:]]:
        shutil.copy(os.path.join(d, fname), t)
    # the repository's run_chain.sh, i.e. what the next task (or recovery batch) will ship
    shutil.copy(os.path.join(ROOT, "job", "run_chain.sh"), t)
    E = C.CHAINS[chain]["events_per_job"]
    if args.as_job:  # run as recovery job 1 standing in for original job N (see recovery.py)
        with open(os.path.join(t, "recover.txt"), "w") as f:
            f.write(f"1 {args.as_job} {E}\n")
    rel, arch = gen_release(chain)
    log = os.path.join(t, "job.log")
    print(f"running run_chain.sh in {t} (sandbox release {rel}); log: {log}")
    t0 = time.time()
    rc = run_in(rel, arch, ["bash run_chain.sh 1"], t, log=log)
    print(f"run_chain.sh exit {rc} after {time.time() - t0:.0f} s")
    for line in open(log, errors="replace"):
        if line.startswith("=== step") and ("exit" in line):
            n = line.split()[2]
            fjr = os.path.join(t, "FrameworkJobReport.xml" if n == "1" else f"fjr_step{n}.xml")
            text = open(fjr, errors="replace").read() if os.path.exists(fjr) else ""
            rss = re.findall(r'Name="PeakValueRss" Value="([\d.]+)"', text)
            thr = re.findall(r'Name="NumberOfThreads" Value="(\d+)"', text)
            print(f"  {line.strip()}  peak RSS {float(rss[-1]):.0f} MB" if rss else f"  {line.strip()}",
                  f"threads {thr[-1]}" if thr else "")
    # step 1's outputs are kept on purpose (CRAB's wrapper post-processes them; see run_chain.sh)
    leftover = sorted(f for f in os.listdir(t) if f.endswith(".root") and f != "MiniAOD.root"
                      and not f.startswith("step1"))
    print("leftover intermediate files:", leftover or "none")
    if rc:
        sys.exit(f"job failed, see {log}")
    s = C.CHAINS[chain]["steps"][-1]
    print(f"{args.n} LHE events -> {events_in(t, 'MiniAOD.root', s)} MiniAOD events")
    events = file_events(os.path.join(t, "MiniAOD.root"), chain)
    job = args.as_job or 1
    lo, hi = recovery.expected_events(job, E)
    problems = recovery.check_numbering(events, job, E)
    print(f"numbering as job {job} (run 1, lumi {job}, events {lo}-{hi}): "
          + ("OK" if not problems else "FAILED: " + "; ".join(problems))
          + (f"; events {min(e for _, _, e in events)}-{max(e for _, _, e in events)}" if events else ""))
    cmd_check(argparse.Namespace(file=os.path.join(t, "MiniAOD.root"), chain=chain))
    if problems:
        sys.exit(1)


def file_events(path, chain=None):
    """[(run, lumi, event)] of a local file or an LFN (read over xrootd), via edmFileUtil -e."""
    url = path if os.path.exists(path) else EOS_URL + path
    cwd = os.path.dirname(os.path.abspath(path)) if os.path.exists(path) else S.work
    os.makedirs(cwd, exist_ok=True)
    out = os.path.join(cwd, ".events")
    rel, arch = tools_release(chain) if chain else C.R2022_PAT
    run_in(rel, arch, [f"edmFileUtil -e {url} > {out} 2>&1 || true"], cwd)
    text = open(out, errors="replace").read()
    os.remove(out)
    return recovery.parse_edmfileutil_events(text)


def events_in(cwd, fname, s):
    out = os.path.join(cwd, ".nevents")
    run_in(s["release"], s["arch"], [f"edmFileUtil {fname} > {out} 2>&1 || true"], cwd)
    m = re.search(r"(\d+) events", open(out).read())
    os.remove(out)
    return int(m.group(1)) if m else 0


def cmd_check(args):
    """Compare the processing history PSetIDs of a MiniAOD file with the expected ones."""
    expected = C.CHAINS[args.chain]["expected_psetids"]
    rel, arch = tools_release(args.chain)
    cwd = os.path.dirname(os.path.abspath(args.file)) if not args.file.startswith("root:") else S.work
    os.makedirs(cwd, exist_ok=True)
    out = os.path.join(cwd, ".provdump")
    run_in(rel, arch, [f"edmProvDump {args.file} > {out} 2>&1 || true"], cwd)
    # 13_0 edmProvDump prints  SIM '' '"CMSSW_.."' [1] (id);  15_0 drops the empty pass name ''
    hist = re.findall(r"^\s*(\w+) (?:'' )?'\"(CMSSW_\w+)\"' \[\d+\]\s+\(([0-9a-f]{32})\)", open(out).read(), re.M)
    os.remove(out)
    if not hist:
        sys.exit("could not read the processing history")
    ok = True
    print("\nprocess   release                  PSetID                            expected")
    for proc, release, psid in hist:
        exp = expected.get(proc)
        status = "-" if exp is None else ("MATCH" if exp == psid else f"DIFFERENT (expected {exp})")
        ok &= exp is None or exp == psid
        print(f"{proc:9s} {release:24s} {psid}  {status}")
    print("provenance check:", "OK" if ok else "FAILED")


def crab_in_env(samples, crab_cmd):
    for s in select(samples):
        d = os.path.join(S.work, s.name)
        rel, arch = gen_release(s.chain)
        print(f"== {s.name}")
        run_in(rel, arch, ["source /cvmfs/cms.cern.ch/common/crab-setup.sh", crab_cmd(d, s.name)], d)


def cmd_submit(args):
    # --dryrun: CRAB uploads the sandbox and splits the task but submits nothing; it waits for
    # `./tmc.py proceed`.  The local estimate run is skipped: tmc.py test/jobtest already did it.
    extra = " --dryrun --skip-estimates" if args.dryrun else ""
    for s in select(args.samples):
        d = os.path.join(S.work, s.name)
        if not os.path.exists(os.path.join(d, "chain.txt")):
            sys.exit(f"{s.name}: run ./tmc.py configs first")
        write_crab_config(d, s, pilot=args.pilot)
    crab_in_env(args.samples, lambda d, name: f"crab submit -c {crab_config_name(args.pilot)}{extra}")


def cmd_proceed(args):
    crab_in_env(args.samples,
                lambda d, name: f"crab proceed -d crab_projects/crab_{request_name(name, args.pilot)}")


def cmd_status(args):
    crab_in_env(args.samples,
                lambda d, name: f"crab status -d crab_projects/crab_{request_name(name, args.pilot)}")


# ----------------------------------------------------------------------------- recovery
def crab_json(d, project, cmd="status"):
    """crabCommand(cmd, dir=project) through CRABAPI, run in the 2022 GEN release (el8)."""
    rel, arch = C.R2022_GEN
    out, py, log = (os.path.join(d, x) for x in (".crab.json", ".crab_json.py", ".crab_json.log"))
    with open(py, "w") as f:
        f.write("import json\nfrom CRABAPI.RawCommand import crabCommand\n"
                f"json.dump(crabCommand({cmd!r}, dir={project!r}), open({out!r}, 'w'), default=str)\n")
    rc = run_in(rel, arch, ["source /cvmfs/cms.cern.ch/common/crab-setup.sh", f"python3 {py}"], d, log=log)
    if rc or not os.path.exists(out):
        sys.exit(f"crab {cmd} -d {project} failed, see {log}")
    with open(out) as f:
        res = json.load(f)
    for x in (out, py, log):
        os.remove(x)
    return res


def xrd(*args):
    r = subprocess.run(["xrdfs", EOS_URL.rstrip("/")] + list(args), capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def xrd_ls(path):
    """Names in an EOS directory, or None if it doesn't exist."""
    rc, out, err = xrd("ls", path)
    return [os.path.basename(x) for x in out.split()] if rc == 0 else None


def xrd_exists(path):
    return xrd("stat", path)[0] == 0


def task_dir(lfn_base):
    """The one CRAB timestamp directory under an output dataset directory (None if none yet)."""
    stamps = [s for s in (xrd_ls(lfn_base) or []) if re.match(r"^\d{6}_\d{6}$", s)]
    if len(stamps) > 1:
        sys.exit(f"{lfn_base}: more than one task directory: {stamps}")
    return f"{lfn_base}/{stamps[0]}" if stamps else None


def mini_lfn_base(smp, k=None):
    return (f"{S.out_lfn_base}/{smp.dataset}/{C.CHAINS[smp.chain]['tag']}_{S.version}"
            + (f"_r{k}" if k else ""))


def cmd_recover(args):
    """Kill a MiniAOD task (--kill), then rerun its unfinished jobs as a recovery batch."""
    smp, = select([args.sample])
    name, chain = smp.name, smp.chain
    c = C.CHAINS[chain]
    E = c["events_per_job"]
    d = os.path.join(S.work, name)
    orig = f"crab_projects/crab_{request_name(name, 0)}"
    led = recovery.load(S.work, name)
    st = crab_json(d, orig)
    jobs = st["jobs"]
    states = {}
    for v in jobs.values():
        states[v["State"]] = states.get(v["State"], 0) + 1
    print(f"== {name}: {st.get('status')} {states}")
    # After `crab kill` CRAB shows the task FAILED and its killed jobs "failed" (Nov 2026), so
    # the ledger's kill record counts too.
    killed = st.get("status") == "KILLED" or bool(led["killed"])
    if args.kill:
        if killed:
            sys.exit(f"   already killed ({led['killed'] or 'status KILLED'})")
        moving = sorted((int(j) for j, v in jobs.items() if v["State"] == "transferring"))
        if moving:  # a killed job could still land its file: let them finish first
            sys.exit(f"   jobs {moving} are transferring their output; kill after they finish")
        if args.dryrun:
            print("   --dryrun: would kill the task")
            return
        rel, arch = C.R2022_GEN
        if run_in(rel, arch, ["source /cvmfs/cms.cern.ch/common/crab-setup.sh", f"crab kill -d {orig}"], d):
            sys.exit("   crab kill failed")
        led["killed"] = recovery.now()
        recovery.save(S.work, name, led)
        print("   killed.  Run ./tmc.py recover again once crab status shows no idle or running jobs.")
        return
    active = sorted(int(j) for j, v in jobs.items() if v["State"] in recovery.ACTIVE_STATES)
    if not killed or active:
        msg = (f"   task is {st.get('status')}, {'killed' if killed else 'not killed'}, active jobs "
               f"{active[:20]}: kill it first (--kill) and wait until no job is active")
        if not args.dryrun:
            sys.exit(msg)
        print(msg + "  [--dryrun: planning anyway]")
    finished = {int(j) for j, v in jobs.items() if v["State"] == "finished"}
    adopted = {int(n) for n in led["adopted"]}
    pending = recovery.pending_jobs(led)
    missing = sorted(set(int(j) for j in jobs) - finished - adopted - pending)
    print(f"   finished {len(finished)}, adopted {len(adopted)}, in recovery batches {len(pending)}, "
          f"to recover {len(missing)}: {missing}")
    if not missing:
        return
    # A file for a job CRAB doesn't count as finished: don't overwrite or duplicate it
    base = task_dir(mini_lfn_base(smp))
    present = {}
    for n in list(missing):
        sub = f"{base}/{n // 1000:04d}"
        if sub not in present:
            present[sub] = set(xrd_ls(sub) or [])
        if f"MiniAOD_{n}.root" in present[sub]:
            msg = f"   {sub}/MiniAOD_{n}.root exists although job {n} is not finished: check it"
            if not args.dryrun:
                sys.exit(msg)
            print(msg + "  [--dryrun: leaving it out]")
            missing.remove(n)
    if not missing:
        return
    # Good jobs' longest run time x 2.5, at least 4 h (a hung job then costs 4 h, not 33 h)
    walls = [v["WallDurations"][-1] for j, v in jobs.items()
             if int(j) in finished and v.get("WallDurations")]
    longest = max(walls) / 60 if walls else 0
    runtime = min(2000, max(240, int(-(-2.5 * longest // 10) * 10)))
    k = len(led["batches"]) + 1
    mapping = {str(i): n for i, n in enumerate(missing, 1)}
    request = request_name(name, 0, k)
    print(f"   batch r{k}: {len(missing)} jobs, maxJobRuntimeMin {runtime} (longest good job "
          f"{longest:.0f} min), sites {c['whitelist']}")
    print(f"   recovery job -> original job: {' '.join(f'{i}->{n}' for i, n in mapping.items())}")
    if os.path.exists(os.path.join(d, "crab_projects", f"crab_{request}")):
        sys.exit(f"   crab_projects/crab_{request} exists but is not in the ledger: check it")
    rdir = recover_dir(d, k)
    os.makedirs(rdir, exist_ok=True)
    with open(os.path.join(rdir, "recover.txt"), "w") as f:
        f.writelines(f"{i} {n} {E}\n" for i, n in mapping.items())
    shutil.copy(os.path.join(ROOT, "job", "run_chain.sh"), rdir)
    write_crab_config(d, smp, recover={"k": k, "njobs": len(missing), "runtime_min": runtime})
    if args.dryrun:
        print(f"   --dryrun: wrote {crab_config_name(0, k)} and {os.path.relpath(rdir, d)}/; nothing submitted")
        return
    rel, arch = gen_release(chain)
    run_in(rel, arch, ["source /cvmfs/cms.cern.ch/common/crab-setup.sh",
                       f"crab submit -c {crab_config_name(0, k)}"], d)
    if not os.path.isdir(os.path.join(d, "crab_projects", f"crab_{request}")):
        sys.exit("   crab submit made no project directory")
    led["batches"].append({"k": k, "request": request, "map": mapping, "events_per_job": E,
                           "runtime_min": runtime, "state": "submitted", "time": recovery.now()})
    recovery.save(S.work, name, led)
    print(f"   submitted {request}; recorded in {os.path.relpath(recovery.ledger_path(S.work, name), ROOT)}")


def cmd_adopt(args):
    """Move finished recovery files into the original task directory as MiniAOD_<N>.root."""
    for smp in select(args.samples):
        name, chain = smp.name, smp.chain
        led = recovery.load(S.work, name)
        live = [b for b in led["batches"] if b["state"] == "submitted"]
        if not live:
            continue
        d = os.path.join(S.work, name)
        print(f"== {name}")
        dst_base = task_dir(mini_lfn_base(smp))
        checked = False
        for b in live:
            k, E = b["k"], b["events_per_job"]
            jobs = crab_json(d, f"crab_projects/crab_{b['request']}")["jobs"]
            src_base = task_dir(mini_lfn_base(smp, k))
            waiting = {}
            for i, n in sorted(b["map"].items(), key=lambda x: int(x[0])):
                i, n = int(i), int(n)
                if str(n) in led["adopted"]:
                    continue
                state = jobs[str(i)]["State"]
                src = f"{src_base}/{i // 1000:04d}/MiniAOD_{i}.root" if src_base else None
                if state != "finished" or not src or not xrd_exists(src):
                    waiting[state] = waiting.get(state, 0) + 1
                    continue
                dst_dir = f"{dst_base}/{n // 1000:04d}"
                dst = f"{dst_dir}/MiniAOD_{n}.root"
                if xrd_exists(dst):
                    sys.exit(f"   {dst} exists already: not moving {src}")
                events = file_events(src, chain)
                problems = recovery.check_numbering(events, n, E)
                if problems:
                    print(f"   r{k} job {i} -> {n}: NOT adopted: {'; '.join(problems)}")
                    continue
                if args.dryrun:
                    print(f"   r{k} job {i} -> {n}: {len(events)} events, numbering OK; would move to {dst}")
                    continue
                rc, out, err = xrd("mkdir", "-p", dst_dir)
                rc, out, err = xrd("mv", src, dst)
                if rc or not xrd_exists(dst) or xrd_exists(src):
                    sys.exit(f"   moving {src} -> {dst} failed: {err.strip()}")
                led["adopted"][str(n)] = {"batch": b["request"], "job": i, "from": src, "to": dst,
                                          "events": len(events), "time": recovery.now()}
                recovery.save(S.work, name, led)
                print(f"   r{k} job {i} -> MiniAOD_{n}.root: {len(events)} events, numbering OK, moved")
                if not checked:  # provenance of the first adopted file
                    cmd_check(argparse.Namespace(file=EOS_URL + dst, chain=chain))
                    checked = True
            left = [n for n in b["map"].values() if str(n) not in led["adopted"]]
            if not left:
                b["state"] = "done"
                recovery.save(S.work, name, led)
            print(f"   r{k}: {len(b['map']) - len(left)}/{len(b['map'])} adopted"
                  + (f"; waiting {waiting}" if waiting else ""))


def main():
    global S
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    # dest sample_set: the subcommands' positional "samples" (name patterns) would overwrite args.samples
    ap.add_argument("--samples", dest="sample_set", metavar="FILE",
                    help="sample set (default: $TMC_SAMPLES, else samples/t5zg.py)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(f=cmd_list)
    p = sub.add_parser("setup"); p.add_argument("chains", nargs="*", help="only these chains (default: the set's)")
    p.set_defaults(f=cmd_setup)
    p = sub.add_parser("configs"); p.add_argument("samples", nargs="*"); p.set_defaults(f=cmd_configs)
    p = sub.add_parser("test"); p.add_argument("sample"); p.add_argument("-n", type=int, default=40)
    p.set_defaults(f=cmd_test)
    p = sub.add_parser("jobtest"); p.add_argument("sample"); p.add_argument("-n", type=int, default=40)
    p.add_argument("--as-job", type=int, default=0, metavar="N",
                   help="run as a recovery job standing in for original job N")
    p.set_defaults(f=cmd_jobtest)
    p = sub.add_parser("check"); p.add_argument("file"); p.add_argument("chain", choices=list(C.CHAINS))
    p.set_defaults(f=cmd_check)
    p = sub.add_parser("submit"); p.add_argument("samples", nargs="*"); p.add_argument("--dryrun", action="store_true")
    p.add_argument("--pilot", type=int, default=0, metavar="N", help="submit only N jobs as a separate pilot task")
    p.set_defaults(f=cmd_submit)
    for cmd, f in (("proceed", cmd_proceed), ("status", cmd_status)):
        p = sub.add_parser(cmd); p.add_argument("samples", nargs="*")
        p.add_argument("--pilot", action="store_true", help="the pilot task instead of the full one")
        p.set_defaults(f=f)
    p = sub.add_parser("recover"); p.add_argument("sample")
    p.add_argument("--kill", action="store_true", help="kill the original task (first step)")
    p.add_argument("--dryrun", action="store_true", help="plan and write the configs, submit nothing")
    p.set_defaults(f=cmd_recover)
    p = sub.add_parser("adopt"); p.add_argument("samples", nargs="*")
    p.add_argument("--dryrun", action="store_true", help="check the files, move nothing")
    p.set_defaults(f=cmd_adopt)
    args = ap.parse_args()
    S = sampleset.load(args.sample_set)
    args.f(args)


if __name__ == "__main__":
    main()
