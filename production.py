"""Single source of truth: which samples to make and how.

Every cmsDriver command below is copied from the CRAB debug configs of the existing
T5Zg productions (janguian's crabsubmit areas, Sep 2026), which in turn reproduce the
processing history stored in the existing MiniAOD (edmProvDump).  The only intended
difference is the 2022 FullSim PAT step, which no longer uses --fast (see README).

Placeholders, filled per sample by tmc.py:
  {frag}   fragment module name in Configuration/GenProduction/python (without .py)
"""
import os

# --------------------------------------------------------------------------- output
# CRAB requires the CERN username here; on cmseos /store/user/aabreuna is a link to .../anazario
OUT_LFN_BASE = "/store/user/aabreuna/T5ZgPrivateMC"
STORAGE_SITE = "T3_US_FNALLPC"
VERSION = "v1"  # goes into the CRAB dataset tag; bump for a new production round

# --------------------------------------------------------------------------- samples
# (mGo, mN2, mN1) in GeV, cτ(N2) in cm, era, chain
COMPRESSED = (1800, 1750, 1700)
HIGH_DM = (2300, 1200, 1000)

SAMPLES = (
    # 2018 FullSim of the compressed point (only 2022 exists)
    [(COMPRESSED, ct, "2018", "2018_full") for ct in (0.1, 20, 300)]
    # high-Δm: the missing lifetimes in both eras (300 cm exists in both)
    + [(HIGH_DM, ct, era, f"{era}_full") for era in ("2018", "2022") for ct in (0.1, 20)]
    # redo of the existing 2022 FullSim samples, whose MiniAOD used the FastSim PAT config
    + [(COMPRESSED, ct, "2022", "2022_full") for ct in (0.1, 20, 300)]
    + [(HIGH_DM, 300, "2022", "2022_full")]
    # 2022 FastSim companions of all 2022 points
    + [(pt, ct, "2022", "2022_fast") for pt in (COMPRESSED, HIGH_DM) for ct in (0.1, 20, 300)]
    # 2018 FastSim companions of all 2018 points
    + [(pt, ct, "2018", "2018_fast") for pt in (COMPRESSED, HIGH_DM) for ct in (0.1, 20, 300)]
)

# --------------------------------------------------------------------------- inputs
FRAGMENT_TEMPLATE = {  # in fragments/; XXXX/YYYY/ZZZZ = masses, TTTT = cτ in cm (as "%f")
    "2022": "Template_T5Zg_13p6TeV_fragment.py",
    "2018": "Template_T5Zg_13TeV_fragment.py",
}

PREMIX_2022_FULL = "dbs:/Neutrino_E-10_gun/Run3Summer21PrePremix-Summer22_124X_mcRun3_2022_realistic_v11-v2/PREMIX"
PREMIX_2018_FULL = "dbs:/Neutrino_E-10_gun/RunIISummer20ULPrePremix-UL18_106X_upgrade2018_realistic_v11_L1v1-v2/PREMIX"
# Justin's private 2022 FastSim premix library "PUlib1": 10 files x 10k events
_PULIB1 = [
    ("1", "260730_165504"), ("2", "260730_182608"), ("3", "260730_182732"), ("4", "260730_182850"),
    ("5", "260730_183005"), ("6", "260730_183116"), ("7", "260730_183236"), ("8", "260730_183352"),
    ("9", "260730_183505"), ("10", "260730_183619"),
]
PREMIX_2022_FAST = ",".join(
    f"root://cmseos.fnal.gov//store/user/janguian/FastSIM2022_PrePremix/PrePremix_PUlib1-{i}_2022/{stamp}/0000/"
    f"PPD-Run3Summer21PrePremix_sublib1-{i}_1.root" for i, stamp in _PULIB1
)
# Central UL18 FastSim premix library, restricted to files pinned on disk (most of it is tape
# only); see the header of premix/UL18_FastSim_disk.txt.  LFNs: opened via the site config / AAA.
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "premix", "UL18_FastSim_disk.txt")) as _f:
    PREMIX_2018_FAST = ",".join(l.strip() for l in _f if l.strip().startswith("/store/"))

