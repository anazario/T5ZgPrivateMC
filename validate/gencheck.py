#!/usr/bin/env python3
"""Generator-level validation of T5Zg and T5HH MiniAOD (prunedGenParticles), FWLite.

    python3 validate/gencheck.py MiniAOD_1.root [MiniAOD_2.root ...]  [--ctau CM] [--masses MGO,MN2,MN1]
                                                                       [--model T5Zg|T5HH] [--label L]

Model, masses and cτ are taken from the file path (<model>_mGo-..._mN2-..._mN1-..._N2ctau-...cm)
unless given.  Run inside any cmsenv with python3 FWLite (e.g. CMSSW_13_0_13; reads the 10_6
MiniAOD as well).

Checks, against the fragments (fragments/Template_<model>_*_fragment.py):
  * every event has 2 gluinos and 2 N2; gluino -> q qbar N2 with q = d,u,s,c,b at 20% each
  * T5Zg: N2 -> N1 gamma : N1 Z = 50 : 50, independently for the two N2 (gamma gamma : gamma Z : Z Z
    = 1 : 2 : 1); the Z (on-shell point) decays with Pythia's branching ratios
  * T5HH: N2 -> N1 h always; the h (on-shell point) decays with Pythia's SM branching ratios.  At an
    off-shell point (m(N2) - m(N1) < m(h)) Pythia recomputes them at the h* mass: printed, not tested
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

GLUINO, N2, N1, PHOTON, Z, H = 1000021, 1000023, 1000022, 22, 23, 25
# PDG Z branching fractions (Pythia 8's defaults agree at the per-mille level)
Z_BR = {"hadrons": 0.6991, "ee": 0.0336, "mumu": 0.0336, "tautau": 0.0336, "invisible": 0.2001}
# Pythia's h0 branching ratios at 125 GeV, identical in 8.240 (CMSSW_10_6_17_patch1, 2018) and 8.306
# (CMSSW_12_4_14_patch3, 2022): Init:showOneParticleData = 25 in the T5HH GEN test (Nov 2026).  Within
# 3% of the LHC Higgs WG YR4 values, but with ~10^5 h per sample only Pythia's own table is exact.
H_BR = {"bb": 0.5772683, "WW": 0.2154067, "gg": 0.0850944, "tautau": 0.0628984, "cc": 0.0288684,
        "ZZ": 0.0261137, "gamgam": 0.0022980, "Zgam": 0.0016166, "mumu": 0.0002186, "ss": 0.0002157,
        "uu/dd": 0.0000011}
M_Z, M_H = 91.188, 125.0
# N2 decay modes of each model: name -> (boson pdgId, BR), as in the fragment's DECAY 1000023 block
MODELS = {
    "T5Zg": {"gamma": (PHOTON, 0.5), "Z": (Z, 0.5)},
    "T5HH": {"h": (H, 1.0)},
}


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


def h_mode(h):
    ids = sorted(abs(h.daughter(i).pdgId()) for i in range(h.numberOfDaughters()))
    mode = {(5, 5): "bb", (24, 24): "WW", (21, 21): "gg", (15, 15): "tautau", (4, 4): "cc",
            (23, 23): "ZZ", (22, 22): "gamgam", (22, 23): "Zgam", (13, 13): "mumu", (3, 3): "ss",
            (1, 1): "uu/dd", (2, 2): "uu/dd", (11, 11): "ee"}.get(tuple(ids))
    return mode or "other:" + ",".join(map(str, ids))


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
    ap.add_argument("--model", choices=sorted(MODELS), help="default: from the file path")
    ap.add_argument("--label", default="prunedGenParticles",
                    help="GenParticle collection (genParticles for GEN/GEN-SIM files)")
    a = ap.parse_args()

    m = re.search(r"mGo-(\d+)_mN2-(\d+)_mN1-(\d+)_N2ctau-([0-9p]+)cm", a.files[0])
    if not m and not (a.masses and a.ctau is not None):
        sys.exit("masses and ctau not in the file path: give --masses and --ctau")
    mgo, mn2, mn1 = map(float, a.masses.split(",")) if a.masses else map(float, m.groups()[:3])
    ctau = a.ctau if a.ctau is not None else float(m.group(4).replace("p", "."))
    model = a.model or next((k for k in MODELS if k + "_mGo-" in a.files[0]), None)
    if not model:
        sys.exit(f"model not in the file path ({'/'.join(MODELS)}_mGo-...): give --model")
    n2_def = MODELS[model]                     # mode name -> (boson, BR)
    by_boson = {pdg: name for name, (pdg, _) in n2_def.items()}
    boson_mass = {Z: M_Z, H: M_H}
    onshell = {pdg: mn2 - mn1 > boson_mass[pdg] + 10 for pdg in by_boson if pdg in boson_mass}

    h = Handle("std::vector<reco::GenParticle>")
    nev = bad_mult = 0
    flav = {q: 0 for q in (1, 2, 3, 4, 5)}
    nglu_dec = 0
    n2_modes = dict.fromkeys(list(n2_def) + ["other"], 0)
    ev_combo = {}
    bos_modes = {pdg: {} for pdg in boson_mass if pdg in by_boson}   # Z or h decay modes
    bos_masses = {pdg: [] for pdg in bos_modes}
    ct = []
    masses = {GLUINO: [], N2: [], N1: []}
    others = {}

    # One chain over all files: an Events object per file is never freed, and a few hundred of
    # them exhaust memory (std::bad_alloc in ChainEvent).
    for e in Events(a.files):
        nev += 1
        e.getByLabel(a.label, h)
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
            masses[N2].append(n2.mass())
            dau = [n2.daughter(i) for i in range(n2.numberOfDaughters())]
            ids = sorted(d.pdgId() for d in dau)
            n1 = [d for d in dau if d.pdgId() == N1]
            boson = next((pdg for pdg in by_boson if ids == sorted([pdg, N1])), None)
            if boson is None:
                n2_modes["other"] += 1
                others[("N2", tuple(ids))] = others.get(("N2", tuple(ids)), 0) + 1
                ev_modes.append("other")
            else:
                n2_modes[by_boson[boson]] += 1
                ev_modes.append(by_boson[boson])
                if boson in bos_modes:
                    b = last_copy([d for d in dau if d.pdgId() == boson][0])
                    bos_masses[boson].append(b.mass())
                    mode = z_mode(b) if boson == Z else h_mode(b)
                    bos_modes[boson][mode] = bos_modes[boson].get(mode, 0) + 1
            if n1:
                masses[N1].append(n1[0].mass())
                d = n1[0]
                L = math.sqrt((d.vx() - n2.vx()) ** 2 + (d.vy() - n2.vy()) ** 2 + (d.vz() - n2.vz()) ** 2)
                ct.append(L * n2.mass() / n2.p())
        if len(ev_modes) == 2:
            key = " ".join(sorted(ev_modes))
            ev_combo[key] = ev_combo.get(key, 0) + 1

    shell = ", ".join(f"{by_boson[pdg]} {'on' if on else 'off'}-shell" for pdg, on in onshell.items())
    print(f"files: {len(a.files)}   events: {nev}   model: {model}   point: mGo={mgo:.0f} mN2={mn2:.0f} "
          f"mN1={mn1:.0f}  ctau={ctau:g} cm   {shell}")
    c = Check()
    c.exact("2 gluinos and 2 N2 per event", bad_mult == 0, f"{nev - bad_mult}/{nev} events")
    for name, pdg, expect in (("gluino mass", GLUINO, mgo), ("N2 mass", N2, mn2), ("N1 mass", N1, mn1)):
        v = masses[pdg]
        mean = sum(v) / len(v) if v else float("nan")
        c.exact(name, bool(v) and abs(mean - expect) < 0.01 * expect, f"{mean:.1f} GeV (expected {expect:.0f})")
    for q, qn in zip((1, 2, 3, 4, 5), "dusc b".replace(" ", "")):
        c.frac(f"BR(gluino -> {qn} {qn}bar N2)", flav[q], nglu_dec, 0.2)
    n2tot = sum(n2_modes.values())
    for name, (_, br) in n2_def.items():
        c.frac(f"BR(N2 -> N1 {name})", n2_modes[name], n2tot, br)
    c.exact(f"N2 decays other than {' / '.join('N1 ' + k for k in n2_def)}", n2_modes["other"] == 0,
            str(n2_modes["other"]))
    if len(n2_def) > 1:   # the two N2 decay independently, e.g. gamma gamma : gamma Z : Z Z = 1 : 2 : 1
        nc = sum(ev_combo.values())
        names = sorted(n2_def)
        for i, x in enumerate(names):
            for y in names[i:]:
                expect = n2_def[x][1] * n2_def[y][1] * (1 if x == y else 2)
                c.frac(f"events with N2 pair -> {x} {y}", ev_combo.get(f"{x} {y}", 0), nc, expect)
    for pdg, modes in bos_modes.items():
        name, ref = by_boson[pdg], (Z_BR if pdg == Z else H_BR)
        if onshell[pdg]:
            nb = sum(modes.values())
            for mode, br in ref.items():
                c.frac(f"BR({name} -> {mode})", modes.get(mode, 0), nb, br)
        c.exact(f"{name} decays of unknown type", not any(k.startswith("other") for k in modes),
                ", ".join(f"{k}: {v}" for k, v in modes.items() if k.startswith("other")) or "none")
    if ct:
        mean = sum(ct) / len(ct)
        c.value("N2 <c*t> (exponential mean)", mean, mean / math.sqrt(len(ct)), ctau, "cm")
        c.frac("fraction of N2 with c*t > ctau", sum(x > ctau for x in ct), len(ct), math.exp(-1))
    c.show()

    for pdg, modes in bos_modes.items():
        name, nb = by_boson[pdg], sum(modes.values())
        if not onshell[pdg] and modes:
            print(f"\n{name}* (off-shell) decays, no reference BRs:",
                  {k: f"{v}/{nb}" for k, v in sorted(modes.items(), key=lambda kv: -kv[1])})
        if bos_masses[pdg]:
            ms = sorted(bos_masses[pdg])
            print(f"{name} mass: median {ms[len(ms) // 2]:.1f} GeV, 5-95% {ms[len(ms) // 20]:.1f}-"
                  f"{ms[len(ms) * 19 // 20]:.1f} GeV, range {ms[0]:.1f}-{ms[-1]:.1f} GeV ({len(ms)} {name})")
    if others:
        print("unexpected decays:", others)
    print("\nRESULT:", "PASS" if c.failed == 0 else f"FAIL ({c.failed} checks)")
    return 1 if c.failed else 0


if __name__ == "__main__":
    sys.exit(main())
