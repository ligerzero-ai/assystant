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

`assystant` never names a POTCAR. Each VASP calculation gets its POTCAR from
`pyiron_workflow_vasp`, and knowing exactly how that lookup works is what
makes a new machine's library configurable in five minutes instead of an
afternoon of `PotcarNotGeneratedError`.

### How `pyiron_workflow_vasp` resolves a POTCAR

1. It reads **one config file**: `$PYIRON_VASP_CONFIG` if that variable is
   set, else `~/.pyiron_vasp_config`. (`vasp.potcar_config` in the site file
   sets the variable for you — below.) The file is `key = value` lines; `#`
   starts a comment. It is read once, at import, and cached.
2. From it, it takes three keys:
   * `pyiron_vasp_resources` — a directory;
   * `default_POTCAR_set` — a name like `potpaw64`, `potpaw54`, `potpaw52`;
   * `default_functional` — `GGA` or `LDA` (`GGA` means the PBE `potpaw_PBE`
     potentials).
3. It builds the library directory as
   **`<pyiron_vasp_resources>/<set with an underscore before the digits>`**:
   `potpaw64` → `<pyiron_vasp_resources>/potpaw_64`. A key
   `vasp_POTCAR_path_potpaw64 = ...` must exist for the set to be recognised,
   but **its value is ignored** — the path always comes from
   `pyiron_vasp_resources` plus the set name. (This is confirmed below, not
   assumed.)
4. For every element in the structure it looks up the **default variant** in a
   table shipped with the package (`vasp_pseudopotential_GGA_data.csv`): `Fe`
   → `Fe`, but `Ti` → `Ti_sv`, `Mn` → `Mn_pv`, `Cr` → `Cr_pv`, `V` → `V_sv`,
   `Nb`/`Mo`/`W` → `_sv`, `Ce` → `Ce_3`, `Nd` → `Nd_3`. These are VASP's own
   recommended potentials. The alternative tables `GGA_MPRelaxSet` and
   `GGA_MatPES` (Materials Project choices: `Fe_pv`, `Ni_pv`, `Ti_pv`, …) are
   selected with a fourth key, `default_pseudopotential_set`.
5. The file it reads is
   **`<library>/<functional>/<variant>/POTCAR`**, e.g.
   `/sw/potcars/potpaw_64/GGA/Ti_sv/POTCAR`. Nothing else: no `.gz`, no
   `POTCAR.Z`, no flat `<library>/Ti_sv`.

So the directory layout has one level — the functional — that VASP's own
tarballs do not have. The distributed archives unpack to
`potpaw_PBE.64/Fe/POTCAR` and `potpaw_LDA.64/Fe/POTCAR`; the lookup wants
`potpaw_64/GGA/Fe/POTCAR` and `potpaw_64/LDA/Fe/POTCAR`. Two symlinks bridge
that.

### Worked example

Say VASP is installed at `/sw/vasp/6.4.3/bin/vasp_std` and the POTCAR
archives were unpacked under `/sw/potcars`:

```
/sw/potcars/potpaw_PBE.64/Fe/POTCAR      (and every other element directory)
/sw/potcars/potpaw_LDA.64/Fe/POTCAR
```

Create the layout the lookup expects, without copying anything:

```bash
mkdir -p /sw/potcars/potpaw_64
ln -s ../potpaw_PBE.64 /sw/potcars/potpaw_64/GGA
ln -s ../potpaw_LDA.64 /sw/potcars/potpaw_64/LDA
```

and write the config file next to it:

```ini
# /sw/potcars/pyiron_vasp_config
pyiron_vasp_resources = /sw/potcars
default_POTCAR_set = potpaw64          # -> /sw/potcars/potpaw_64
default_functional = GGA               # -> /sw/potcars/potpaw_64/GGA/<variant>/POTCAR
vasp_POTCAR_path_potpaw64 = unused     # the key must exist; the value is not read
# default_pseudopotential_set = GGA_MPRelaxSet   # optional: Materials Project variants instead
```

Then point the site file at both:

```yaml
vasp:
  binary: /sw/vasp/6.4.3/bin/vasp_std
  potcar_config: /sw/potcars/pyiron_vasp_config
```

Executed against exactly this layout (built from placeholder files; paths
shortened):

