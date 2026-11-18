# T5ZgPrivateMC

Private FullSim and FastSim MiniAOD for the T5Zg signal (gluino pair → q q̄ χ̃⁰₂, χ̃⁰₂ → χ̃⁰₁ γ/Z with
BR 0.5/0.5), made with the same recipes as the existing KUCMS T5Zg samples. Each sample is one CRAB
task, and each job runs the whole chain from GEN to MiniAOD on the worker node. Only `MiniAOD.root` is
staged out.

## Samples (`production.py`)

| Point (mGo, mN2, mN1) | cτ(χ̃⁰₂) | 2018 FullSim | 2018 FastSim | 2022 FullSim | 2022 FastSim |
|---|---|---|---|---|---|
| 1800, 1750, 1700 (compressed) | 0.1, 20, 300 cm | new | new | redo | new |
| 2300, 1200, 1000 (high Δm) | 0.1, 20 cm | new | new | new | new |
| 2300, 1200, 1000 (high Δm) | 300 cm | exists | new | redo | new |

That's 11 FullSim tasks of 375 jobs and 12 FastSim tasks of 188 jobs. Each sample requests 300k LHE
events; MLM matching keeps about 28% (~83k), the same as the existing samples.

**Why the 2022 FullSim samples are redone:** the existing 2022 FullSim MiniAOD (T5Zg and gogoGZ) was
made with `--fast` in the PAT step. Its PAT PSetID `d5d37242…` is identical to the FastSim MiniAOD's.
With `--fast`, the beam-halo MET filters become `HLTBool(True)`, `slimmedDisplacedMuons` is missing,
and patMuons trigger matching, patJets tag infos and isolatedTracks dE/dx are switched off. Here the
2022 FullSim PAT step runs without `--fast`. All other steps are unchanged.

## Recipes

Copied from the CRAB debug configs of the existing productions and checked against the processing
history stored in the existing MiniAOD (`edmProvDump`):

* **2022 FullSim:** LHE,GEN,SIM → DIGI,DATAMIX,L1,DIGI2RAW,HLT:2022v12 (Summer22 premix) →
  RAW2DIGI,L1Reco,RECO,RECOSIM, all in CMSSW_12_4_14_patch3 with `124X_mcRun3_2022_realistic_v12`;
  then PAT in CMSSW_13_0_13 with `130X_mcRun3_2022_realistic_v5`.
* **2022 FastSim:** a single LHE→RECO FastSim step in CMSSW_12_4_14_patch3, reading Justin's PUlib1
  premix library from cmseos; then PAT `--fast` in CMSSW_13_0_13.
* **2018 FullSim (UL):** LHE,GEN → SIM → DIGI premix (UL18) → RECO (+EI) in CMSSW_10_6_17_patch1,
  with HLT:2018v32 in CMSSW_10_2_16_UL in between; then PAT (MiniAODv2) in CMSSW_10_6_27.
* **2018 FastSim (UL):** a single LHE→RECO FastSim step in CMSSW_10_6_35_patch1 with
  `106X_upgrade2018_realistic_v16`, then PAT (MiniAODv2) `--fast` in CMSSW_10_6_29, as the central
  `SMS-T5Wg` RunIISummer20UL18 FastSim (from its provenance). Premix: 100 files (520k events) of the
  central UL18 FastSim library that are pinned on disk at FNAL and CERN, listed in
  `premix/UL18_FastSim_disk.txt`. The rest of that library is only on tape, so `dbs:` can't be used.
* Gridpacks (gluino mass only): `/store/user/janguian/gridpacks_central/T5Zg_mGo-*` (el8 / 14_0_18
  for 2022, slc7 / 10_6_44 for 2018). Fragments come from janguian's T5Zg templates in `fragments/`.

## Usage (cmslpc-el9)

```bash
cd ~/nobackup && git clone <this repo> T5ZgPrivateMC && cd T5ZgPrivateMC
voms-proxy-init -voms cms -valid 192:00
./tmc.py list
./tmc.py setup                      # creates releases/ (7 releases), installs fragments, scram b
./tmc.py configs <sample>           # cmsDriver for every step -> work/<sample>/
./tmc.py test <sample> -n 40        # whole chain locally: time, memory, size, provenance check
./tmc.py submit <sample> --dryrun   # CRAB dry run, then without --dryrun for a pilot
./tmc.py status [<sample> ...]
```

`<sample>` can be any unique part of the name, e.g. `2300_mN2-1200_mN1-1000_N2ctau-20cm_2022_Full`.

## Validation

`./tmc.py check <MiniAOD> <chain>` compares the process PSetIDs in the file with
`expected_psetids` in `production.py`:

* 2022 FullSim: HLT and RECO must equal the existing samples, and PAT must equal central
  Run3Summer22MiniAODv4 FullSim (`5443ed9e…`), not the old `d5d37242…`.
* 2022 FastSim: PAT must equal the existing FastSim (`d5d37242…`).
* 2018 FullSim: SIM, DIGI2RAW, HLT, RECO and PAT must all equal the existing 2018 sample.
* 2018 FastSim: PAT must equal the central `SMS-T5Wg` UL18 FastSim MiniAODv2 (`1d31bd42…`).

GEN/SIM IDs that contain the generator config differ by mass point and lifetime. That is expected.

## Still to confirm in the CRAB pilot

* Switching releases inside a job (`scram project` + `scram runtime` in `run_chain.sh`).
* Step 1 writes `FrameworkJobReport.xml`, and `MiniAOD.root` is staged out as a non-EDM output file
  (`disableAutomaticOutputCollection`).
* Job length with 800 LHE events per job (FullSim) and 1600 (FastSim).
