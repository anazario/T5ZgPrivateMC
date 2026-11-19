#!/usr/bin/env python3
"""Generator-level validation of T5Zg MiniAOD (prunedGenParticles), FWLite.

    python3 validate/gencheck.py MiniAOD_1.root [MiniAOD_2.root ...]  [--ctau CM] [--masses MGO,MN2,MN1]

Masses and cτ are taken from the file path (T5Zg_mGo-..._mN2-..._mN1-..._N2ctau-...cm) unless given.
Run inside any cmsenv with python3 FWLite (e.g. CMSSW_13_0_13; reads the 10_6 MiniAOD as well).

Checks, against the fragment (fragments/Template_T5Zg_*_fragment.py):
  * every event has 2 gluinos and 2 N2; gluino -> q qbar N2 with q = d,u,s,c,b at 20% each
  * N2 -> N1 gamma : N1 Z = 50 : 50, independently for the two N2 (gamma gamma : gamma Z : Z Z = 1 : 2 : 1)
  * Z (on-shell point) decays with Pythia's branching ratios: hadrons, e, mu, tau, invisible
  * N2 proper decay length c*t = L * m / |p| is exponential with mean cτ (limitTau0 = off)
  * generated masses of gluino, N2, N1
A check fails if it is off by more than 3 sigma: an exact binomial p-value below 0.0027 for the
fractions, a Gaussian pull above 3 for the mean c*t.
"""
import argparse
import math
import re
import sys

import ROOT
from DataFormats.FWLite import Events, Handle

ROOT.gROOT.SetBatch(True)

GLUINO, N2, N1, PHOTON, Z = 1000021, 1000023, 1000022, 22, 23
# PDG Z branching fractions (Pythia 8's defaults agree at the per-mille level)
Z_BR = {"hadrons": 0.6991, "ee": 0.0336, "mumu": 0.0336, "tautau": 0.0336, "invisible": 0.2001}


P_3SIGMA = 0.0027  # two-sided


def binom_pvalue(k, n, p):
    """Two-sided exact binomial p-value: total probability of outcomes no likelier than k."""
    if p <= 0 or p >= 1:
        return 1.0 if k == round(p * n) else 0.0
    logpmf = [math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
              + i * math.log(p) + (n - i) * math.log(1 - p) for i in range(n + 1)]
    ref = logpmf[k] + 1e-9
    return min(1.0, sum(math.exp(x) for x in logpmf if x <= ref))


def last_copy(p):
    """Follow p down its chain of self-copies (radiation recoil, status changes)."""
    while True:
        same = [p.daughter(i) for i in range(p.numberOfDaughters()) if p.daughter(i).pdgId() == p.pdgId()]
        if not same:
            return p
        p = same[0]


def z_mode(z):
    ids = sorted(abs(z.daughter(i).pdgId()) for i in range(z.numberOfDaughters()))
    s = set(ids)
    if s <= {1, 2, 3, 4, 5}:
        return "hadrons"
    if s == {11}:
        return "ee"
    if s == {13}:
        return "mumu"
    if s == {15}:
        return "tautau"
    if s and s <= {12, 14, 16}:
        return "invisible"
    return "other:" + ",".join(map(str, ids))