```
$ find sw -maxdepth 3 | sort
  sw
  sw/potpaw_64
  sw/potpaw_64/GGA          -> ../potpaw_PBE.64
  sw/potpaw_64/LDA          -> ../potpaw_LDA.64
  sw/potpaw_LDA.64
  sw/potpaw_LDA.64/Fe
  sw/potpaw_LDA.64/Fe/POTCAR
  sw/potpaw_PBE.64
  sw/potpaw_PBE.64/Al
  sw/potpaw_PBE.64/Al/POTCAR
  sw/potpaw_PBE.64/C
  sw/potpaw_PBE.64/C/POTCAR
  sw/potpaw_PBE.64/Fe
  sw/potpaw_PBE.64/Fe/POTCAR
  sw/potpaw_PBE.64/Mn_pv
  sw/potpaw_PBE.64/Mn_pv/POTCAR
  sw/potpaw_PBE.64/Ti_sv
  sw/potpaw_PBE.64/Ti_sv/POTCAR
  sw/pyiron_vasp_config

$ PYIRON_VASP_CONFIG=/scratch/demo/sw/pyiron_vasp_config python -c "
from ase import Atoms
from pyiron_workflow_vasp.vasp import (_default_POTCAR_library_path, _default_functional,
                                       get_default_POTCAR_paths, _get_potcar_config)
print('resolved config:', {k: v for k, v in _get_potcar_config().items() if k != 'pyiron_vasp_resources'})
lib, func = _default_POTCAR_library_path(), _default_functional()
import os
for el in ('Fe', 'Ti', 'Mn', 'C', 'Al'):
    p = get_default_POTCAR_paths(Atoms(el), lib, func)[0]
    print(f'{el:3s} -> {p}   exists={os.path.exists(p)}')
"
resolved config: {'default_POTCAR_set': 'potpaw64', 'default_functional': 'GGA', 'vasp_POTCAR_path_potpaw64': '/scratch/demo/sw/potpaw_64', 'default_POTCAR_path': '/scratch/demo/sw/potpaw_64', 'pseudopotential_csv_suffix': 'GGA'}
Fe  -> /scratch/demo/sw/potpaw_64/GGA/Fe/POTCAR   exists=True
Ti  -> /scratch/demo/sw/potpaw_64/GGA/Ti_sv/POTCAR   exists=True
Mn  -> /scratch/demo/sw/potpaw_64/GGA/Mn_pv/POTCAR   exists=True
C   -> /scratch/demo/sw/potpaw_64/GGA/C/POTCAR   exists=True
Al  -> /scratch/demo/sw/potpaw_64/GGA/Al/POTCAR   exists=True
```

Note `vasp_POTCAR_path_potpaw64` in the resolved config: the file said
`IGNORED-see-below`, the library says `/scratch/demo/sw/potpaw_64`. That is
step 3 above. Two more things the same script shows when the config is
changed:

```
# default_POTCAR_set = potpaw_64   (underscore in the set name)
ValueError: Unknown or missing default_POTCAR_set: potpaw_64. Valid options: ['potpaw64']

# default_pseudopotential_set = GGA_MPRelaxSet
Fe -> Fe_pv    Ti -> Ti_pv    Mn -> Mn_pv    Ni -> Ni_pv
```

