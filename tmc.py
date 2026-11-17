#!/usr/bin/env python3
"""T5Zg private MC: set up releases, make configs, test locally, submit to CRAB.

Runs on a cmslpc-el9 login node with the standard library only.  Every CMSSW command is
executed inside the container that matches the release (cmssw-el7 / cmssw-el8).

    ./tmc.py list
    ./tmc.py setup                         # cmsrel the 5 releases, install fragments, scram b
    ./tmc.py configs [SAMPLE ...]          # cmsDriver -> work/<sample>/ (all samples by default)
    ./tmc.py test SAMPLE [-n 40]           # run the whole chain locally on n LHE events
    ./tmc.py jobtest SAMPLE [-n 40]        # run job/run_chain.sh as a CRAB worker node would
    ./tmc.py check FILE CHAIN              # compare MiniAOD provenance with the expected PSetIDs
    ./tmc.py submit [SAMPLE ...] [--dryrun] [--pilot N]   # --pilot N: only N jobs, separate task
    ./tmc.py proceed [SAMPLE ...] [--pilot]   # submit a task that was uploaded with --dryrun
    ./tmc.py status [SAMPLE ...] [--pilot]

SAMPLE may be a full sample name or any unique substring of it.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

import production as P

# Physical path: at the LPC ~/nobackup is a symlink chain into /uscms_data/dN, which the
# cmssw-elX containers don't mount by default, so ROOT is bound into every container (run_in).
ROOT = os.path.dirname(os.path.realpath(__file__))
RELEASES = os.path.join(ROOT, "releases")
WORK = os.path.join(ROOT, "work")
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
    allsamples = P.samples()
    if not patterns:
        return allsamples
    chosen = []
    for pat in patterns:
        hits = [s for s in allsamples if s[0] == pat] or [s for s in allsamples if pat in s[0]]
        if len(hits) != 1:
            sys.exit(f"'{pat}' matches {len(hits)} samples: {[h[0] for h in hits]}")
        chosen.append(hits[0])
    return chosen


def gen_release(chain):
    s = P.CHAINS[chain]["steps"][0]
    return s["release"], s["arch"]


def fragment_text(point, ct, era):
    with open(os.path.join(ROOT, "fragments", P.FRAGMENT_TEMPLATE[era])) as f:
        t = f.read()
    mgo, mn2, mn1 = point
    for key, val in (("XXXX", str(mgo)), ("YYYY", str(mn2)), ("ZZZZ", str(mn1)), ("TTTT", "%f" % ct)):
        t = t.replace(key, val)
    return t


def step_files(chain):
    """[(n, cfg, infile, outfile)] for a chain."""
    steps = P.CHAINS[chain]["steps"]
    out = []
    for i in range(1, len(steps) + 1):
        outfile = "MiniAOD.root" if i == len(steps) else f"step{i}.root"
        infile = None if i == 1 else f"step{i - 1}.root"
        out.append((i, f"step{i}_cfg.py", infile, outfile))
    return out


# ----------------------------------------------------------------------------- commands
def cmd_list(args):
    for name, chain, point, ct, era in P.samples():
        c = P.CHAINS[chain]
        njobs = -(-c["total_events"] // c["events_per_job"])
        print(f"{chain:10s} {njobs:4d} jobs  {name}")


def cmd_setup(args):
    os.makedirs(RELEASES, exist_ok=True)
    releases = {}
    for chain in P.CHAINS.values():
        for s in chain["steps"]:
            releases[s["release"]] = s["arch"]
    for rel, arch in releases.items():
        if os.path.isdir(os.path.join(RELEASES, rel)):
            print(f"{rel}: exists")
            continue
        print(f"{rel}: creating ({arch})")
        if run_in(rel, arch, [f"scram -a {arch} project {rel}"], RELEASES, env_release=False):
            sys.exit(f"scram project {rel} failed")
    # fragments go into the GEN release of each chain, then scram b
    built = set()
    for name, chain, point, ct, era in P.samples():
        rel, arch = gen_release(chain)
        d = os.path.join(RELEASES, rel, "src", "Configuration", "GenProduction", "python")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, P.fragment_name(point, ct, era) + ".py"), "w") as f:
            f.write(fragment_text(point, ct, era))
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
    for name, chain, point, ct, era in select(args.samples):
        c = P.CHAINS[chain]
        d = os.path.join(WORK, name)
        os.makedirs(d, exist_ok=True)
        print(f"== {name} ({chain})")
        chain_lines = []
        for (n, cfg, infile, outfile), s in zip(step_files(chain), c["steps"]):
            a = s["args"].format(frag=P.fragment_name(point, ct, era))
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
        write_crab_config(d, name, chain, point, ct, era)


def request_name(name, pilot):
    return f"{name}_{P.VERSION}" + ("_pilot" if pilot else "")


def crab_config_name(pilot):
    return "crabConfig_pilot.py" if pilot else "crabConfig.py"


def write_crab_config(d, name, chain, point, ct, era, pilot=0):
    """pilot=N: only N jobs, under a separate request name and output dataset tag."""
    c = P.CHAINS[chain]
    cfgs = [cfg for n, cfg, i, o in step_files(chain)]
    total = pilot * c["events_per_job"] if pilot else c["total_events"]
    tag = f"{c['tag']}_{P.VERSION}" + ("_pilot" if pilot else "")
    text = f"""from CRABClient.UserUtilities import config
