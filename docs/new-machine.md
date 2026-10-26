# Setting up `assystant` on a new machine

Three files, three concerns. Keep them apart and a campaign moves between
clusters by changing one of them:

| file | what it holds | lives |
|---|---|---|
| **campaign YAML** (`camp.yaml`) | the physics: `system`, `incar`, `incar_static`, `magmom`, `assyst`, `filters` | with the project, under version control, identical on every machine |
| **site YAML** (`site_<cluster>.yaml`) | this machine: `slurm`, `vasp`, perhaps `root` | one per cluster; passed with `--site` |
| **POTCAR config** (`pyiron_vasp_config`) | where the POTCAR library is, which set and functional | once per cluster; named by `vasp.potcar_config` in the site file |

Every transcript below was executed as written; only absolute paths were
shortened.

## 1. Install

```bash
python -m pip install assystant          # Python 3.11-3.14; venv / uv / conda / pixi all work
assystant --help
```

The environment must be reachable **from the compute nodes**, because every
job runs `<slurm.env>/bin/python -m assystant.runner`. On a cluster where login
and compute nodes share the filesystem that is automatic; `slurm.env` is only
needed when the jobs must use a different prefix than the shell you submit
from. Details and the per-manager commands are in
[`installation.md`](installation.md).

## 2. The POTCAR library

`assystant` never names a POTCAR; `pyiron_workflow_vasp` resolves one per
element from a small `key = value` file:

```ini
# /sw/potcars/pyiron_vasp_config
default_POTCAR_set = potpaw64
default_functional = GGA
vasp_POTCAR_path_potpaw64 = /sw/potcars/potpaw_64
vasp_POTCAR_path_potpaw54 = /sw/potcars/potpaw_54
```

`default_POTCAR_set` must match the suffix of one `vasp_POTCAR_path_<suffix>`
key; each such directory holds `GGA/` and `LDA/`, each with one directory per
POTCAR variant (`Fe/`, `Ti_sv/`, `Cr_pv/`, …) containing a `POTCAR`.

Write the file once per cluster and name it in the site file:

```yaml
vasp:
  potcar_config: /sw/potcars/pyiron_vasp_config
```

That pins it: the rendered job script exports `PYIRON_VASP_CONFIG` and the
in-job runner sets the same variable before the library is imported, so the
campaign no longer depends on `~/.pyiron_vasp_config` or on what the shell you
submitted from had exported. (Leave it `null` and the library falls back to
`$PYIRON_VASP_CONFIG`, then `~/.pyiron_vasp_config` — fine on a machine you
alone use, fragile everywhere else.)

**Check it resolves for every element in the campaign** before submitting
anything. A missing POTCAR fails only inside the allocation, after queueing:

```bash
PYIRON_VASP_CONFIG=/sw/potcars/pyiron_vasp_config python -c "
from ase import Atoms
from pyiron_workflow_vasp.vasp import (
    _default_POTCAR_library_path, _default_functional, get_default_POTCAR_paths)
lib, func = _default_POTCAR_library_path(), _default_functional()
print(lib, func)
for el in ('Fe', 'Mn', 'C'):                      # every element of every system
    print(el, get_default_POTCAR_paths(Atoms(el), lib, func))
"
```