Run that check with **every element of every system in the campaign** in the
tuple, and look at `exists=`. A `False` there is the only cheap warning you
get: a missing POTCAR otherwise fails inside the allocation, after queueing,
in `<run_dir>/assystant.err`. The variant choice also feeds the core-overlap
filter — see
[`behaviour-changes.md`](behaviour-changes.md#rcore-is-read-from-the-potcar-actually-used).

### What `vasp.potcar_config` does with it

```yaml
vasp:
  potcar_config: /sw/potcars/pyiron_vasp_config
```

The rendered job script gets `export PYIRON_VASP_CONFIG=/sw/potcars/pyiron_vasp_config`,
and the in-job runner sets the same variable before `pyiron_workflow_vasp` is
imported (it has to be *before*: the library reads the variable into a module
constant at import time). The campaign therefore no longer depends on
`~/.pyiron_vasp_config` or on what the shell you submitted from had exported.
Leave it `null` and the library's own lookup applies — fine on a machine you
alone use, fragile everywhere else.

## 3. How the job file is built

`assystant run` renders **one sbatch script per structure** and hands it to
`sbatch`. Knowing what goes where lets you adapt it to a GPU partition, a
different MPI, or a different launcher without guessing. Here is a script
rendered from a GPU site file (shown in the next section), with every line
annotated by the config key that produced it:

```bash
#!/bin/bash
#SBATCH --job-name=assyst_Al_00000                       # assyst_<system>_<index>
#SBATCH --partition=gpu                                  # slurm.partition
#SBATCH --ntasks=4                                       # slurm.ntasks
#SBATCH --cpus-per-task=16                               # slurm.cpus_per_task
#SBATCH --time=01:00:00                                  # slurm.time
#SBATCH --chdir=/scratch/demo/gpu_campaign/Al/struct_00000_36c5d68a   # the structure's run_dir (fixed)
#SBATCH --output=/scratch/demo/gpu_campaign/Al/struct_00000_36c5d68a/assystant.out
#SBATCH --error=/scratch/demo/gpu_campaign/Al/struct_00000_36c5d68a/assystant.err
#SBATCH --get-user-env                                   # fixed
#SBATCH --mem-per-cpu=8000MB                             # slurm.mem_per_cpu
#SBATCH --hint=nomultithread                             # fixed
#SBATCH --account=proj1234                               # slurm.account (only when set)
#SBATCH --gres=gpu:a100:4                                # slurm.extra_directives, verbatim
#SBATCH --constraint=a100                                #   "

module purge                                             # unless slurm.module_purge: false

source /etc/profile.d/modules.sh                         # slurm.prologue, verbatim
export OMP_NUM_THREADS=16                                #   "

# The POTCAR library pyiron_workflow_vasp reads, pinned by vasp.potcar_config
# so the job does not depend on the submitting shell or ~/.pyiron_vasp_config.
export PYIRON_VASP_CONFIG=/scratch/demo/sw/pyiron_vasp_config    # vasp.potcar_config

# Run the ASSYST graph for exactly this structure. run_dir is absolute;
# executorlib's cache lives at <run_dir>/executorlib_cache so it is
# scoped to one structure and cannot be shared between two.
/scratch/demo/env/bin/python -m assystant.runner \       # slurm.env/bin/python, or the interpreter running assystant
    --config /scratch/demo/gpu_campaign/assystant_config.yaml \
    --poscar /scratch/demo/gpu_campaign/Al/structures/00000_structure.vasp \
    --run-dir /scratch/demo/gpu_campaign/Al/struct_00000_36c5d68a \
    --system Al
```

(The runner line is one line in the real file.) `--chdir` is load-bearing,
not cosmetic: `run` skips a structure whose directory shows up in
`squeue -u $USER -o %Z`, which is how re-running `run` resumes instead of
double-submitting.

**That script does not run VASP.** It runs the Python runner, which executes
the ASSYST graph for the structure: three relaxations, then the statics. For
each of those calculations the graph creates a directory
(`ISIF7/`, `ISIF5/`, `ISIF2/`, `ISIF2_accur_relaxstep0/`, …), writes
INCAR/POSCAR/POTCAR/KPOINTS into it, and runs **the VASP command block** in it
through a shell. That block is the second thing you adapt, and it is built
from `vasp`:

```bash
module load nvhpc/24.5                                   # vasp.modules, one line each
module load openmpi/4.1.6-cuda
module load fftw/3.3.10
export OMP_PLACES=cores                                  # vasp.prologue, verbatim
export OMP_PROC_BIND=close
cat > custodian_vasp.py << 'PYEOF'                       # only with vasp.custodian: true
import sys
from custodian.custodian import Custodian
from custodian.vasp.handlers import (
    VaspErrorHandler,
    NonConvergingErrorHandler,
    FrozenJobErrorHandler,
)
from custodian.vasp.jobs import VaspJob

output_filename = "vasp.log"
handlers = [
    VaspErrorHandler(output_filename=output_filename),
    NonConvergingErrorHandler(),
    FrozenJobErrorHandler(output_filename=output_filename),
]
jobs = [VaspJob(sys.argv[1:], output_file=output_filename, suffix="")]
Custodian(handlers, jobs, max_errors=10, polling_time_step=3, monitor_freq=1).run()   # vasp.max_errors
PYEOF

/sw/envs/pymatgen/bin/python custodian_vasp.py srun -n 4 --gpus-per-task=1 --cpu-bind=cores /sw/vasp/6.4.3-nvhpc/bin/vasp_std >> vasp.log
#  ^ vasp.custodian_python          ^ vasp.launcher ({ntasks} = slurm.ntasks)      ^ vasp.binary
```

With `custodian: false` the last part collapses to
`<launcher> <binary> >> vasp.log`. Everything VASP prints lands in `vasp.log`
in that calculation directory, which is also what custodian's handlers watch.

So the whole invocation is the composition of five keys, in this order:
**`vasp.modules` → `vasp.prologue` → (custodian wrapper) →
`vasp.launcher` `vasp.binary`**, run with the working directory set to the
calculation directory, inside the allocation that `slurm.*` requested. You
can print it for any config without rendering a campaign:

```bash
python -c "
from assystant.config import CampaignConfig
from assystant.submit import build_vasp_command
print(build_vasp_command(CampaignConfig.from_yaml('camp.yaml', ['site_gpu.yaml']).resolve()))"
```

## 4. Adapting it: GPUs, other MPIs, flux

The site file that produced the script above:

```yaml
slurm:
  partition: gpu
  account: proj1234
  ntasks: 4                       # one MPI rank per GPU
  cpus_per_task: 16
  mem_per_cpu: 8000MB
  extra_directives: ["--gres=gpu:a100:4", "--constraint=a100"]
  prologue:
    - source /etc/profile.d/modules.sh
    - export OMP_NUM_THREADS=16
vasp:
  binary: /sw/vasp/6.4.3-nvhpc/bin/vasp_std
  modules: [nvhpc/24.5, openmpi/4.1.6-cuda, fftw/3.3.10]
  prologue:
    - export OMP_PLACES=cores
    - export OMP_PROC_BIND=close
  launcher: "srun -n {ntasks} --gpus-per-task=1 --cpu-bind=cores"
  potcar_config: /scratch/demo/sw/pyiron_vasp_config
  custodian: true
  custodian_python: /sw/envs/pymatgen/bin/python
```

The pattern for any machine: resources and anything the *job* needs go in
`slurm` (`extra_directives` for `#SBATCH` lines, `prologue` for shell lines
run once per job); anything the *VASP process* needs goes in `vasp`
(`modules`, `prologue`, `launcher`). Recipes:

| situation | what to set |
|---|---|
| **GPU VASP** (`vasp_std` built with NVHPC / OpenACC) | `slurm.extra_directives: ["--gres=gpu:4"]`, `slurm.ntasks` = number of GPUs, `cpus_per_task` for the OpenMP threads, `vasp.launcher: "srun -n {ntasks} --gpus-per-task=1"`, `vasp.prologue` for `OMP_*` / `CUDA_VISIBLE_DEVICES`, and the CUDA-aware MPI in `vasp.modules` |
| **OpenMPI / MPICH without SLURM integration** | `vasp.launcher: "mpirun -n {ntasks} --bind-to core"` (or `mpiexec`); keep `slurm.ntasks` so `{ntasks}` is right |
| **Intel MPI** | the default `srun` launcher works when the `impi` module wires PMI2 (the MPIE setup); outside an allocation see the [smoke test](#7-smoke-test-on-the-login-node) |
| **flux as the launcher inside a SLURM job** | `vasp.launcher: "flux run -n {ntasks} -g 1"`, `vasp.prologue: [source /opt/flux/env.sh]` |
| **flux (or PBS, LSF) as the scheduler** | `slurm.submit_command: "flux batch -N1 -n4 -g4 -t 48h"` (the script path is appended; flux ignores `#SBATCH` lines, so give resources on the command line), `slurm.module_purge` as appropriate, and `vasp.launcher: "flux run -n {ntasks}"`. The already-finished skip still works; the *queued* skip queries `squeue` and prints a warning when it is absent, so do not re-run `run` while jobs are waiting |
| **no environment-modules system** | `slurm.module_purge: false`, `vasp.modules: []`, and `source` whatever sets up the toolchain in `vasp.prologue` |
| **VASP needs a license / scratch variable** | `vasp.prologue: ["export VASP_SCRATCH=/tmp", ...]` |
| **custodian lives elsewhere** | `vasp.custodian_python: /path/to/env/bin/python`; the runner env does not need custodian, the custodian env does not need `pyiron_workflow` |
| **sbatch needs flags** | `slurm.submit_command: "sbatch --export=ALL --parsable"` |

The flux-launcher, no-modules, no-custodian variant renders the VASP block as:

```bash
source /opt/nvhpc/env.sh
flux run -n 4 -g 1 /sw/vasp/6.4.3-nvhpc/bin/vasp_std >> vasp.log
```

Whatever you change, the two checks are the same: `run --dry-run --limit 1`
and read the script, then the `build_vasp_command` one-liner above for the
VASP block. Neither needs the scheduler.

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
* [ ] `<pyiron_vasp_resources>/potpaw_64/{GGA,LDA}` layout in place; `pyiron_vasp_config` written; the resolution snippet prints `exists=True` for every element
* [ ] `assystant example site -o site_<cluster>.yaml`; no `<EDIT` left: `partition`, `binary`, `modules`, `potcar_config`, `custodian_python` if `custodian: true`
* [ ] `run --dry-run --limit 1`; read the script, and print the VASP block with `build_vasp_command`: partition, directives, modules, launcher, binary, `export PYIRON_VASP_CONFIG`
* [ ] smoke test finishes on the login node
* [ ] `run --limit 1`; the job produces `df_ASSYST_jobs.pkl`
* [ ] then everything else
