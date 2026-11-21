"""Sample sets: the input of the pipeline (tmc.py, ntuple.py), independent of what is generated.

A sample set is a Python file, by default samples/t5zg.py; another one is chosen with
`--samples FILE` or $TMC_SAMPLES.  It defines

    OUT_LFN_BASE   output directory of its MiniAOD (and ntuples), /store/user/...
    VERSION        production round, in request names and dataset tags ("v1")
    samples()      [Sample(...)], see below
    WORK           (optional) work directory; default work/<file name without .py>
    NTUPLE_MODEL   (optional) model in the ntuple collection name (kucmsntuple_SMS_<model>_...);
                   ntuple.py refuses a set without it

A Sample is what the pipeline needs to know about a sample, and nothing about its physics:

    dataset        CRAB primary dataset: the sample's directory under OUT_LFN_BASE
    chain          key of chains.CHAINS
    fragment_name  file name (without .py) the fragment is installed as in Configuration/GenProduction/python
    fragment_text  the complete fragment
    era            free label, used by ntuple.py for the global tag ("2018", "2022", "2024")
    total_events   LHE events of the whole task (default: the chain's)

Its work name is <dataset>_<chain tag>, e.g. T5Zg_..._2022_FullMini.  Names must be unique in a set.
"""
import importlib.util
import os
import re
import sys

import chains as C

ROOT = os.path.dirname(os.path.realpath(__file__))
DEFAULT = os.path.join(ROOT, "samples", "t5zg.py")


class Sample:
    __slots__ = ("dataset", "chain", "fragment_name", "fragment_text", "era", "total_events", "name")

    def __init__(self, dataset, chain, fragment_name, fragment_text, era, total_events=None):
        if chain not in C.CHAINS:
            raise ValueError(f"{dataset}: unknown chain {chain!r}; known: {list(C.CHAINS)}")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", dataset):
            raise ValueError(f"bad dataset name {dataset!r}")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", fragment_name):   # a file name: cmsDriver takes its path
            raise ValueError(f"{dataset}: bad fragment name {fragment_name!r}")
        self.dataset, self.chain, self.era = dataset, chain, era
        self.fragment_name, self.fragment_text = fragment_name, fragment_text
        self.total_events = total_events or C.CHAINS[chain]["total_events"]
        self.name = f"{dataset}_{C.CHAINS[chain]['tag']}"

    def __repr__(self):
        return f"Sample({self.name})"


class SampleSet:
    def __init__(self, path, module):
        self.path = path
        self.label = os.path.splitext(os.path.basename(path))[0]
        for key in ("OUT_LFN_BASE", "VERSION", "samples"):
            if not hasattr(module, key):
                sys.exit(f"{path}: sample set has no {key} (see sampleset.py)")
        self.out_lfn_base = module.OUT_LFN_BASE.rstrip("/")
        if not self.out_lfn_base.startswith("/store/user/"):
            sys.exit(f"{path}: OUT_LFN_BASE must be under /store/user/, got {self.out_lfn_base}")
        self.version = module.VERSION
        self.work = getattr(module, "WORK", None) or os.path.join(ROOT, "work", self.label)
        self.ntuple_model = getattr(module, "NTUPLE_MODEL", None)
        self.samples = list(module.samples())
        names = [s.name for s in self.samples]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            sys.exit(f"{path}: duplicate sample names {dup}")
        frags = {}
        for s in self.samples:   # one fragment name, one text (they share a release directory)
            if frags.setdefault(s.fragment_name, s.fragment_text) != s.fragment_text:
                sys.exit(f"{path}: fragment {s.fragment_name} has two different texts")

    def chains(self):
        """Chains used by the set, in order of first use."""
        return list(dict.fromkeys(s.chain for s in self.samples))


def resolve(path=None):
    """--samples FILE, else $TMC_SAMPLES, else samples/t5zg.py; relative to the current directory,
    else to this repository."""
    path = path or os.environ.get("TMC_SAMPLES") or DEFAULT
    if not os.path.isabs(path) and not os.path.exists(path):
        path = os.path.join(ROOT, path)
    if not os.path.exists(path):
        sys.exit(f"sample set {path} not found")
    return os.path.realpath(path)


def load(path=None):
    path = resolve(path)
    spec = importlib.util.spec_from_file_location(f"sampleset_{os.path.basename(path)[:-3]}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return SampleSet(path, module)
