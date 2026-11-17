MASS_Go = XXXX   # GeV
MASS_N2 = YYYY  # GeV
MASS_N1 = ZZZZ  # GeV
CTAU_N2 = TTTT #cm
SLHA_TABLE="""
BLOCK MASS  # Mass Spectrum
# PDG code           mass       particle
   1000001     1.00000000E+05   # ~d_L
   2000001     1.00000000E+05   # ~d_R
   1000002     1.00000000E+05   # ~u_L
   2000002     1.00000000E+05   # ~u_R
   1000003     1.00000000E+05   # ~s_L
   2000003     1.00000000E+05   # ~s_R
   1000004     1.00000000E+05   # ~c_L
   2000004     1.00000000E+05   # ~c_R
   1000005     1.00000000E+05   # ~b_1
   2000005     1.00000000E+05   # ~b_2
   1000006     1.00000000E+05   # ~t_1
   2000006     1.00000000E+05   # ~t_2
   1000011     1.00000000E+05   # ~e_L
   2000011     1.00000000E+05   # ~e_R
   1000012     1.00000000E+05   # ~nu_eL
   1000013     1.00000000E+05   # ~mu_L
   2000013     1.00000000E+05   # ~mu_R
   1000014     1.00000000E+05   # ~nu_muL
   1000015     1.00000000E+05   # ~tau_1
   2000015     1.00000000E+05   # ~tau_2
   1000016     1.00000000E+05   # ~nu_tauL
   1000021     %MGO%            # ~g
   1000022     %MN1%            # ~chi_10
   1000023     %MN2%            # ~chi_20
   1000025     1.00000000E+05   # ~chi_30
   1000035     1.00000000E+05   # ~chi_40
   1000024     1.00000000E+05   # ~chi_1+
   1000037     1.00000000E+05   # ~chi_2+

# DECAY TABLE
#         PDG            Width
DECAY   1000001     0.00000000E+00   # sdown_L decays
DECAY   2000001     0.00000000E+00   # sdown_R decays
DECAY   1000002     0.00000000E+00   # sup_L decays
DECAY   2000002     0.00000000E+00   # sup_R decays
DECAY   1000003     0.00000000E+00   # sstrange_L decays
DECAY   2000003     0.00000000E+00   # sstrange_R decays
DECAY   1000004     0.00000000E+00   # scharm_L decays
DECAY   2000004     0.00000000E+00   # scharm_R decays
DECAY   1000005     0.00000000E+00   # sbottom1 decays
DECAY   2000005     0.00000000E+00   # sbottom2 decays
DECAY   1000006     0.00000000E+00   # stop1 decays
DECAY   2000006     0.00000000E+00   # stop2 decays
DECAY   1000011     0.00000000E+00   # selectron_L decays
DECAY   2000011     0.00000000E+00   # selectron_R decays
DECAY   1000012     0.0000000E+00    # snu_elL decays
DECAY   1000013     0.00000000E+00   # smuon_L decays
DECAY   2000013     0.00000000E+00   # smuon_R decays
DECAY   1000014     0.00000000E+00   # snu_muL decays
DECAY   1000015     0.00000000E+00   # stau_1 decays
DECAY   2000015     0.00000000E+00   # stau_2 decays
DECAY   1000016     0.00000000E+00   # snu_tauL decays
DECAY   1000021     5.506754   # gluino decays
    2.00000000E-01   3     -1      1       1000023
    2.00000000E-01   3     -2      2       1000023
    2.00000000E-01   3     -3      3       1000023
    2.00000000E-01   3     -4      4       1000023
    2.00000000E-01   3     -5      5       1000023
DECAY   1000022     0.00000000E+00   # neutralino1 decays
DECAY   1000023     %WIDTH%   # neutralino2 decays
    5.00000000E-01      2    1000022    22   # BR(~chi_20 -> ~chi_10 a)
    5.00000000E-01      2    1000022    23   # BR(~chi_20 -> ~chi_10 Z)
    0.00000000E-00      3    1000022    -11     11  #Dummy 3bd decay for offshell Z
DECAY   1000024     0.00000000E+00   # chargino1+ decays
DECAY   1000025     0.00000000E+00   # neutralino3 decays
DECAY   1000035     0.00000000E+00   # neutralino4 decays
DECAY   1000037     0.00000000E+00   # chargino2+ decays
"""

