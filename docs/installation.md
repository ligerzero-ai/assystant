# Installation

## The short version

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

`assystant` needs Python 3.11–3.13 (`pyiron_workflow` 0.19 supports no others)
and three libraries that are not on PyPI:

| package | source | why |
|---|---|---|
| `pyiron_workflow` >= 0.19, < 0.20 | PyPI | the graph runtime |
| `flowrep` >= 0.6, < 0.7 | PyPI | the graph description language 0.19 split out |
| `pyiron_workflow_vasp` | [ligerzero-ai/pyiron_workflow_vasp](https://github.com/ligerzero-ai/pyiron_workflow_vasp), branch `main` | VASP input writing and output parsing |
| `pyiron_workflow_assyst` >= 0.2 | [ligerzero-ai/pyiron_workflow_assyst](https://github.com/ligerzero-ai/pyiron_workflow_assyst), branch `main` | the ASSYST graph and structure filters |

Both `pyiron_workflow_*` ports to 0.19 are merged to `main` in their respective
repos. Neither has a release tag carrying the 0.19 work, which matters for the
install order below.

Create the environment with conda first, because the scientific stack needs to be
importable *at build time* for one of the packages:

```bash
mamba create -n assystant -c conda-forge python=3.12 \
    ase numpy scipy pandas scikit-learn pymatgen pyxtal pyyaml tqdm
mamba activate assystant
pip install "pyiron_workflow>=0.19,<0.20" flowrep
```

Then the three source packages, in this order:

```bash
git clone https://github.com/ligerzero-ai/pyiron_workflow_vasp
git clone https://github.com/ligerzero-ai/pyiron_workflow_assyst
git clone https://github.com/ligerzero-ai/assystant

pip install --no-build-isolation -e ./pyiron_workflow_vasp
pip install -e ./pyiron_workflow_assyst
pip install -e ./assystant
```

Two wrinkles, both verified, both harmless once you know about them:

* **`pyiron_workflow_vasp` needs `--no-build-isolation`.** Its build backend
  imports `ase` while generating requirements, so in pip's isolated build
  environment it fails with `ModuleNotFoundError: No module named 'ase'` before
  it ever gets to installing. Building against the conda environment, which
  already has `ase`, is what `--no-build-isolation` does. Do *not* pass the same
  flag to the other two: `pyiron_workflow_assyst` and `assystant` build with
  `hatchling`, which the isolated environment supplies and the conda environment
  does not (`BackendUnavailable: Cannot import 'hatchling.build'`).

* **`pip check` will complain about `pyiron_workflow_vasp`'s version.** Both
  `assystant` and `pyiron_workflow_assyst` declare `pyiron_workflow_vasp>=0.2.0`,
  but that repo derives its version from git tags via versioneer and its newest
  tag is `pyiron_workflow_vasp-0.1.0`, so a checkout of `main` reports something
  like `0.1.0.post0.dev17`:

  ```
  assystant 0.1.0 has requirement pyiron-workflow-vasp>=0.2.0,
      but you have pyiron-workflow-vasp 0.1.0.post0.dev2.
  ```

  The code on `main` *is* the 0.19-ported code the constraint is asking for; only
  the version string is behind. An editable install is unaffected. A non-editable
  `pip install git+https://...` of `assystant` would fail to resolve, which is why
  the recipe above installs the dependency from source first.

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
pytest tests/ -q
```

108 tests, none of which touch SLURM or VASP. For an end-to-end check that does
run real DFT — without a scheduler, on a login node, in a couple of minutes — see
the smoke test in [`operations.md`](operations.md#smoke-testing-without-a-scheduler).