Note the default selection prefers semicore variants (`Ti` → `Ti_sv`), and
that choice feeds the core-overlap filter — see
[`behaviour-changes.md`](behaviour-changes.md#rcore-is-read-from-the-potcar-actually-used).

## 3. VASP: binary, modules, launcher, custodian

```yaml
vasp:
  binary: /sw/vasp/6.4.3/bin/vasp_std        # as the COMPUTE NODES see it
  modules: [intel/2024.0, impi/2021.11, mkl/2024.0]   # what makes its libraries resolvable; [] if none
  launcher: null                             # null -> srun -c 1 -n {ntasks} --hint=nomultithread
  custodian: true
  custodian_python: /sw/envs/pymatgen/bin/python     # an interpreter with `custodian` importable
```

* **`binary`** must be an absolute path. `${VAR}` and `~` are expanded (see
  [section 5](#5-one-site-file-per-machine)).
* **`modules`** are emitted as `module load` lines after a `module purge`.
* **`launcher`** replaces `srun` where needed: `"mpirun -n {ntasks}"` on a
  cluster whose MPI is not SLURM-integrated, `""` to invoke the binary bare, or
  `"env I_MPI_FABRICS=shm"` for a login-node smoke test — see
  [`operations.md`](operations.md#vasplauncher-and-running-outside-an-allocation).
* **`custodian`** drives VASP through error handlers that a campaign of random
  cells needs. `custodian_python` is frequently *not* the runner environment
  (the runner does not need custodian; the custodian env does not need
  `pyiron_workflow`); `null` falls back to `slurm.env`. Neither is validated at
  submit time.

## 4. SLURM

```yaml
slurm:
  partition: compute
  account: proj1234            # only if your site requires --account
  ntasks: 64
  cpus_per_task: 1
  time: "48:00:00"
  mem_per_cpu: 2000MB
  env: ~/mambaforge/envs/assystant      # prefix with assystant, as seen from compute nodes; null -> this interpreter
  extra_directives: ["--constraint=rome"]   # verbatim extra #SBATCH lines
```

`slurm` is the one section the [staleness guard](operations.md#the-staleness-guard)
ignores, so changing partition, wall time or task count never forces
`--from-scratch`.

## 5. One site file per machine

`assystant example site` writes a site file with `"<EDIT ...>"` placeholders
for exactly the values that differ between machines:

```
$ assystant example
AlCu_tutorial  Multi-element tutorial campaign: unary Al, unary Cu and the Al-Cu binary in one file, cubic spacegroups, minutes of DFT.
Al_tutorial    Unary Al tutorial campaign: 1-4 atoms, cubic spacegroups, minutes of DFT.
site           Site file: everything about ONE machine -- scheduler, VASP, POTCARs -- kept apart from the physics; pass it with --site.

$ assystant example site -o site_mycluster.yaml
wrote site_mycluster.yaml

$ assystant example AlCu_tutorial -o camp.yaml
wrote camp.yaml
```

`--site` merges the file over the campaign YAML before anything else happens:
nested mappings merge key by key, scalars and lists are replaced, and the
precedence is **CLI flag > site file(s) > campaign YAML > defaults** (later
`--site` files win over earlier ones). A real `run` refuses while any
placeholder is left, from either file:

```
$ assystant run camp.yaml --site site_mycluster.yaml
error: placeholder value(s) still set at: slurm.partition, vasp.binary, vasp.potcar_config. Edit them in the campaign YAML (or override them, e.g. --partition / --vasp-binary) before a real `run`; `run --dry-run` works without them.
```

Filled in (comments stripped), using an environment variable for the VASP
root so the same site file follows a module system that moves it:

```yaml
slurm:
  partition: compute
vasp:
  binary: ${VASP_ROOT}/bin/vasp_std
  modules: [intel/2024.0, impi/2021.11, mkl/2024.0]
  potcar_config: /scratch/demo/sw/potcars/pyiron_vasp_config
  custodian: true
```

`${VAR}`, `$VAR` and a leading `~` are expanded in `root`, `slurm` and `vasp`
(not in the physics sections). An unset variable is an error naming the key,
not an empty string that would only fail inside the allocation — and since
`gen` resolves the whole config too, it is caught at the very first command:

```
$ assystant gen camp.yaml --site site_mycluster.yaml --no-progress
error: `vasp.binary` references environment variable ${VASP_ROOT}, which is not set in this shell. Export it, or write the value out.

$ export VASP_ROOT=/sw/vasp/6.4.3
$ assystant gen camp.yaml --site site_mycluster.yaml --no-progress
Al: 43/144 (spacegroup, composition) pairs produced a structure; rejected: Comp_CompatibilityError x101
Cu: 43/144 (spacegroup, composition) pairs produced a structure; rejected: Comp_CompatibilityError x101
Al_Cu: 15/216 (spacegroup, composition) pairs produced a structure; rejected: Comp_CompatibilityError x201
Al: 43 structures -> /scratch/demo/AlCu_campaign/Al
Cu: 43 structures -> /scratch/demo/AlCu_campaign/Cu
Al_Cu: 15 structures -> /scratch/demo/AlCu_campaign/Al_Cu

$ assystant run camp.yaml --site site_mycluster.yaml --dry-run --limit 1
rendered 1, skipped 0 queued / 0 finished
```

The rendered script carries the machine: the pinned POTCAR config, and the
expanded binary inside the recorded config it points at:

```
$ sed -n '/module purge/,$p' AlCu_campaign/Al/struct_00000_*/assystant_job.sh
module purge

# The POTCAR library pyiron_workflow_vasp reads, pinned by vasp.potcar_config
# so the job does not depend on the submitting shell or ~/.pyiron_vasp_config.
export PYIRON_VASP_CONFIG=/scratch/demo/sw/potcars/pyiron_vasp_config

# Run the ASSYST graph for exactly this structure. run_dir is absolute;
# executorlib's cache lives at <run_dir>/executorlib_cache so it is
# scoped to one structure and cannot be shared between two.
/scratch/demo/env/bin/python -m assystant.runner --config /scratch/demo/AlCu_campaign/assystant_config.yaml --poscar /scratch/demo/AlCu_campaign/Al/structures/00000_structure.vasp --run-dir /scratch/demo/AlCu_campaign/Al/struct_00000_36c5d68a --system Al

$ grep -E "binary|potcar_config" AlCu_campaign/assystant_config.yaml
  binary: /sw/vasp/6.4.3/bin/vasp_std
  potcar_config: /scratch/demo/sw/potcars/pyiron_vasp_config
```

Two consequences of recording the *expanded* values. The provenance file says
which binary actually ran, not `${VASP_ROOT}`. And `vasp` participates in the
staleness guard, so one campaign directory belongs to one machine: the same
`root` reached from a second cluster with a different `vasp.binary` is refused
as stale, correctly, since a different binary can produce different numbers.
Use a different `root` per machine (the site file is a natural place for it)
and concatenate at `collect` time.

A typical layout, then:

```
project/
  camp_FeMnC.yaml              # physics, under version control
  site_cluster_a.yaml          # slurm + vasp + potcar_config + root for cluster A
  site_cluster_b.yaml          # same for cluster B
```

```bash
assystant run camp_FeMnC.yaml --site site_cluster_a.yaml           # on A
assystant run camp_FeMnC.yaml --site site_cluster_b.yaml           # on B
```

Everything else — `--partition`, `--vasp-binary`, `--env`,
`--set vasp.modules="[]"` — still works on top for one-off deviations.

## 6. Magnetic moments per element

Only consulted with `incar.ISPIN: 2`. Two layers:

```yaml
magmom:
  host: null             # null -> the first species of each system
  host_moment: 3.0       # the host's initial moment
  solute_moment: -1.0    # every other element's, unless listed below
  moments:               # per-element overrides, applied in every system
    Mn: -2.0
    C: 0.0
```

`host` / `host_moment` / `solute_moment` is the historical rule: one moment for
the matrix, one for everything else, with the matrix being the first species
of each system unless `host` is set. `moments` is what makes a multi-element
campaign express different moments for different solutes — an element listed
there gets that value wherever it appears, one not listed falls back to the
rule. For an `Fe Fe Mn C` cell:

```
(no moments)              -> MAGMOM = [3.0, 3.0, -1.0, -1.0]
moments: {Mn: -2.0, C: 0.0} -> MAGMOM = [3.0, 3.0, -2.0,  0.0]
```

A campaign over several magnetic matrices needs no `host` at all: put the
matrix first in each inner list and list the solutes in `moments`:

```yaml
system:
  species: [[Fe], [Fe, Mn], [Fe, C], [Ni], [Ni, Mn]]
magmom:
  host: null                     # Fe hosts the Fe systems, Ni the Ni systems
  moments: {Fe: 3.0, Ni: 0.6, Mn: -2.0, C: 0.0}   # explicit for every element -> host/solute rule never used
```

`MAGMOM` is written positionally from the structure's site order, with
consecutive runs of one species collapsed (`2*3.0 1*-2.0 1*0.0`). The statics
INCAR gets the same treatment as the relaxation INCAR, including when
`incar_static` switches `ISPIN` on.

## 7. Smoke test on the login node

Before the first real submission, prove that *this machine's* binary, modules
and POTCARs work together — in under a minute, without the scheduler.
[`examples/Fe_smoke.yaml`](../examples/Fe_smoke.yaml) is a deliberately
non-physical two-atom campaign built for exactly this; with a site file it
needs only the launcher swapped for something that runs outside an
allocation:

```bash
assystant gen Fe_smoke.yaml --site site_mycluster.yaml --no-progress --root ./smoke
assystant run Fe_smoke.yaml --site site_mycluster.yaml --root ./smoke --dry-run --limit 1 \
    --set vasp.custodian=false --set vasp.launcher="mpirun -n 1"
bash smoke/Fe/struct_00000_*/assystant_job.sh        # runs the chain right here
```

It ends with `ASSYST finished: 4 rows -> .../df_ASSYST_jobs.pkl`. What to do
when it does not is in
[`operations.md`](operations.md#smoke-testing-without-a-scheduler) and
[`troubleshooting.md`](troubleshooting.md).

## 8. The first real campaign

```bash
assystant gen    camp.yaml --site site_mycluster.yaml --no-progress
assystant run    camp.yaml --site site_mycluster.yaml --dry-run --limit 1   # read the script
assystant run    camp.yaml --site site_mycluster.yaml --limit 1             # one job; wait for df_ASSYST_jobs.pkl
assystant run    camp.yaml --site site_mycluster.yaml                       # the rest; re-runnable, resumes
assystant status camp.yaml --site site_mycluster.yaml --watch 300
assystant collect camp.yaml --site site_mycluster.yaml -o train.pkl
```

Pass the site file to every command: `gen` and `collect` only need `root`
from it, but a consistent command line is cheaper than remembering which
commands do not.

## Checklist

* [ ] `pip install assystant` in an environment the compute nodes can run
* [ ] `pyiron_vasp_config` written; the resolution snippet prints a POTCAR for every element
* [ ] `assystant example site -o site_<cluster>.yaml`; no `<EDIT` left: `partition`, `binary`, `modules`, `potcar_config`, `custodian_python` if `custodian: true`
* [ ] `run --dry-run --limit 1`; read the script: partition, binary, modules, `export PYIRON_VASP_CONFIG`
* [ ] smoke test finishes on the login node
* [ ] `run --limit 1`; the job produces `df_ASSYST_jobs.pkl`
* [ ] then everything else