import FWCore.ParameterSet.Config as cms

from Configuration.Generator.Pythia8CommonSettings_cfi import *
from Configuration.Generator.MCTunesRun3ECM13p6TeV.PythiaCP5Settings_cfi import *
from Configuration.Generator.PSweightsPythia.PythiaPSweightsSettings_cfi import *

Nevents = 100
externalLHEProducer = cms.EDProducer('ExternalLHEProducer',
    args = cms.vstring('root://cmseos.fnal.gov//store/user/janguian/gridpacks_central/T5Zg_mGo-XXXX_el8_amd64_gcc12_CMSSW_14_0_18_tarball.tar.xz'),
    nEvents = cms.untracked.uint32(Nevents),
    numberOfParameters = cms.uint32(1),
    outputFile = cms.string('cmsgrid_final.lhe'),
    scriptName = cms.FileInPath('GeneratorInterface/LHEInterface/data/run_generic_tarball_xrootd.sh'),
    generateConcurrently = cms.untracked.bool(False)
)




def matchParams(mass):
    if   mass<799: return 118., 0.235
    elif mass<999: return 128., 0.235
    elif mass<1199: return 140., 0.235
    elif mass<1399: return 143., 0.245
    elif mass<1499: return 147., 0.255
    elif mass<1799: return 150., 0.267
    elif mass<2099: return 156., 0.290 
    elif mass<2301: return 160., 0.315 
    elif mass<2601: return 162., 0.340
    elif mass<2851: return 168, 0.364
    else: return 168., 0.364


model = "T5Zg"
hBarCinGeVmm = 1.973269788e-13

N2width = hBarCinGeVmm / (CTAU_N2*10.)
qcut, tru_eff = matchParams(MASS_Go)



slhatable = SLHA_TABLE.replace('%MGO%','%e' % MASS_Go)
slhatable = slhatable.replace('%MN2%','%e' % MASS_N2)
slhatable = slhatable.replace('%MN1%', '%e' % MASS_N1)
slhatable = slhatable.replace('%WIDTH%','%e' % N2width)

basePythiaParameters = cms.PSet(
     pythia8CommonSettingsBlock,
     pythia8CP5SettingsBlock,
     pythia8PSweightsSettingsBlock,
     processParameters = cms.vstring(
          'JetMatching:setMad = off',
            'JetMatching:scheme = 1',
            'JetMatching:merge = on',
            'JetMatching:jetAlgorithm = 2',
            'JetMatching:etaJetMax = 5.',
            'JetMatching:coneRadius = 1.',
            'JetMatching:slowJetPower = 1',
            'JetMatching:qCut = %.0f' % qcut,  #this is the actual merging scale
            'JetMatching:nQmatch = 5', #4 corresponds to 4-flavour scheme (no matching of b-quarks), 5 for 5-flavour scheme
            'JetMatching:nJetMax = 2', #number of partons in born matrix element for highest multiplicity
            'JetMatching:doShowerKt = off', #off for MLM matching, turn on for shower-kT matching
            '23:mMin = 0.1',
            'Check:abortIfVeto = on',

     ),
     parameterSets = cms.vstring('pythia8CommonSettings',
                                 'pythia8CP5Settings',
                                 'pythia8PSweightsSettings',
                                 'processParameters'
     ))

basePythiaParameters.pythia8CommonSettings.extend(['1000023:tau0 = %e' % (CTAU_N2*10.)])
#basePythiaParameters.pythia8CommonSettings.extend(['ParticleDecays:tau0Max = 10000.1'])
basePythiaParameters.pythia8CommonSettings.extend(['ParticleDecays:limitTau0 = off'])
basePythiaParameters.pythia8CommonSettings.extend(['LesHouches:setLifetime = 2'])

generator = cms.EDFilter("Pythia8ConcurrentHadronizerFilter",
     maxEventsToPrint = cms.untracked.int32(1),
     pythiaPylistVerbosity = cms.untracked.int32(1),
     filterEfficiency = cms.untracked.double(1.0),
     pythiaHepMCVerbosity = cms.untracked.bool(False),
     comEnergy = cms.double(13600),
     SLHATableForPythia8 = cms.string('%s' % slhatable),
     PythiaParameters = basePythiaParameters,
)


