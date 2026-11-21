#!/bin/bash
# CRAB scriptExe: run the whole GEN -> MiniAOD chain of one sample inside one job.
#
# Step 1 is CRAB's own PSet.py (psetName with per-job seeds, event count and first lumi
# applied by CRAB; in recovery tasks also recover.txt, see below).  The remaining steps are
# listed in chain.txt, one per line:
#     <step> <release> <scram_arch> <cfg> <input file to delete afterwards, unless step 1 wrote it>
# Steps in another release set that release up from CVMFS on the worker node.
# Only MiniAOD.root is staged out (config.JobType.outputFiles).

set -o pipefail

# xrootd's fork handler closes every open connection whenever cmsRun forks, and CMSSW forks
# every few minutes (CondorStatusService spawning condor_chirp).  In the xrootd 5.5.4 of
# CMSSW_10_6_35_patch1 this races with a connection that is mid-TLS-handshake: SSL_write on a
# freed SSL object, a segfault (exit 50117 / 5) in 2018 FastSim, which reads remote premix.
# The forked children only exec, so they need no xrootd state.  Verified in 10_6_35_patch1:
# with 0 the prepare handler returns without stopping anything and open files stay readable.
export XRD_RUNFORKHANDLER=0
TOP=$PWD
JOB=$1
echo "=== run_chain.sh: job ${JOB} on $(hostname) at $(date)"
echo "=== sandbox release ${CMSSW_VERSION} (${SCRAM_ARCH})"
cat chain.txt

stamp() { date +%s; }

# Recovery tasks (tmc.py recover) ship recover.txt, lines "<crab job> <original job> <events per
# job>".  The job then numbers its lumi and events as CRAB numbers the original job (lumi N, events
# (N-1)*E+1 ...), so its MiniAOD can replace the lost file.  Both parameters are untracked: the
# PSetIDs are unchanged.  Regular tasks don't ship recover.txt.
if [ -f recover.txt ]; then
  read -r ORIG EPJ <<< "$(awk -v j="${JOB}" '$1 == j {print $2, $3}' recover.txt)"
  if [ -z "${ORIG}" ] || [ -z "${EPJ}" ]; then
    echo "=== recover.txt has no entry for job ${JOB}"
    exit 95
  fi
  FIRST=$(( (ORIG - 1) * EPJ + 1 ))
  cat >> PSet.py <<PY

# --- run_chain.sh: recovery job ${JOB} stands in for original job ${ORIG}
import FWCore.ParameterSet.Config as cms
for _name, _value in (("firstLuminosityBlock", ${ORIG}), ("firstEvent", ${FIRST})):
    if hasattr(process.source, _name):
        getattr(process.source, _name).setValue(_value)
    else:
        setattr(process.source, _name, cms.untracked.uint32(_value))
PY
  echo "=== recovery job ${JOB}: numbered as original job ${ORIG} (lumi ${ORIG}, first event ${FIRST})"
fi

# Log the seeds CRAB put into PSet.py (it doesn't), so that a failed job can be reproduced.
# 10_X releases have python 2 only; a system python3 there can't import FWCore and falls through.
for py in python3 python; do
  ${py} -c '
from PSet import process
_rs = process.RandomNumberGeneratorService
print("=== step 1 random seeds: " + " ".join("%s=%s" % (_n, getattr(_rs, _n).initialSeed.value())
      for _n in sorted(_rs.parameterNames_()) if hasattr(getattr(_rs, _n), "initialSeed")))
' 2>/dev/null && break
done

t0=$(stamp)
echo "=== step 1 (PSet.py, ${CMSSW_VERSION}) starting at $(date)"
# positional config: cmsRun of CMSSW_14_0 and later no longer has -p (10_6 - 13_0 accept both)
cmsRun -j FrameworkJobReport.xml PSet.py
rc=$?
echo "=== step 1 exit ${rc} after $(( $(stamp) - t0 )) s"
[ ${rc} -ne 0 ] && exit ${rc}

# CRAB's wrapper (CMSRunAnalysis.py) post-processes every output file listed in
# FrameworkJobReport.xml, i.e. step 1's outputs: it stats and checksums them and, when the
# report has no PSet hash, runs edmProvDump on them.  A missing file (50115) or an empty
# placeholder (56) fails the job after the chain succeeded, so step 1's outputs are kept.
# They are not staged out (disableAutomaticOutputCollection).  Take only the first PFN of
# each <File> block (the file itself; later PFNs are its inputs).
KEEP=$(awk '/<File>/{o=1} o && /<PFN>/{print; o=0}' FrameworkJobReport.xml \
         | sed -e 's:.*<PFN>\(.*\)</PFN>.*:\1:' -e 's:^file\:::')
echo "=== keeping step-1 outputs for CRAB: ${KEEP}"

while read -r n rel arch cfg consumed; do
  [ -z "${n}" ] && continue
  [ "${n}" = "1" ] && continue
  # CRAB randomizes the seeds of step 1 (PSet.py) only; later steps would run with the fixed
  # default seeds in every job, e.g. the same premix pileup events for event i of every job.
  # Give each step fresh random seeds, as CRAB does for step 1 (seeds are untracked: PSetIDs
  # are unchanged).  Works with the python 2 and 3 of all releases used.
  seeded="${cfg%.py}_seeded.py"
  cp "${cfg}" "${seeded}"
  cat >> "${seeded}" <<'PY'

from IOMC.RandomEngine.RandomServiceHelper import RandomNumberServiceHelper
RandomNumberServiceHelper(process.RandomNumberGeneratorService).populate()
_rs = process.RandomNumberGeneratorService
print("=== random seeds: " + " ".join("%s=%s" % (_n, getattr(_rs, _n).initialSeed.value())
      for _n in sorted(_rs.parameterNames_()) if hasattr(getattr(_rs, _n), "initialSeed")))
PY
  cfg="${seeded}"
  t0=$(stamp)
  echo "=== step ${n} (${cfg}, ${rel}) starting at $(date)"
  if [ "${rel}" = "${CMSSW_VERSION}" ]; then
    cmsRun -j "fjr_step${n}.xml" "${cfg}"
    rc=$?
  else
    (
      export SCRAM_ARCH="${arch}"
      if [ ! -d "${rel}" ]; then
        scram -a "${arch}" project "${rel}" > /dev/null || exit 80
      fi
      cd "${rel}/src" && eval "$(scram runtime -sh)" && cd "${TOP}" || exit 81
      cmsRun -j "fjr_step${n}.xml" "${cfg}"
    )
    rc=$?
  fi
  echo "=== step ${n} exit ${rc} after $(( $(stamp) - t0 )) s"
  [ ${rc} -ne 0 ] && exit ${rc}
  case " ${KEEP//$'\n'/ } " in
    *" ${consumed} "*) ;;          # a step-1 output: CRAB still needs it
    *) rm -f "${consumed}" ;;
  esac
done < chain.txt

if [ ! -s MiniAOD.root ]; then
  echo "=== MiniAOD.root missing or empty"
  exit 90
fi
ls -l MiniAOD.root
echo "=== done at $(date)"
exit 0
