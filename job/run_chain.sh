#!/bin/bash
# CRAB scriptExe: run the whole GEN -> MiniAOD chain of one sample inside one job.
#
# Step 1 is CRAB's own PSet.py (psetName with per-job seeds, event count and first lumi
# applied by CRAB).  The remaining steps are listed in chain.txt, one per line:
#     <step> <release> <scram_arch> <cfg> <input file to delete afterwards>
# Steps in another release set that release up from CVMFS on the worker node.
# Only MiniAOD.root is staged out (config.JobType.outputFiles).

set -o pipefail
TOP=$PWD
JOB=$1
echo "=== run_chain.sh: job ${JOB} on $(hostname) at $(date)"
echo "=== sandbox release ${CMSSW_VERSION} (${SCRAM_ARCH})"
cat chain.txt

stamp() { date +%s; }

t0=$(stamp)
echo "=== step 1 (PSet.py, ${CMSSW_VERSION}) starting at $(date)"
cmsRun -j FrameworkJobReport.xml -p PSet.py
rc=$?
echo "=== step 1 exit ${rc} after $(( $(stamp) - t0 )) s"
[ ${rc} -ne 0 ] && exit ${rc}
rm -f step1_inLHE.root   # LHE copy written by the RAWSIM,LHE event content; not needed

while read -r n rel arch cfg consumed; do
  [ -z "${n}" ] && continue
  [ "${n}" = "1" ] && continue
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
  rm -f "${consumed}"
done < chain.txt

# CRAB's wrapper (CMSRunAnalysis.py AddChecksums) stats and checksums every output file
# listed in FrameworkJobReport.xml, i.e. step 1's outputs, which were deleted above.
# A missing one fails the job with 50115 after the chain succeeded, so leave empty
# placeholders.  They are not staged out (disableAutomaticOutputCollection).
# Take only the first PFN of each <File> block (the file itself; later PFNs are its inputs).
for f in $(awk '/<File>/{o=1} o && /<PFN>/{print; o=0}' FrameworkJobReport.xml \
             | sed -e 's:.*<PFN>\(.*\)</PFN>.*:\1:' -e 's:^file\:::'); do
  [ -e "${f}" ] || { : > "${f}"; echo "=== placeholder for deleted step-1 output ${f}"; }
done

if [ ! -s MiniAOD.root ]; then
  echo "=== MiniAOD.root missing or empty"
  exit 90
fi
ls -l MiniAOD.root
echo "=== done at $(date)"
exit 0
