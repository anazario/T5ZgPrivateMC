"""iDM sample set: the samples of the iDM-InclusiveGen generator, read from its manifest.

The generator (separate repository) owns the physics: mass points, gridpacks on EOS, the fragments
with the chi2 c*tau, and its manifest (fragments/manifest.json; see its make_fragments.py).  This
file owns the production: which chain, how many events, where the output goes.

    ./tmc.py --samples samples/idm.py list

Manifest: $IDM_MANIFEST, default ../iDM-InclusiveGen/fragments/manifest.json next to this repository.
"""
import json
import os
import sys

from sampleset import Sample

TOP = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
MANIFEST = os.environ.get("IDM_MANIFEST") or os.path.join(
    os.path.dirname(TOP), "iDM-InclusiveGen", "fragments", "manifest.json")

OUT_LFN_BASE = "/store/user/aabreuna/iDMPrivateMC"
VERSION = "v1"
# WORK: default work/idm
ERA, CHAIN = "2024", "2024_full"
TOTAL_EVENTS = 50000      # LHE events per sample (~97.5% kept by the matching); placeholder, Nov 2026
INCLUDE = None            # manifest names to produce; None: all of them
# NTUPLE_MODEL: not set (no iDM ntuples yet)


def samples():
    if not os.path.exists(MANIFEST):
        sys.exit(f"iDM manifest {MANIFEST} not found: check out iDM-InclusiveGen next to this repository "
                 "and run its ./make_fragments.py, or set IDM_MANIFEST")
    with open(MANIFEST) as f:
        m = json.load(f)
    if m.get("generator") != "iDM-InclusiveGen" or m.get("format") != 1:
        sys.exit(f"{MANIFEST}: not an iDM-InclusiveGen manifest of format 1")
    entries = m["samples"] if INCLUDE is None else [e for e in m["samples"] if e["name"] in INCLUDE]
    if INCLUDE is not None and len(entries) != len(INCLUDE):
        sys.exit(f"INCLUDE names not in the manifest: {sorted(set(INCLUDE) - {e['name'] for e in entries})}")
    out = []
    for e in entries:
        with open(os.path.join(os.path.dirname(MANIFEST), e["fragment"])) as f:
            text = f.read()
        out.append(Sample(f"{e['name']}_{ERA}", CHAIN, e["fragment_name"], text, ERA, TOTAL_EVENTS))
    return out
