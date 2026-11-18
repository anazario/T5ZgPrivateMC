#!/usr/bin/env python3
"""Check that MiniAOD files from different jobs have independent pileup.

    python3 validate/pucheck.py MiniAOD_1.root MiniAOD_2.root [...]

For the first N events of each file it reads slimmedAddPileupInfo (in-time bunch crossing):
the true number of interactions and the z positions of the pileup vertices.  With fixed seeds
in the premix step, event i of every job gets the same premix event, so these sequences are
identical across files; with independent seeds they never match.
"""
import argparse
import sys

import ROOT
from DataFormats.FWLite import Events, Handle

ROOT.gROOT.SetBatch(True)


def pileup(fname, n):
    h = Handle("std::vector<PileupSummaryInfo>")
    out = []
    for i, e in enumerate(Events(fname)):
        if i >= n:
            break
        e.getByLabel("slimmedAddPileupInfo", h)
        bx0 = [p for p in h.product() if p.getBunchCrossing() == 0][0]
        zs = tuple(round(z, 4) for z in sorted(bx0.getPU_zpositions())[:5])
        out.append((round(bx0.getTrueNumInteractions(), 3), bx0.getPU_NumInteractions(), zs))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("-n", type=int, default=50, help="events per file (default 50)")
    a = ap.parse_args()
    seqs = {f: pileup(f, a.n) for f in a.files}
    names = list(seqs)
    for f in names:
        print("%-16s first events (true nInt, nPU): %s" % (f.split("/")[-1],
              " ".join("(%.1f,%d)" % (t, k) for t, k, _ in seqs[f][:6])))
    worst = 0
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            A, B = seqs[names[i]], seqs[names[j]]
            m = min(len(A), len(B))
            same = sum(A[k] == B[k] for k in range(m))
            worst = max(worst, same)
            print("%s vs %s: %d/%d events with identical pileup" % (
                names[i].split("/")[-1], names[j].split("/")[-1], same, m))
    print("\nRESULT:", "PASS (independent pileup)" if worst == 0 else "FAIL (pileup repeated across jobs)")
    return 1 if worst else 0


if __name__ == "__main__":
    sys.exit(main())