config = config()

config.General.requestName = '{request_name(name, pilot)}'
config.General.workArea = 'crab_projects'
config.General.transferOutputs = True
config.General.transferLogs = False

config.JobType.pluginName = 'PrivateMC'
config.JobType.psetName = '{cfgs[0]}'
config.JobType.scriptExe = 'run_chain.sh'
config.JobType.inputFiles = {['chain.txt'] + cfgs[1:]!r}
config.JobType.outputFiles = ['MiniAOD.root']
config.JobType.disableAutomaticOutputCollection = True
config.JobType.allowUndistributedCMSSW = True
config.JobType.numCores = {c.get('threads', 1)}
config.JobType.maxMemoryMB = {c['max_memory_mb']}
config.JobType.maxJobRuntimeMin = 2000

config.Data.outputPrimaryDataset = '{P.sample_name(point, ct, era)}'
config.Data.splitting = 'EventBased'
config.Data.unitsPerJob = {c['events_per_job']}
config.Data.totalUnits = {total}
config.Data.publication = False
config.Data.outputDatasetTag = '{tag}'
config.Data.outLFNDirBase = '{P.OUT_LFN_BASE}'

config.Site.storageSite = '{P.STORAGE_SITE}'
config.Site.whitelist = {c['whitelist']!r}
"""
    with open(os.path.join(d, crab_config_name(pilot)), "w") as f:
        f.write(text)


def cmd_test(args):
    (name, chain, point, ct, era), = select([args.sample])
    d = os.path.join(WORK, name)
    if not os.path.exists(os.path.join(d, "chain.txt")):
        sys.exit("run ./tmc.py configs first")
    t = os.path.join(d, "test")
    shutil.rmtree(t, ignore_errors=True)
    os.makedirs(t)
    report = []
    for (n, cfg, infile, outfile), s in zip(step_files(chain), P.CHAINS[chain]["steps"]):
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
    nev = events_in(t, "MiniAOD.root", P.CHAINS[chain]["steps"][-1])
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
    (name, chain, point, ct, era), = select([args.sample])
    d = os.path.join(WORK, name)
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
    for fname in ["chain.txt", "run_chain.sh"] + [cfg for n, cfg, i, o in files[1:]]:
        shutil.copy(os.path.join(d, fname), t)
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
    leftover = sorted(f for f in os.listdir(t) if f.endswith(".root") and f != "MiniAOD.root")
    print("leftover intermediate files:", leftover or "none")
    if rc:
        sys.exit(f"job failed, see {log}")
    s = P.CHAINS[chain]["steps"][-1]
    print(f"{args.n} LHE events -> {events_in(t, 'MiniAOD.root', s)} MiniAOD events")
    cmd_check(argparse.Namespace(file=os.path.join(t, "MiniAOD.root"), chain=chain))


def events_in(cwd, fname, s):
    out = os.path.join(cwd, ".nevents")
    run_in(s["release"], s["arch"], [f"edmFileUtil {fname} > {out} 2>&1 || true"], cwd)
    m = re.search(r"(\d+) events", open(out).read())
    os.remove(out)
    return int(m.group(1)) if m else 0


def cmd_check(args):
    """Compare the processing history PSetIDs of a MiniAOD file with the expected ones."""
    expected = P.CHAINS[args.chain]["expected_psetids"]
    rel, arch = P.R2022_PAT
    cwd = os.path.dirname(os.path.abspath(args.file)) if not args.file.startswith("root:") else WORK
    os.makedirs(cwd, exist_ok=True)
    out = os.path.join(cwd, ".provdump")
    run_in(rel, arch, [f"edmProvDump {args.file} > {out} 2>&1 || true"], cwd)
    hist = re.findall(r"^\s*(\w+) '' '\"(CMSSW_\w+)\"' \[\d+\]\s+\(([0-9a-f]{32})\)", open(out).read(), re.M)
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
    for name, chain, point, ct, era in select(samples):
        d = os.path.join(WORK, name)
        rel, arch = gen_release(chain)
        print(f"== {name}")
        run_in(rel, arch, ["source /cvmfs/cms.cern.ch/common/crab-setup.sh", crab_cmd(d, name)], d)


def cmd_submit(args):
    # --dryrun: CRAB uploads the sandbox and splits the task but submits nothing; it waits for
    # `./tmc.py proceed`.  The local estimate run is skipped: tmc.py test/jobtest already did it.
    extra = " --dryrun --skip-estimates" if args.dryrun else ""
    for s in select(args.samples):
        name, chain, point, ct, era = s
        d = os.path.join(WORK, name)
        if not os.path.exists(os.path.join(d, "chain.txt")):
            sys.exit(f"{name}: run ./tmc.py configs first")
        write_crab_config(d, name, chain, point, ct, era, pilot=args.pilot)
    crab_in_env(args.samples, lambda d, name: f"crab submit -c {crab_config_name(args.pilot)}{extra}")


def cmd_proceed(args):
    crab_in_env(args.samples,
                lambda d, name: f"crab proceed -d crab_projects/crab_{request_name(name, args.pilot)}")


def cmd_status(args):
    crab_in_env(args.samples,
                lambda d, name: f"crab status -d crab_projects/crab_{request_name(name, args.pilot)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(f=cmd_list)
    sub.add_parser("setup").set_defaults(f=cmd_setup)
    p = sub.add_parser("configs"); p.add_argument("samples", nargs="*"); p.set_defaults(f=cmd_configs)
    p = sub.add_parser("test"); p.add_argument("sample"); p.add_argument("-n", type=int, default=40)
    p.set_defaults(f=cmd_test)
    p = sub.add_parser("jobtest"); p.add_argument("sample"); p.add_argument("-n", type=int, default=40)
    p.set_defaults(f=cmd_jobtest)
    p = sub.add_parser("check"); p.add_argument("file"); p.add_argument("chain", choices=list(P.CHAINS))
    p.set_defaults(f=cmd_check)
    p = sub.add_parser("submit"); p.add_argument("samples", nargs="*"); p.add_argument("--dryrun", action="store_true")
    p.add_argument("--pilot", type=int, default=0, metavar="N", help="submit only N jobs as a separate pilot task")
    p.set_defaults(f=cmd_submit)
    for cmd, f in (("proceed", cmd_proceed), ("status", cmd_status)):
        p = sub.add_parser(cmd); p.add_argument("samples", nargs="*")
        p.add_argument("--pilot", action="store_true", help="the pilot task instead of the full one")
        p.set_defaults(f=f)
    args = ap.parse_args()
    args.f(args)


if __name__ == "__main__":
    main()