# --------------------------------------------------------------------------- chains
# Each step: release, SCRAM arch, cmsDriver arguments.  Step N reads stepN-1.root and writes
# stepN.root; the last step writes MiniAOD.root.  Step 1 becomes CRAB's psetName.
R2022_GEN = ("CMSSW_12_4_14_patch3", "el8_amd64_gcc10")
R2022_PAT = ("CMSSW_13_0_13", "el8_amd64_gcc11")
R2018_GEN = ("CMSSW_10_6_17_patch1", "slc7_amd64_gcc700")
R2018_HLT = ("CMSSW_10_2_16_UL", "slc7_amd64_gcc700")
R2018_PAT = ("CMSSW_10_6_27", "slc7_amd64_gcc700")
R2018_FAST = ("CMSSW_10_6_35_patch1", "slc7_amd64_gcc700")    # central UL18 FastSim (SMS-T5Wg provenance)
R2018_FASTPAT = ("CMSSW_10_6_29", "slc7_amd64_gcc700")

MON = "--customise Configuration/DataProcessing/Utils.addMonitoring"


def step(release, args):
    return {"release": release[0], "arch": release[1], "args": args}

CHAINS = {
    "2022_full": {
        "tag": "FullMini",
        "events_per_job": 800,      # LHE events requested; ~28% survive MLM matching
        "total_events": 300000,     # -> 375 jobs, ~83k events, as the existing samples
        # 1 thread: premix DIGI+HLT peaks at 3.0 GB, the CRAB single-core limit.  2 cores allow
        # 5000 MB (2500/core).  numberOfThreads is untracked, so PSetIDs don't change.
        "threads": 2,
        "max_memory_mb": 5000,
        "whitelist": ["T2_*", "T3_*"],
        "steps": [
            step(R2022_GEN,
                "Configuration/GenProduction/python/{frag}.py --eventcontent RAWSIM,LHE " + MON +
                " --datatier GEN-SIM,LHE --conditions 124X_mcRun3_2022_realistic_v12"
                " --beamspot Realistic25ns13p6TeVEarly2022Collision"
                " --customise_commands 'process.RandomNumberGeneratorService.externalLHEProducer.initialSeed=int(123456)"
                "\\nprocess.source.numberEventsInLuminosityBlock=cms.untracked.uint32(250)'"
                " --step LHE,GEN,SIM --geometry DB:Extended --era Run3 --mc"),
            step(R2022_GEN,
                "--eventcontent PREMIXRAW " + MON + " --datatier GEN-SIM-RAW"
                f" --pileup_input {PREMIX_2022_FULL} --conditions 124X_mcRun3_2022_realistic_v12"
                " --step DIGI,DATAMIX,L1,DIGI2RAW,HLT:2022v12 --procModifiers premix_stage2,siPixelQualityRawToDigi"
                " --geometry DB:Extended --datamix PreMix --era Run3 --mc"),
            step(R2022_GEN,
                "--eventcontent AODSIM " + MON + " --datatier AODSIM --conditions 124X_mcRun3_2022_realistic_v12"
                " --step RAW2DIGI,L1Reco,RECO,RECOSIM --procModifiers siPixelQualityRawToDigi"
                " --geometry DB:Extended --era Run3 --mc"),
            step(R2022_PAT,  # NO --fast: this is the fix
                "--eventcontent MINIAODSIM " + MON + " --datatier MINIAODSIM --conditions 130X_mcRun3_2022_realistic_v5"
                " --step PAT --geometry DB:Extended --era Run3,run3_miniAOD_12X --mc"),
        ],
        # expected process PSetIDs of the MiniAOD (validation): steps whose config doesn't depend on the sample
        "expected_psetids": {
            "HLT": "b722d1cf11a99a4476f09a94f34c768e",   # existing T5Zg/gogoGZ 2022 FullSim
            "RECO": "0190a31f56290269056b5583a84fa4cc",  # existing T5Zg/gogoGZ 2022 FullSim
            "PAT": "5443ed9e0a49f9c5d5f0b2fff4804347",   # central Run3Summer22MiniAODv4 FullSim (TTto2L2Nu)
        },
    },
    "2022_fast": {
        "tag": "FastMini",
        "events_per_job": 1600,     # FastSim is ~10x cheaper; fewer, longer jobs
        "total_events": 300000,
        # prmon (whole job, 40 events): 1 thread 3.6-4.1 GB, 2 threads 5.1 GB; each extra thread
        # adds ~1.5 GB while CRAB grants 2.5 GB per core.  The 3000 MB single-core pilot was killed
        # (50660).  4 cores = 10000 MB, the CRAB maximum for 4 cores; expected peak ~8 GB.
        "threads": 4,
        "max_memory_mb": 10000,
        # PUlib1 is read from cmseos: keep jobs on US sites
        "whitelist": ["T2_US_*", "T3_US_FNALLPC"],
        "steps": [
            step(R2022_GEN,
                "Configuration/GenProduction/python/{frag}.py"
                f" --pileup_input {PREMIX_2022_FAST} --mc --eventcontent AODSIM --fast --datatier AODSIM"
                " --conditions 124X_mcRun3_2022_realistic_v12 --beamspot Realistic25ns13p6TeVEarly2022Collision"
                " --step LHE,GEN,SIM,RECOBEFMIX,DIGI,DATAMIX,L1,DIGI2RAW,L1Reco,RECO --geometry DB:Extended"
                " --datamix PreMix --era Run3_FastSim --procModifiers premix_stage2"),
            step(R2022_PAT,  # --fast is correct here; same config as the existing gogoGZ FastSim MiniAOD
                "--eventcontent MINIAODSIM --fast " + MON + " --datatier MINIAODSIM"
                " --conditions 130X_mcRun3_2022_realistic_v5 --step PAT --geometry DB:Extended"
                " --era Run3,run3_miniAOD_12X --mc"),
        ],
        "expected_psetids": {
            "PAT": "d5d37242d92d7fac6d1ac2345b76e889",   # existing gogoGZ 2022 FastSim MiniAOD
        },
    },
    "2018_full": {
        "tag": "FullMini",
        "events_per_job": 800,
        "total_events": 300000,
        "max_memory_mb": 3000,
        "whitelist": ["T2_*", "T3_*"],
        "steps": [
            step(R2018_GEN,
                "Configuration/GenProduction/python/{frag}.py --eventcontent RAWSIM,LHE " + MON +
                " --datatier RAWSIM,LHE --conditions 106X_upgrade2018_realistic_v4"
                " --beamspot Realistic25ns13TeVEarly2018Collision --step LHE,GEN --geometry DB:Extended"
                " --era Run2_2018 --mc"),
            step(R2018_GEN,
                "--eventcontent RAWSIM " + MON + " --datatier GEN-SIM --conditions 106X_upgrade2018_realistic_v11_L1v1"
                " --beamspot Realistic25ns13TeVEarly2018Collision --step SIM --geometry DB:Extended"
                " --era Run2_2018 --runUnscheduled --mc"),
            step(R2018_GEN,
                "--eventcontent PREMIXRAW " + MON + " --datatier GEN-SIM-DIGI"
                f" --pileup_input {PREMIX_2018_FULL} --conditions 106X_upgrade2018_realistic_v11_L1v1"
                " --step DIGI,DATAMIX,L1,DIGI2RAW --procModifiers premix_stage2 --geometry DB:Extended"
                " --datamix PreMix --era Run2_2018 --runUnscheduled --mc"),
            step(R2018_HLT,
                "--eventcontent RAWSIM " + MON + " --datatier GEN-SIM-RAW --conditions 102X_upgrade2018_realistic_v15"
                " --customise_commands 'process.source.bypassVersionCheck = cms.untracked.bool(True)'"
                " --step HLT:2018v32 --geometry DB:Extended --era Run2_2018 --mc"),
            step(R2018_GEN,
                "--eventcontent AODSIM " + MON + " --datatier AODSIM --conditions 106X_upgrade2018_realistic_v11_L1v1"
                " --step RAW2DIGI,L1Reco,RECO,RECOSIM,EI --geometry DB:Extended --era Run2_2018 --runUnscheduled --mc"),
            step(R2018_PAT,
                "--eventcontent MINIAODSIM " + MON + " --datatier MINIAODSIM --conditions 106X_upgrade2018_realistic_v16_L1v1"
                " --step PAT --procModifiers run2_miniAOD_UL --geometry DB:Extended --era Run2_2018"
                " --runUnscheduled --mc"),
        ],
        "expected_psetids": {  # existing T5Zg 2018 FullSim MiniAOD
            "SIM": "128efeffab8ceb577467d0e58be013b1",
            "DIGI2RAW": "a03b32ff24c52459822077d23898e252",
            "HLT": "b403a189a2d057e62e59ed092120c7f4",
            "RECO": "a5d501e738bc46974ac8d371aaff19e9",
            "PAT": "e2b541b9a2622f5ad47ca02585af3f7c",
        },
    },
    # From the provenance of the central SMS-T5Wg RunIISummer20UL18 FastSim MiniAODv2 (all
    # non-generator modules of the FastSim step and the whole PAT config compared):
    #  * FastSim step: era Run2_2018_FastSim (no GEM) and procModifier fastSimFixLongLivedBug, which
    #    lets SM daughters of exotics produced outside the beam pipe make hits instead of being
    #    decayed by FastSim (essential for the displaced N2).  No --runUnscheduled: central has
    #    the modules on the paths, not in tasks.
    #  * PAT: era Run2_2018 (with GEM); --fast adds the fastSim modifier.
    "2018_fast": {
        "tag": "FastMini",
        "events_per_job": 1600,
        "total_events": 300000,
        # prmon (whole job, 40 events, 4 threads): peak 4.5 GB, half of 2022 FastSim.  The 2022
        # FastSim pilot grew from 7.9 GB (jobtest) to 8.3 GB mid-job with 1600 events, so leave
        # ~55% headroom.
        "threads": 4,
        "max_memory_mb": 7000,
        # premix is read remotely (FNAL disk / CERN): keep jobs on US sites
        "whitelist": ["T2_US_*", "T3_US_FNALLPC"],
        "steps": [
            step(R2018_FAST,
                "Configuration/GenProduction/python/{frag}.py"
                f" --pileup_input {PREMIX_2018_FAST} --mc --eventcontent AODSIM --fast --datatier AODSIM"
                " --conditions 106X_upgrade2018_realistic_v16 --beamspot Realistic25ns13TeVEarly2018Collision"
                " --step LHE,GEN,SIM,RECOBEFMIX,DIGI,DATAMIX,L1,DIGI2RAW,L1Reco,RECO --geometry DB:Extended"
                " --datamix PreMix --era Run2_2018_FastSim --procModifiers premix_stage2,fastSimFixLongLivedBug"),
            step(R2018_FASTPAT,
                "--eventcontent MINIAODSIM --fast " + MON + " --datatier MINIAODSIM"
                " --conditions 106X_upgrade2018_realistic_v16_L1v1 --step PAT --procModifiers run2_miniAOD_UL"
                " --geometry DB:Extended --era Run2_2018 --runUnscheduled --mc"),
        ],
        "expected_psetids": {
            "PAT": "1d31bd42d1d45aa3ad7364946c5ce28f",   # central SMS-T5Wg UL18 FastSim MiniAODv2
        },
    },
}


# --------------------------------------------------------------------------- naming
def ctau_str(ct):
    """0.1 -> '0p1', 20 -> '20' (the convention of the existing samples)."""
    return f"{ct:g}".replace(".", "p")


def sample_name(point, ct, era):
    mgo, mn2, mn1 = point
    return f"T5Zg_mGo-{mgo}_mN2-{mn2}_mN1-{mn1}_N2ctau-{ctau_str(ct)}cm_{era}"


def fragment_name(point, ct, era):
    mgo, mn2, mn1 = point
    energy = "13p6TeV" if era == "2022" else "13TeV"
    return f"T5Zg_mGo-{mgo}_mN2-{mn2}_mN1-{mn1}_N2ctau-{ctau_str(ct)}cm_{energy}_fragment"


def samples():
    """[(name, chain_key, point, ct, era)] with the chain tag appended to the name."""
    out = []
    for point, ct, era, chain in SAMPLES:
        out.append((f"{sample_name(point, ct, era)}_{CHAINS[chain]['tag']}", chain, point, ct, era))
    return out