class Check:
    def __init__(self):
        self.rows, self.failed = [], 0

    def frac(self, name, k, n, expect):
        """Exact two-sided binomial test (Gaussian pulls are wrong for small n or small BRs)."""
        if n == 0:
            self.rows.append((name, "no entries", "", "", "FAIL")); self.failed += 1; return
        f = k / n
        p = binom_pvalue(k, n, expect)
        ok = p > P_3SIGMA
        self.failed += not ok
        self.rows.append((name, f"{k}/{n} = {f:.4f}", f"{expect:.4f}", f"p = {p:.3g}", "ok" if ok else "FAIL"))

    def value(self, name, val, err, expect, unit=""):
        pull = (val - expect) / err if err > 0 else (0.0 if abs(val - expect) < 1e-6 * max(1, abs(expect)) else float("inf"))
        ok = abs(pull) < 3
        self.failed += not ok
        self.rows.append((name, f"{val:.4g} ± {err:.2g} {unit}".rstrip(), f"{expect:.4g} {unit}".rstrip(),
                          f"{pull:+.1f} sigma", "ok" if ok else "FAIL"))

    def exact(self, name, ok, detail):
        self.failed += not ok
        self.rows.append((name, detail, "", "", "ok" if ok else "FAIL"))

    def show(self):
        w = [max(len(str(r[i])) for r in self.rows + [("check", "observed", "expected", "pull", "")]) for i in range(5)]
        fmt = "  ".join("{:<%d}" % x for x in w)
        print(fmt.format("check", "observed", "expected", "pull", ""))
        for r in self.rows:
            print(fmt.format(*r))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--ctau", type=float, help="nominal N2 c*tau in cm")
    ap.add_argument("--masses", help="MGO,MN2,MN1 in GeV")
    a = ap.parse_args()

    m = re.search(r"mGo-(\d+)_mN2-(\d+)_mN1-(\d+)_N2ctau-([0-9p]+)cm", a.files[0])
    mgo, mn2, mn1 = map(float, a.masses.split(",")) if a.masses else map(float, m.groups()[:3])
    ctau = a.ctau if a.ctau is not None else float(m.group(4).replace("p", "."))
    onshell_z = mn2 - mn1 > 91.188 + 10

    h = Handle("std::vector<reco::GenParticle>")
    nev = bad_mult = 0
    flav = {q: 0 for q in (1, 2, 3, 4, 5)}
    nglu_dec = 0
    n2_modes = {"gamma": 0, "Z": 0, "other": 0}
    ev_combo = {}
    z_modes, z_masses = {}, []
    ct = []
    masses = {GLUINO: [], N2: [], N1: []}
    others = {}

    # One chain over all files: an Events object per file is never freed, and a few hundred of
    # them exhaust memory (std::bad_alloc in ChainEvent).
    for e in Events(a.files):
        nev += 1
        e.getByLabel("prunedGenParticles", h)
        gp = h.product()
        glu = [p for p in gp if abs(p.pdgId()) == GLUINO and p.isLastCopy()]
        n2s = [p for p in gp if p.pdgId() == N2 and p.isLastCopy()]
        if len(glu) != 2 or len(n2s) != 2:
            bad_mult += 1
        for g in glu:
            masses[GLUINO].append(g.mass())
            qs = [abs(g.daughter(i).pdgId()) for i in range(g.numberOfDaughters())
                  if abs(g.daughter(i).pdgId()) < 10]
            if len(qs) == 2 and qs[0] == qs[1] and qs[0] in flav:
                flav[qs[0]] += 1
                nglu_dec += 1
            else:
                others[("gluino", tuple(sorted(qs)))] = others.get(("gluino", tuple(sorted(qs))), 0) + 1
        ev_modes = []
        for n2 in n2s:
            ev_modes.append(sorted(n2.daughter(i).pdgId() for i in range(n2.numberOfDaughters())))
        if len(ev_modes) == 2:
            ng = sum(m == [PHOTON, N1] for m in ev_modes)
            nz_ev = sum(m == [Z, N1] for m in ev_modes)
            key = {(2, 0): "gamma gamma", (1, 1): "gamma Z", (0, 2): "Z Z"}.get((ng, nz_ev), "other")
            ev_combo[key] = ev_combo.get(key, 0) + 1
        for n2 in n2s:
            masses[N2].append(n2.mass())
            dau = [n2.daughter(i) for i in range(n2.numberOfDaughters())]
            ids = sorted(d.pdgId() for d in dau)
            n1 = [d for d in dau if d.pdgId() == N1]
            if ids == [PHOTON, N1]:
                n2_modes["gamma"] += 1
            elif ids == [Z, N1]:
                n2_modes["Z"] += 1
                z = last_copy([d for d in dau if d.pdgId() == Z][0])
                z_masses.append(z.mass())
                mode = z_mode(z)
                z_modes[mode] = z_modes.get(mode, 0) + 1
            else:
                n2_modes["other"] += 1
                others[("N2", tuple(ids))] = others.get(("N2", tuple(ids)), 0) + 1
            if n1:
                masses[N1].append(n1[0].mass())
                d = n1[0]
                L = math.sqrt((d.vx() - n2.vx()) ** 2 + (d.vy() - n2.vy()) ** 2 + (d.vz() - n2.vz()) ** 2)
                ct.append(L * n2.mass() / n2.p())

    print(f"files: {len(a.files)}   events: {nev}   point: mGo={mgo:.0f} mN2={mn2:.0f} mN1={mn1:.0f}  "
          f"ctau={ctau:g} cm   Z {'on' if onshell_z else 'off'}-shell")
    c = Check()
    c.exact("2 gluinos and 2 N2 per event", bad_mult == 0, f"{nev - bad_mult}/{nev} events")
    for name, pdg, expect in (("gluino mass", GLUINO, mgo), ("N2 mass", N2, mn2), ("N1 mass", N1, mn1)):
        v = masses[pdg]
        mean = sum(v) / len(v) if v else float("nan")
        c.exact(name, bool(v) and abs(mean - expect) < 0.01 * expect, f"{mean:.1f} GeV (expected {expect:.0f})")
    for q, qn in zip((1, 2, 3, 4, 5), "dusc b".replace(" ", "")):
        c.frac(f"BR(gluino -> {qn} {qn}bar N2)", flav[q], nglu_dec, 0.2)
    n2tot = sum(n2_modes.values())
    c.frac("BR(N2 -> N1 gamma)", n2_modes["gamma"], n2tot, 0.5)
    c.frac("BR(N2 -> N1 Z)", n2_modes["Z"], n2tot, 0.5)
    c.exact("N2 decays other than N1 gamma / N1 Z", n2_modes["other"] == 0, str(n2_modes["other"]))
    # the two N2 decay independently: gamma gamma : gamma Z : Z Z = 1 : 2 : 1
    nc = sum(ev_combo.values())
    for key, expect in (("gamma gamma", 0.25), ("gamma Z", 0.5), ("Z Z", 0.25)):
        c.frac(f"events with N2 pair -> {key}", ev_combo.get(key, 0), nc, expect)
    nz = sum(z_modes.values())
    if onshell_z:
        for mode, br in Z_BR.items():
            c.frac(f"BR(Z -> {mode})", z_modes.get(mode, 0), nz, br)
    c.exact("Z decays of unknown type", not any(k.startswith("other") for k in z_modes),
            ", ".join(f"{k}: {v}" for k, v in z_modes.items() if k.startswith("other")) or "none")
    if ct:
        mean = sum(ct) / len(ct)
        c.value("N2 <c*t> (exponential mean)", mean, mean / math.sqrt(len(ct)), ctau, "cm")
        c.frac("fraction of N2 with c*t > ctau", sum(x > ctau for x in ct), len(ct), math.exp(-1))
    c.show()

    if not onshell_z and z_modes:
        print("\nZ* (off-shell) decays, no reference BRs:", {k: f"{v}/{nz}" for k, v in sorted(z_modes.items())})
    if z_masses:
        zs = sorted(z_masses)
        print(f"Z mass: median {zs[len(zs) // 2]:.1f} GeV, range {zs[0]:.1f}-{zs[-1]:.1f} GeV ({len(zs)} Z)")
    if others:
        print("unexpected decays:", others)
    print("\nRESULT:", "PASS" if c.failed == 0 else f"FAIL ({c.failed} checks)")
    return 1 if c.failed else 0


if __name__ == "__main__":
    sys.exit(main())
