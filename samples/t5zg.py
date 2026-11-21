"""T5Zg sample set (the default): gluino pairs, gluino -> q qbar N2, N2 -> N1 gamma / N1 Z (BR 0.5/0.5).

Fragments from the templates in fragments/ (XXXX/YYYY/ZZZZ = masses, TTTT = cτ in cm as "%f").
Names and paths are those of the running production: don't change them (see sampleset.py).
"""
import os
import re
import sys

from sampleset import Sample

HERE = os.path.dirname(os.path.realpath(__file__))
TOP = os.path.dirname(HERE)

# CRAB requires the CERN username here; on cmseos /store/user/aabreuna is a link to .../anazario
OUT_LFN_BASE = "/store/user/aabreuna/T5ZgPrivateMC"
VERSION = "v1"  # goes into the CRAB dataset tag; bump for a new production round
WORK = os.path.join(TOP, "work")   # predates sample sets: other sets use work/<set>
NTUPLE_MODEL = "T5Zg"

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
# janguian's gridpacks_central has no slc7 (2018) gridpack for mGo = 1800.  Ours was built with
# genproductions mg265UL from his 2300 slc7 cards with only the gluino mass changed (Nov 2026).
GRIDPACK_OVERRIDE = {  # (era, mGo) -> gridpack replacing the template's path
    ("2018", 1800): "root://cmseos.fnal.gov//store/user/aabreuna/gridpacks/"
                    "T5Zg_mGo-1800_slc7_amd64_gcc700_CMSSW_10_6_44_tarball.tar.xz",
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


def fragment_text(point, ct, era):
    with open(os.path.join(TOP, "fragments", FRAGMENT_TEMPLATE[era])) as f:
        t = f.read()
    mgo, mn2, mn1 = point
    gridpack = GRIDPACK_OVERRIDE.get((era, mgo))
    if gridpack:
        default = re.findall(r"root://\S+_tarball\.tar\.xz", t)
        if len(default) != 1:
            sys.exit(f"{FRAGMENT_TEMPLATE[era]}: expected one gridpack path, found {default}")
        t = t.replace(default[0], gridpack)
    for key, val in (("XXXX", str(mgo)), ("YYYY", str(mn2)), ("ZZZZ", str(mn1)), ("TTTT", "%f" % ct)):
        t = t.replace(key, val)
    return t


def samples():
    return [Sample(sample_name(pt, ct, era), chain, fragment_name(pt, ct, era), fragment_text(pt, ct, era), era)
            for pt, ct, era, chain in SAMPLES]
