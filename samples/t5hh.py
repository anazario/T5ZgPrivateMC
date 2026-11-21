"""T5HH sample set: gluino pairs, gluino -> q qbar N2, N2 -> N1 h (BR 1), FullSim 2018 and 2022.

The T5Zg points and lifetimes (samples/t5zg.py) with the N2 decaying to N1 h instead of N1 gamma / N1 Z,
for a comparison point by point.  At the compressed point (dm = 50 GeV) the h is off shell; the
template handles that (see fragments/Template_T5HH_*_fragment.py).  The gridpacks are T5Zg's: they
contain only p p > go go (+ up to 2 jets), so nothing in them depends on the N2 decay.

    ./tmc.py --samples samples/t5hh.py list        (or export TMC_SAMPLES=samples/t5hh.py)

Validated with a GEN-only test (Nov 2026): every N2 -> N1 h, h BRs equal to Pythia's SM table at the
on-shell point, nothing left undecayed, N2 c*t as set.  The off-shell h* line shape is Pythia's
(fixed-width Breit-Wigner x phase space), accepted as the standard treatment.
"""
import os
import re
import sys

from sampleset import Sample

HERE = os.path.dirname(os.path.realpath(__file__))
TOP = os.path.dirname(HERE)

# CRAB requires the CERN username here; on cmseos /store/user/aabreuna is a link to .../anazario
OUT_LFN_BASE = "/store/user/aabreuna/T5HHPrivateMC"
VERSION = "v1"  # goes into the CRAB dataset tag; bump for a new production round
# WORK: default work/t5hh
NTUPLE_MODEL = "T5HH"   # -> kucmsntuple_SMS_T5HH_SVHPM100_FullMini_<version>

# --------------------------------------------------------------------------- samples
# (mGo, mN2, mN1) in GeV, cτ(N2) in cm: the T5Zg points
COMPRESSED = (1800, 1750, 1700)   # dm = 50 < m(h): off-shell h
HIGH_DM = (2300, 1200, 1000)
CTAUS = (0.1, 20, 300)
ERAS = ("2018", "2022")

# FullSim only (no FastSim companions): 2 points x 3 lifetimes x 2 eras = 12 samples
SAMPLES = [(pt, ct, era, f"{era}_full") for era in ERAS for pt in (COMPRESSED, HIGH_DM) for ct in CTAUS]

# --------------------------------------------------------------------------- inputs
FRAGMENT_TEMPLATE = {  # in fragments/; XXXX/YYYY/ZZZZ = masses, TTTT = cτ in cm (as "%f")
    "2022": "Template_T5HH_13p6TeV_fragment.py",
    "2018": "Template_T5HH_13TeV_fragment.py",
}
# As in samples/t5zg.py: janguian's gridpacks_central has no slc7 (2018) gridpack for mGo = 1800,
# so the 2018 mGo-1800 samples use ours (genproductions mg265UL, his 2300 slc7 cards with only the
# gluino mass changed).
GRIDPACK_OVERRIDE = {  # (era, mGo) -> gridpack replacing the template's path
    ("2018", 1800): "root://cmseos.fnal.gov//store/user/aabreuna/gridpacks/"
                    "T5Zg_mGo-1800_slc7_amd64_gcc700_CMSSW_10_6_44_tarball.tar.xz",
}


# --------------------------------------------------------------------------- naming
def ctau_str(ct):
    """0.1 -> '0p1', 20 -> '20' (T5Zg's convention)."""
    return f"{ct:g}".replace(".", "p")


def sample_name(point, ct, era):
    mgo, mn2, mn1 = point
    return f"T5HH_mGo-{mgo}_mN2-{mn2}_mN1-{mn1}_N2ctau-{ctau_str(ct)}cm_{era}"


def fragment_name(point, ct, era):
    mgo, mn2, mn1 = point
    energy = "13p6TeV" if era == "2022" else "13TeV"
    return f"T5HH_mGo-{mgo}_mN2-{mn2}_mN1-{mn1}_N2ctau-{ctau_str(ct)}cm_{energy}_fragment"


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
    if re.search(r"XXXX|YYYY|ZZZZ|TTTT", t):
        sys.exit(f"{FRAGMENT_TEMPLATE[era]}: placeholder left after filling {point} {ct} {era}")
    return t


def samples():
    return [Sample(sample_name(pt, ct, era), chain, fragment_name(pt, ct, era), fragment_text(pt, ct, era), era)
            for pt, ct, era, chain in SAMPLES]
