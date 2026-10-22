# Installation

## The short version

```bash
python -m pip install assystant
assystant --help
```

in any fresh Python 3.11–3.13 environment — see
[below](#building-an-environment-from-scratch) for venv, uv, conda and pixi, and
for the [pre-release caveat](#before-the-dependencies-are-on-pypi).

On the MPIE cluster an environment already exists and is the one every example
in these docs was executed in:

```
/cmmc/ptmp/hmai/mambaforge/envs/assystant
```

Python 3.12.13, `pyiron_workflow` 0.19.0, `flowrep` 0.6.2, `pymatgen-core`
2026.7.27, `pyxtal` 1.1.4, `ase` 3.29.0. If you are on that cluster, use it and
skip to [`worked-example.md`](worked-example.md).

Nothing in `assystant` requires the environment to be *active*: every path it
renders into a job script is absolute, and the sbatch script invokes
`<slurm.env>/bin/python` explicitly. Calling the entry point by absolute path
works fine and is what the examples do:

```bash
/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/assystant --help
```

## Building an environment from scratch

`assystant` is a normal Python package. In a fresh environment with Python 3.11,
3.12 or 3.13, one command installs it and everything it needs:

```bash
python -m pip install assystant
```

No `--no-build-isolation`, no install order, nothing to pre-install. Pick
whichever environment manager you already use:

```bash
# venv
python3.12 -m venv assystant-env
assystant-env/bin/python -m pip install assystant

# uv
uv venv -p 3.12 assystant-env
uv pip install -p assystant-env/bin/python assystant

# conda / mamba (Python from conda-forge, the rest from PyPI)
mamba create -n assystant -c conda-forge python=3.12 pip
mamba run -n assystant python -m pip install assystant

# pixi
pixi init assystant && cd assystant
pixi add python=3.12
pixi add --pypi assystant
```

Python 3.13 needs `pyiron_workflow_vasp` 0.2.0 or later, the first release
declaring `requires-python <3.14`; with 0.1.x `pip` refuses 3.13.

What gets pulled in:

| package | why |
|---|---|
| `pyiron_workflow` >= 0.19, < 0.21 | the graph runtime |
| `pyiron_workflow_assyst` >= 0.2, < 0.3 | the ASSYST graph and structure filters |
| `pyiron_workflow_vasp` (via `pyiron_workflow_assyst`) | VASP input writing and output parsing |
| `pyxtal`, `pymatgen`, `numpy`, `pandas`, `PyYAML`, `tqdm` | structure generation and bookkeeping |

The CLI is then on `PATH` of that environment:

```bash
assystant --help
assystant gen     camp.yaml              # PyXtal scan -> POSCARs + manifest
assystant run     camp.yaml --dry-run    # render the job scripts without submitting
assystant run     camp.yaml              # submit to SLURM
assystant status  camp.yaml
assystant collect camp.yaml -o train.pkl
```

`gen` (and `run --dry-run`) work anywhere. Actually running DFT needs three
things on top of the Python install: a SLURM cluster to submit to (`slurm:` in
the YAML; `slurm.env` must be this environment, as seen from the compute
nodes), a VASP binary (`vasp:`, [below](#vasp-itself)), and a POTCAR
configuration file in `$PYIRON_VASP_CONFIG` or `~/.pyiron_vasp_config`
([below](#the-potcar-library)). Each job runs
`<slurm.env>/bin/python -m assystant.runner ...`.

### Before the dependencies are on PyPI

`pyiron_workflow_vasp` 0.2.0 and `pyiron_workflow_assyst` 0.2.0 — the releases
carrying the `pyiron_workflow` 0.19 port — are not on PyPI yet, so until they
are, `pip install assystant` cannot resolve. Put their 0.2.0 wheels (built with
`python -m build` from each repo's `0.2.0` release tag — a plain checkout of
`main` reports a `0.1.0.post…` version that does not satisfy `>=0.2.0`) in a
directory and point the installer at it:

```bash
python -m pip install --find-links wheelhouse/ assystant
# or
uv pip install --find-links wheelhouse/ assystant
```

Once both are released this section goes away.

## The POTCAR library

`assystant` never names a POTCAR. `pyiron_workflow_vasp` resolves them from a
configuration file, read from `$PYIRON_VASP_CONFIG` if set and otherwise from
`~/.pyiron_vasp_config`. Without it, every VASP job in the campaign fails at
input-writing time.

The file is `key = value` lines, `#` for comments:

```ini
default_POTCAR_set = potpaw64
default_functional = GGA
pyiron_vasp_resources = /cmmc/u/hmai/pyiron-resources-cmmc/vasp/potentials/pyiron_nodes/

vasp_POTCAR_path_potpaw64 = {pyiron_vasp_resources}/potpaw_64
vasp_POTCAR_path_potpaw54 = {pyiron_vasp_resources}/potpaw_54
vasp_POTCAR_path_potpaw52 = {pyiron_vasp_resources}/potpaw_52
```

`default_POTCAR_set` must match the suffix of one of the
`vasp_POTCAR_path_<suffix>` keys. Each of those directories must contain `GGA/`
and `LDA/` subdirectories, each holding one directory per POTCAR variant
(`Fe/`, `Ti_sv/`, `Cr_pv/`, …) with a `POTCAR` inside.

Check that it resolves before launching anything:

```bash
python -c "
from ase import Atoms
from pyiron_workflow_vasp.vasp import (
    _default_POTCAR_library_path, _default_functional, get_default_POTCAR_paths)
lib, func = _default_POTCAR_library_path(), _default_functional()
print(lib, func)
for el in ('Fe', 'Ti'):
    print(el, get_default_POTCAR_paths(Atoms(el), lib, func))
"
```

which on the reference setup prints:

```
/cmmc/u/hmai/pyiron-resources-cmmc/vasp/potentials/pyiron_nodes/potpaw_64 GGA
Fe ['.../potpaw_64/GGA/Fe/POTCAR']
Ti ['.../potpaw_64/GGA/Ti_sv/POTCAR']
```

Note that `Ti` resolves to `Ti_sv`, not `Ti`. The default selection prefers
semicore variants for most transition metals, and that choice propagates into the
core-overlap filter — see
[`behaviour-changes.md`](behaviour-changes.md#rcore-is-read-from-the-potcar-actually-used).

## VASP itself

`vasp.binary` in the campaign YAML must point at a VASP executable the compute
nodes can run, and `vasp.modules` must list the modules that make its runtime
libraries resolvable. The reference campaign uses

```yaml
vasp:
  binary: /cmmc/ptmp/hmai/vasp_compiled/intel_suite/vasp.6.4.3_intelsuite_march_znver4/bin/vasp_std
  modules: [intel/2024.0, impi/2021.11, mkl/2024.0]
```

With `vasp.custodian: true` (the default) the job additionally needs `custodian`
importable by `vasp.custodian_python` — an interpreter that may differ from
`slurm.env`, because the runner environment does not need custodian and the
custodian environment does not need `pyiron_workflow`. This is not validated at
submit time, since the check would have to run inside the allocation; a wrong
value surfaces as a failed job rather than a failed submission.

## Verifying the install

```bash
python -m pip install "assystant[test]"
pytest tests/ -q          # from a checkout, or any copy of its tests/ directory
```

108 tests, none of which touch SLURM or VASP. For an end-to-end check that does
run real DFT — without a scheduler, on a login node, in a couple of minutes — see
the smoke test in [`operations.md`](operations.md#smoke-testing-without-a-scheduler).
