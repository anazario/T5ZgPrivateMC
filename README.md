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
  He has no slc7 gridpack for mGo = 1800, so we built one (genproductions `mg265UL`, his 2300 slc7
  cards with only the gluino mass changed) at `/store/user/aabreuna/gridpacks/`; `GRIDPACK_OVERRIDE`
  in `production.py` points the 2018 mGo-1800 fragments to it.

## Usage (cmslpc-el9)

```bash
cd ~/nobackup && git clone <this repo> T5ZgPrivateMC && cd T5ZgPrivateMC
voms-proxy-init -voms cms -valid 192:00
./tmc.py list
./tmc.py setup                      # creates releases/ (7 releases), installs fragments, scram b
./tmc.py configs <sample>           # cmsDriver for every step -> work/<sample>/
./tmc.py test <sample> -n 40        # whole chain locally: time, memory, size, provenance check
./tmc.py submit <sample> [--pilot 5] --dryrun   # CRAB dry run; then ./tmc.py proceed <sample> [--pilot]
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

Physics checks on the MiniAOD (run in any python3 FWLite release, e.g. CMSSW_13_0_13):

* `validate/gencheck.py <files>`: 2 gluinos and 2 N2 per event, masses, gluino -> q qbar N2
  flavours, N2 -> N1 γ / N1 Z at 50/50 (1:2:1 per event), Z decays vs PDG, and the N2 proper decay
  length vs cτ. Masses and cτ are read from the file path.
* `validate/pucheck.py <files from different jobs>`: the in-time pileup of event i must differ
  between jobs.

## How a CRAB job runs (`job/run_chain.sh`)

* Step 1 is CRAB's `PSet.py`; CRAB sets its seeds, event count and lumi. The job log prints them.
* Every later step gets fresh random seeds (`RandomNumberServiceHelper.populate()`), also printed.
  Without this, steps 2+ ran with the fixed default seeds in every job, so premix DIGI overlaid the
  same pileup on event i of every FullSim job. Seeds are untracked, so PSetIDs are unchanged.
* Steps in other releases set their release up from CVMFS inside the job.
* Step 1's outputs stay on the worker node: CRAB's wrapper checksums them and runs `edmProvDump` on
  them after the chain. Later intermediates are deleted. Only `MiniAOD.root` is staged out.
* 2018 FullSim SIM skips an event on a Geant4 stuck track (`SkipEvent = EventCorruption`): in
  CMSSW_10_6 any stuck track aborts the whole job.

## Pilot results (Nov 2026, high-Δm 20 cm, 5 jobs per chain)

| Chain | Cores / memory requested | Peak memory | Run time per job | MiniAOD events per job |
|---|---|---|---|---|
| 2022 FullSim | 2 / 5000 MB | 3.7–4.0 GB | 1.8–3.1 h | 221–243 |
| 2018 FullSim | 1 / 3000 MB | 2.2–2.5 GB | ~3.2 h (SIM 2.1 h) | 237 |
| 2022 FastSim | 4 / 10000 MB | 8.5–8.8 GB | 15–35 min | 402–442 |
| 2018 FastSim | 4 / 7000 MB | 4.4–4.8 GB | 30–70 min | 454 |

All outputs pass `gencheck.py`; the 2022 and 2018 FullSim jobs pass `pucheck.py` (all 5 jobs each). Stage-out to T3_US_FNALLPC failed
(`copy HTTP 500`, exit 60328) for every job that ran at Nebraska, Caltech or Florida, so those sites
are in `SITE_BLACKLIST`. CRAB does not apply a blacklist to sites matched by a whitelist wildcard
(wave 1 still ran 343 jobs there), so every chain uses an explicit list, `SITES_US`,
`SITES_US_NO_MIT` (2018 FastSim) or `SITES_WORLD`, and `tmc.py` refuses wildcards and blacklisted
sites. Failed jobs are recovered with `crab resubmit`.

## 2018 crashes (wave 1, Nov 2026)

* **xrootd fork crash (exit 50117), fixed:** cmsRun forks every few minutes (CondorStatusService
  runs `condor_chirp`), and xrootd's fork handler then closes every connection. In the xrootd 5.5.4
  of CMSSW_10_6_35_patch1 (2018 FastSim, remote premix) this races with a TLS handshake and
  segfaults in `SSL_write (s=0x0)`. 46 of 47 such crashes came within 5 s of a teardown.
  `run_chain.sh` sets `XRD_RUNFORKHANDLER=0`. The 2018 FullSim premix step uses xrootd 4.8.5,
  which has no TLS client.
* **Random segfaults at MIT, not understood:** crashes at a different place in CMSSW each time,
  22 of 25 at MIT on 21 nodes, also with the xrootd fix. 2018 FastSim no longer runs at MIT.

## Ntuples (`ntuple.py`)

KUCMSNtupleizer (`test/llpgana_miniaod.py`) runs over the MiniAOD in its own CMSSW_15_1_0_patch5 area
(`/uscms_data/d3/anazario/YOURWORKINGAREA`). `cmsenv` there, source `crab-setup.sh`, then from this
directory:

```
./ntuple.py status [samples]             # MiniAOD finished / submitted / to submit, ntuple job states
./ntuple.py submit [--dryrun] [samples]  # submit finished MiniAOD files not yet submitted
./ntuple.py proceed [samples]            # continue a --dryrun batch
```

* **Whole samples:** `submit` skips a sample until its MiniAOD task is complete (`--partial`
  overrides this), so each sample's ntuples come from one task.
* **Batches:** each `submit` sends a sample's MiniAOD files not yet submitted as a new CRAB task
  (`..._b1`, `_b2`, …). `work/<sample>/ntuple/ledger.json` lists the files of every batch, so no
  file is submitted twice or skipped. After `crab kill`, `./ntuple.py killed <sample> <request>`
  marks the batch killed, and its files count as not submitted. File lists come from the MiniAOD task's finished
  jobs, not from EOS (EOS can hold files of jobs CRAB still counts as running).
* **Output:** next to the MiniAOD, `<sample>/KUCMSNtuple_SVHPM100_v38_<Fast|Full>Mini_v1/<timestamp>/`,
  one timestamp per batch. `MiniAOD_N.root` holds exactly lumi N, so `crab report` shows which
  MiniAOD files a batch processed.
* **Options:** `multicrab=True hasGenInfo=True eventFilter=SVHPMet100`, plus
  `106X_upgrade2018_realistic_v11_L1v1 / Run2_2018` or `124X_mcRun3_2022_realistic_v12 / Run3_2022`,
  plus `doFastSim=True` for FastSim. 10 MiniAOD files per job. Sites: `SITES_US` (FNALLPC alone
  ran no jobs in an hour; the T2s read the MiniAOD over xrootd).
* **Sandbox:** the area also holds HiggsCombine, CombineHarvester and LLPCombine
  (`HiggsAnalysis/CombinedLimit/data` alone is 313 MB), which pushed the sandbox over CRAB's limit.
  `ntuple.py` swaps CRAB's tar filter (`UserTarball.excludeFromTar`) for one that also drops those
  packages: 2.6 MB, nothing deleted from the area. Each submission checks the tarball.
* **Local test (Nov 2026):** all 4 chains on 2 full MiniAOD files each: exit 0, 3–7 min and
  1.3–1.6 GB per file, `nTotEvts` equal to the input events.
