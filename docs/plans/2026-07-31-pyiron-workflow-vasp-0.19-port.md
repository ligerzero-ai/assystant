# pyiron_workflow_vasp 0.19 Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port `ligerzero-ai/pyiron_workflow_vasp` from pyiron_workflow 0.11 to 0.19/flowrep, so it can serve as the VASP layer for `pyiron_workflow_assyst` and `assystant`.

**Architecture:** The repo splits cleanly into a `pyiron_workflow`-free parser subpackage (`vasp_parser/`, 1830 lines, untouched) and a thin decorated node layer (14 functions across `vasp.py` and `generic.py`). Function nodes become `@fr.atomic`; the single macro `vasp_job` becomes `@fr.workflow`. Because 0.19 removed execution signals entirely, `vasp_job`'s step ordering must be re-expressed as data dependencies.

**Tech Stack:** Python 3.12, pyiron_workflow 0.19.0, flowrep 0.6.2, pymatgen 2026.7.27, pytest.

## Global Constraints

- Target environment: `/cmmc/ptmp/hmai/mambaforge/envs/assystant` (Python 3.12.13). Invoke as `/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/python` and `.../bin/pytest`.
- Dependency floors: `pyiron_workflow>=0.19,<0.20`, `flowrep>=0.6,<0.7`, `requires-python = ">=3.11,<3.14"`.
- **No `os.chdir` anywhere in the package.** 0.19 runs DAG layers multithreaded by default (`dag_layers_multithreaded=True`, 10 threads); process working directory is global state and would race between concurrent nodes.
- **No execution signals.** `>>`, `starting_nodes`, and `self.<label> = ...` macro assignment do not exist in 0.19. Ordering between side-effecting nodes must be created by threading a value from one into the next.
- All working directories are absolute paths. Never rely on process CWD.
- Node functions must be importable at module top level — flowrep rejects anything with `<locals>` in its qualname.
- Git commits use author name `Han Lin Mai`.

---

## Preflight

The repo has one uncommitted 2-line edit to `pyiron_workflow_assyst/workflow.py` — that is a *different* repo and is not touched here. In `pyiron_workflow_vasp` the only uncommitted item is an untracked `.ipynb_checkpoints/` directory.

- [ ] **Step 0.1: Branch from a clean main**

```bash
cd /cmmc/u/hmai/pip_repos/pyiron_workflow_vasp
git status --short
git checkout -b port/pyiron-workflow-0.19
```

- [ ] **Step 0.2: Ignore notebook checkpoints**

Append to `.gitignore` (create it if absent):

```
.ipynb_checkpoints/
__pycache__/
*.pyc
.pytest_cache/
```

```bash
git add .gitignore && git commit -m "chore: ignore notebook checkpoints and caches"
```

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `pyiron_workflow_vasp/vasp_parser/outcar.py` | OUTCAR parsing | docstring hygiene only |
| `pyiron_workflow_vasp/vasp_parser/output.py` | directory-level parsing | unchanged |
| `pyiron_workflow_vasp/vasp_parser/__init__.py` | parser exports | fix broken `__all__` |
| `pyiron_workflow_vasp/generic.py` | shell + file operations | port 5 nodes, delete `submit_to_slurm` |
| `pyiron_workflow_vasp/vasp.py` | VASP inputs, nodes, `vasp_job` | port 9 nodes, delete `get_multiple_input` |
| `pyiron_workflow_vasp/__init__.py` | package exports | update |
| `tests/conftest.py` | shared fixtures | create |
| `tests/test_generic.py` | file-op and shell node tests | create |
| `tests/test_vasp_nodes.py` | VASP node tests | create |
| `tests/test_vasp_job.py` | end-to-end `vasp_job` integration test | create |
| `pyproject.toml` | deps, python floor, pytest config | modify |

---

### Task 1: Test harness and dependency floors

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/conftest.py`
- Test: `tests/test_imports.py`

**Interfaces:**
- Consumes: nothing.
- Produces: fixture `fake_vasp_dir(tmp_path)` returning a `pathlib.Path` to a directory containing a minimal `vasp.log`, `OUTCAR` and `POSCAR`; fixture `abs_workdir(tmp_path)` returning an absolute `str` path to an empty directory.

- [ ] **Step 1.1: Write the failing test**

Create `tests/test_imports.py`:

```python
import pyiron_workflow as pwf
import flowrep as fr


def test_pyiron_workflow_is_019():
    assert pwf.__version__.startswith("0.19")


def test_package_imports_without_pyiron_workflow_legacy_api():
    import pyiron_workflow_vasp

    assert hasattr(pyiron_workflow_vasp, "vasp_job")


def test_parser_subpackage_star_import_works():
    import pyiron_workflow_vasp.vasp_parser as vp

    for name in vp.__all__:
        assert hasattr(vp, name), f"{name} advertised in __all__ but missing"
```

- [ ] **Step 1.2: Run test to verify it fails**

Run: `/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pytest tests/test_imports.py -v`

Expected: FAIL. `test_package_imports_without_pyiron_workflow_legacy_api` errors with `ImportError: cannot import name 'Workflow' from 'pyiron_workflow'` (0.19 removed the legacy top-level export used by `vasp.py`), and `test_parser_subpackage_star_import_works` fails on `VaspParser` being absent.

- [ ] **Step 1.3: Update `pyproject.toml`**

Set the dependency block and python floor:

```toml
[project]
requires-python = ">=3.11,<3.14"
dependencies = [
    "pyiron_workflow>=0.19,<0.20",
    "flowrep>=0.6,<0.7",
    "pymatgen>=2024.1.1",
    "ase>=3.22.0",
    "numpy>=1.20.0",
    "pandas>=1.3.0",
    "monty",
    "scipy",
]

[project.optional-dependencies]
dev = ["pytest>=6.0", "pytest-cov>=2.0"]

[tool.pytest.ini_options]
testpaths = ["tests"]
filterwarnings = ["error::SyntaxWarning"]
```

The `filterwarnings` entry turns the `outcar.py` docstring warnings into failures, which is what forces Task 2 to be done properly rather than left as noise.

- [ ] **Step 1.4: Neutralise `__init__.py` so submodules are importable during the port**

Found by pre-flight scan. `pyiron_workflow_vasp/__init__.py` does `from .vasp import *`,
and Python executes the package `__init__` before any submodule import. So
`from pyiron_workflow_vasp.generic import ...` — which every test in Tasks 3–5 does —
fails while `vasp.py` is still on the 0.11 API. Tasks 3, 4 and 5 are untestable until
this is emptied. Task 9 fills it back in with the real exports.

Replace `pyiron_workflow_vasp/__init__.py` with:

```python
"""pyiron_workflow_vasp — VASP nodes and parsers for pyiron_workflow.

Intentionally empty during the 0.19 port: eager star-imports of `vasp` would
break submodule imports while that module is mid-migration. Task 9 restores
the explicit public surface.
"""

__version__ = "0.2.0.dev0"
```

- [ ] **Step 1.5: Create shared fixtures**

Create `tests/conftest.py`:

```python
import pathlib

import pytest

MINIMAL_OUTCAR = """\
 vasp.6.4.3 complex
 POTCAR:    PAW_PBE Fe 06Sep2000
 energy  without entropy=      -8.21000000  energy(sigma->0) =       -8.21000000
 reached required accuracy - stopping structural energy minimisation
"""

MINIMAL_POSCAR = """\
Fe
1.0
2.83 0.00 0.00
0.00 2.83 0.00
0.00 0.00 2.83
Fe
1
Direct
0.0 0.0 0.0
"""


@pytest.fixture
def abs_workdir(tmp_path: pathlib.Path) -> str:
    d = tmp_path / "calc"
    d.mkdir()
    return str(d.resolve())


@pytest.fixture
def fake_vasp_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    d = tmp_path / "vaspcalc"
    d.mkdir()
    (d / "OUTCAR").write_text(MINIMAL_OUTCAR)
    (d / "POSCAR").write_text(MINIMAL_POSCAR)
    (d / "vasp.log").write_text(
        "reached required accuracy - stopping structural energy minimisation\n"
    )
    (d / "CHGCAR").write_text("bulk charge density placeholder\n")
    (d / "WAVECAR").write_text("wavefunction placeholder\n")
    return d
```

- [ ] **Step 1.6: Run tests to confirm the harness itself works**

Run: `/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pytest tests/test_imports.py::test_pyiron_workflow_is_019 -v`

Expected: PASS. `test_package_imports_without_pyiron_workflow_legacy_api` still fails
(no `vasp_job` export until Task 9) and `test_parser_subpackage_star_import_works` still
fails (Task 2). Both are expected at this point.

- [ ] **Step 1.7: Commit**

```bash
git add pyproject.toml pyiron_workflow_vasp/__init__.py tests/conftest.py tests/test_imports.py
git commit -m "test: add harness, pin 0.19 floors, neutralise __init__ during port"
```

---

### Task 2: Parser hygiene

The parsers need no porting, but two latent defects surfaced during verification: six docstrings in `outcar.py` contain an unescaped `\A` (raising `SyntaxWarning` under Python 3.12), and `vasp_parser/__init__.py` advertises a `VaspParser` name that does not exist, so `from pyiron_workflow_vasp.vasp_parser import *` raises `AttributeError`.

**Files:**
- Modify: `pyiron_workflow_vasp/vasp_parser/outcar.py` (lines 250, 278, 306, 332, 1101, 1139)
- Modify: `pyiron_workflow_vasp/vasp_parser/__init__.py`
- Test: `tests/test_imports.py::test_parser_subpackage_star_import_works`

**Interfaces:**
- Consumes: nothing.
- Produces: `pyiron_workflow_vasp.vasp_parser.parse_vasp_directory(directory)` importable and star-importable.

- [ ] **Step 2.1: Run the failing test**

Run: `/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pytest tests/test_imports.py::test_parser_subpackage_star_import_works -v`

Expected: FAIL with `AssertionError: VaspParser advertised in __all__ but missing`.

- [ ] **Step 2.2: Fix the docstrings**

Each of the six affected docstrings contains `$\AA$`. Prefix the docstring with `r` to make it raw. For example at line 250:

```python
        r"""
        Returns the positions for every ionic step from the OUTCAR file

        Args:
            filename (str): Filename of the OUTCAR file to parse
            lines (list/None): lines read from the file

        Returns:
            numpy.ndarray: A Nx3xM array of positions in $\AA$
        """
```

Apply the same `r` prefix at lines 278, 306, 332, 1101 and 1139.

- [ ] **Step 2.3: Fix the parser exports**

Replace `pyiron_workflow_vasp/vasp_parser/__init__.py` with:

```python
"""VASP parser module for pyiron_workflow_vasp.

Deliberately free of any pyiron_workflow import so it can be used from any
workflow-engine version, or standalone.
"""

from .outcar import Outcar
from .output import parse_vasp_directory

__all__ = ["Outcar", "parse_vasp_directory"]
```

- [ ] **Step 2.4: Run tests to verify they pass**

Run: `/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pytest tests/test_imports.py::test_parser_subpackage_star_import_works -v`

Expected: PASS, with no `SyntaxWarning` (which `filterwarnings = ["error::SyntaxWarning"]` would otherwise turn into a failure).

- [ ] **Step 2.5: Commit**

```bash
git add pyiron_workflow_vasp/vasp_parser/
git commit -m "fix: raw docstrings in outcar.py and correct vasp_parser __all__"
```

---

### Task 3: Port the file-operation nodes in `generic.py`

**Files:**
- Modify: `pyiron_workflow_vasp/generic.py`
- Test: `tests/test_generic.py`

**Interfaces:**
- Consumes: `abs_workdir`, `fake_vasp_dir` fixtures from Task 1.
- Produces, all as `@fr.atomic` nodes:
  - `delete_files_recursively(workdir: str, files_to_be_deleted: list[str], after: Any = None) -> str` returning `workdir`, output port `"workdir"`.
  - `compress_directory(directory_path: str, actually_compress: bool = True, inside_dir: bool = True, exclude_files: list[str] | None = None, after: Any = None) -> str` returning `directory_path`, output port `"directory_path"`.
  - `remove_directory(directory_path: str, actually_remove: bool = False, payload: Any = None) -> Any` returning `payload`, output port `"payload"`.
  - `is_line_in_file(filepath: str, line: str, exact_match: bool = True) -> bool`, output port `"line_found"`.

The `after` and `payload` parameters exist solely to create data edges that impose ordering, replacing the deleted `>>` signal chain. `remove_directory` is a pass-through: it forwards whatever it is given, so that consumers of that value are guaranteed to be ordered before the directory disappears.

- [ ] **Step 3.1: Write the failing tests**

Create `tests/test_generic.py`:

```python
import pathlib
import tarfile

import pyiron_workflow as pwf

from pyiron_workflow_vasp.generic import (
    compress_directory,
    delete_files_recursively,
    is_line_in_file,
    remove_directory,
)


def test_delete_files_recursively_removes_only_named_files(fake_vasp_dir):
    run = pwf.node(delete_files_recursively).run(
        workdir=str(fake_vasp_dir), files_to_be_deleted=["CHGCAR", "WAVECAR"]
    )
    assert run.status == "finished"
    assert run.outputs.workdir == str(fake_vasp_dir)
    assert not (fake_vasp_dir / "CHGCAR").exists()
    assert not (fake_vasp_dir / "WAVECAR").exists()
    assert (fake_vasp_dir / "OUTCAR").exists()


def test_delete_files_recursively_tolerates_missing_directory(tmp_path):
    missing = str(tmp_path / "nope")
    run = pwf.node(delete_files_recursively).run(
        workdir=missing, files_to_be_deleted=["CHGCAR"]
    )
    assert run.status == "finished"
    assert run.outputs.workdir == missing


def test_compress_directory_creates_tarball_and_returns_path(fake_vasp_dir):
    run = pwf.node(compress_directory).run(
        directory_path=str(fake_vasp_dir), actually_compress=True, inside_dir=True
    )
    assert run.outputs.directory_path == str(fake_vasp_dir)
    tarballs = list(pathlib.Path(fake_vasp_dir).glob("*.tar.gz"))
    assert len(tarballs) == 1
    with tarfile.open(tarballs[0]) as tf:
        assert any(n.endswith("OUTCAR") for n in tf.getnames())


def test_compress_directory_is_a_noop_when_disabled(fake_vasp_dir):
    run = pwf.node(compress_directory).run(
        directory_path=str(fake_vasp_dir), actually_compress=False
    )
    assert run.outputs.directory_path == str(fake_vasp_dir)
    assert list(pathlib.Path(fake_vasp_dir).glob("*.tar.gz")) == []


def test_remove_directory_forwards_payload_and_deletes(fake_vasp_dir):
    sentinel = {"energy": -8.21}
    run = pwf.node(remove_directory).run(
        directory_path=str(fake_vasp_dir), actually_remove=True, payload=sentinel
    )
    assert run.outputs.payload == sentinel
    assert not fake_vasp_dir.exists()


def test_remove_directory_forwards_payload_without_deleting(fake_vasp_dir):
    run = pwf.node(remove_directory).run(
        directory_path=str(fake_vasp_dir), actually_remove=False, payload="keep"
    )
    assert run.outputs.payload == "keep"
    assert fake_vasp_dir.exists()


def test_is_line_in_file_substring_match(fake_vasp_dir):
    run = pwf.node(is_line_in_file).run(
        filepath=str(fake_vasp_dir / "vasp.log"),
        line="reached required accuracy",
        exact_match=False,
    )
    assert run.outputs.line_found is True
```

- [ ] **Step 3.2: Run tests to verify they fail**

Run: `/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pytest tests/test_generic.py -v`

Expected: FAIL at import — `ImportError: cannot import name 'Workflow' from 'pyiron_workflow'`, raised by `generic.py:14`.

- [ ] **Step 3.3: Rewrite the node layer in `generic.py`**

Replace the `from pyiron_workflow import Workflow` import at line 14 with `import flowrep as fr`, then replace each `@Workflow.wrap.as_function_node("x")` decorator with `@fr.atomic("x")`. The function bodies are unchanged except as noted. Rename `isLineInFile` to `is_line_in_file` and `remove_dir` to `remove_directory` for PEP 8 consistency, since every call site is being rewritten anyway.

```python
import flowrep as fr


@fr.atomic("workdir")
def delete_files_recursively(
    workdir: str, files_to_be_deleted: list[str], after: object = None
) -> str:
    """Recursively delete named files under ``workdir``.

    ``after`` is an ordering token: it is never read, but accepting it lets a
    caller create a data edge that forces this node to run after another.
    0.19 has no execution signals, so ordering must come from data flow.
    """
    if not os.path.isdir(workdir):
        logger.info(f"Error: {workdir} is not a valid directory.")
    else:
        for root, _, files in os.walk(workdir):
            for file in files:
                if file in files_to_be_deleted:
                    file_path = os.path.join(root, file)
                    try:
                        os.remove(file_path)
                        logger.info(f"Deleted: {file_path}")
                    except Exception as e:
                        logger.info(f"Error deleting {file_path}: {e}")
    return workdir


@fr.atomic("directory_path")
def compress_directory(
    directory_path: str,
    actually_compress: bool = True,
    inside_dir: bool = True,
    exclude_files: list[str] | None = None,
    after: object = None,
) -> str:
    """Compress ``directory_path`` to a gzipped tarball, returning the directory.

    Returns the directory rather than the tarball so the value can be threaded
    onward to establish ordering. ``after`` is an ordering token; see
    :func:`delete_files_recursively`.
    """
    if not actually_compress:
        return directory_path
    exclude = set(exclude_files or [])
    base = os.path.basename(os.path.normpath(directory_path))
    target_dir = directory_path if inside_dir else os.path.dirname(directory_path)
    tar_path = os.path.join(target_dir, f"{base}.tar.gz")
    with tarfile.open(tar_path, "w:gz") as tar:
        for root, _, files in os.walk(directory_path):
            for file in files:
                if file in exclude or file == f"{base}.tar.gz":
                    continue
                full = os.path.join(root, file)
                tar.add(full, arcname=os.path.relpath(full, directory_path))
    return directory_path


@fr.atomic("payload")
def remove_directory(
    directory_path: str, actually_remove: bool = False, payload: object = None
) -> object:
    """Optionally delete ``directory_path``, forwarding ``payload`` unchanged.

    The pass-through is deliberate: any consumer of the returned payload is
    necessarily ordered after the removal, which is how the old signal chain's
    "remove last" guarantee is preserved.
    """
    if actually_remove and os.path.isdir(directory_path):
        shutil.rmtree(directory_path)
    return payload


@fr.atomic("line_found")
def is_line_in_file(filepath: str, line: str, exact_match: bool = True) -> bool:
    line_found = False
    try:
        with open(filepath) as f:
            for file_line in f:
                if (exact_match and file_line.strip() == line) or (
                    not exact_match and line in file_line
                ):
                    line_found = True
                    break
    except FileNotFoundError:
        logger.info(f"File '{filepath}' not found.")
    return line_found
```

Ensure `import shutil` and `import tarfile` are present at the top of the module.

- [ ] **Step 3.4: Run tests to verify they pass**

Run: `/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pytest tests/test_generic.py -v`

Expected: PASS, 7 tests.

- [ ] **Step 3.5: Commit**

```bash
git add pyiron_workflow_vasp/generic.py tests/test_generic.py
git commit -m "feat: port generic file-operation nodes to flowrep atomics"
```

---

### Task 4: Port `shell` and remove the `os.chdir` race

The existing `shell` saves the CWD, calls `os.chdir(workdir)`, runs the subprocess, and restores. The `os.chdir` is redundant — `subprocess.run` is already given `cwd=workdir` — and under 0.19's multithreaded DAG layers two concurrent `shell` nodes would corrupt each other's working directory.

**Files:**
- Modify: `pyiron_workflow_vasp/generic.py`
- Test: `tests/test_generic.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `run_shell(command: str, workdir: str, environment: dict[str, str] | None = None, arguments: list[str] | None = None) -> ShellOutput` as an `@fr.atomic` node with output port `"output"`. `ShellOutput` keeps its existing `stdout`, `stderr`, `return_code` attributes.

- [ ] **Step 4.1: Write the failing tests**

Append to `tests/test_generic.py`:

```python
import os

import pyiron_workflow as pwf

from pyiron_workflow_vasp.generic import run_shell


def test_run_shell_executes_in_workdir(abs_workdir):
    run = pwf.node(run_shell).run(command="pwd", workdir=abs_workdir)
    assert run.outputs.output.return_code == 0
    assert run.outputs.output.stdout.strip() == abs_workdir


def test_run_shell_does_not_mutate_process_cwd(abs_workdir):
    before = os.getcwd()
    pwf.node(run_shell).run(command="pwd", workdir=abs_workdir)
    assert os.getcwd() == before


def test_run_shell_captures_failure(abs_workdir):
    run = pwf.node(run_shell).run(command="exit 3", workdir=abs_workdir)
    assert run.outputs.output.return_code == 3


def test_run_shell_is_concurrency_safe(abs_workdir, tmp_path):
    """Two shell nodes in one DAG layer must not corrupt each other's cwd.

    This is the regression test for removing os.chdir: with the chdir in
    place, interleaved execution makes one of the two `pwd` results wrong.
    """
    import concurrent.futures

    other = tmp_path / "other"
    other.mkdir()
    other_abs = str(other.resolve())

    def run_in(d):
        return pwf.node(run_shell).run(command="pwd", workdir=d).outputs.output.stdout.strip()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        futures = [ex.submit(run_in, d) for d in (abs_workdir, other_abs) for _ in range(20)]
        results = {f.result() for f in futures}

    assert results == {abs_workdir, other_abs}
```

- [ ] **Step 4.2: Run tests to verify they fail**

Run: `/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pytest tests/test_generic.py -k shell -v`

Expected: FAIL with `ImportError: cannot import name 'run_shell'`.

- [ ] **Step 4.3: Rewrite `shell`**

Replace the `shell` function in `generic.py`:

```python
@fr.atomic("output")
def run_shell(
    command: str,
    workdir: str,
    environment: dict[str, str] | None = None,
    arguments: list[str] | None = None,
) -> ShellOutput:
    """Run ``command`` with ``workdir`` as the working directory.

    Deliberately does NOT call os.chdir: subprocess.run's ``cwd`` argument
    already scopes the child process, and mutating the parent's working
    directory is unsafe now that pyiron_workflow 0.19 evaluates sibling nodes
    in a DAG layer on separate threads by default.
    """
    if environment is None:
        environment = {}
    if arguments is None:
        arguments = []
    environ = dict(os.environ)
    environ.update({k: str(v) for k, v in environment.items()})
    proc = subprocess.run(
        [command, *map(str, arguments)],
        capture_output=True,
        cwd=workdir,
        encoding="utf8",
        env=environ,
        shell=True,
    )
    output = ShellOutput()
    output.stdout = proc.stdout
    output.stderr = proc.stderr
    output.return_code = proc.returncode
    return output
```

- [ ] **Step 4.4: Run tests to verify they pass**

Run: `/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pytest tests/test_generic.py -v`

Expected: PASS, 11 tests.

- [ ] **Step 4.5: Commit**

```bash
git add pyiron_workflow_vasp/generic.py tests/test_generic.py
git commit -m "fix: port shell node and drop thread-unsafe os.chdir"
```

---

### Task 5: Delete `submit_to_slurm`

`generic.py:297` does `from pyiron_workflow import PickleStorage` inside `submit_to_slurm`. `PickleStorage` does not exist in 0.19 and has no replacement. Job submission belongs to `assystant`, so the function is removed rather than ported.

**Files:**
- Modify: `pyiron_workflow_vasp/generic.py` (delete `submit_to_slurm`, lines ~247–308)
- Test: `tests/test_generic.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `submit_to_slurm` no longer exists in the package namespace.

- [ ] **Step 5.1: Write the failing test**

Append to `tests/test_generic.py`:

```python
def test_submit_to_slurm_is_gone():
    """Submission moved to assystant; the old helper imported PickleStorage,
    which pyiron_workflow 0.19 removed outright."""
    import pyiron_workflow_vasp.generic as generic

    assert not hasattr(generic, "submit_to_slurm")


def test_no_pickle_storage_references_remain():
    import pathlib

    import pyiron_workflow_vasp

    root = pathlib.Path(pyiron_workflow_vasp.__file__).parent
    offenders = [
        p.name
        for p in root.rglob("*.py")
        if "PickleStorage" in p.read_text()
    ]
    assert offenders == []
```

- [ ] **Step 5.2: Run tests to verify they fail**

Run: `/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pytest tests/test_generic.py -k "slurm or pickle" -v`

Expected: FAIL — both assertions, since `submit_to_slurm` is still defined and still references `PickleStorage`.

- [ ] **Step 5.3: Delete the function**

Remove the entire `submit_to_slurm` definition from `generic.py`, including its `from pyiron_workflow import PickleStorage` line.

- [ ] **Step 5.4: Run tests to verify they pass**

Run: `/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pytest tests/test_generic.py -v`

Expected: PASS, 13 tests.

- [ ] **Step 5.5: Commit**

```bash
git add pyiron_workflow_vasp/generic.py tests/test_generic.py
git commit -m "refactor: drop submit_to_slurm; submission is assystant's concern"
```

---

### Task 6: Port the VASP input/output nodes

**Files:**
- Modify: `pyiron_workflow_vasp/vasp.py` (lines 217–325)
- Test: `tests/test_vasp_nodes.py`

**Interfaces:**
- Consumes: `is_line_in_file` from Task 3; `abs_workdir`, `fake_vasp_dir` fixtures from Task 1.
- Produces, all `@fr.atomic`:
  - `create_working_directory(workdir: str, quiet: bool = False) -> str`, port `"workdir"`.
  - `write_vasp_input_set(workdir: str, vasp_input: VaspInput) -> str`, port `"workdir"`.
  - `parse_vasp_output(workdir: str, function: Callable | None = None, parser_args: dict | None = None, after: object = None) -> dict`, port `"output_dict"`.
  - `check_convergence(workdir: str, filename_vasprun: str = "vasprun.xml", filename_vasplog: str = "vasp.log", backup_vasplog: str = "error.out", after: object = None) -> bool`, port `"convergence"`.

`VaspInput` and the undecorated `write_POSCAR`/`write_INCAR`/`write_POTCAR`/`write_KPOINTS` helpers carry over unchanged.

- [ ] **Step 6.1: Write the failing tests**

Create `tests/test_vasp_nodes.py`:

```python
import os

import pyiron_workflow as pwf
import pytest

from pyiron_workflow_vasp.vasp import (
    check_convergence,
    create_working_directory,
    parse_vasp_output,
)


def test_create_working_directory_makes_it(tmp_path):
    target = str((tmp_path / "fresh").resolve())
    run = pwf.node(create_working_directory).run(workdir=target)
    assert run.outputs.workdir == target
    assert os.path.isdir(target)


def test_create_working_directory_is_idempotent(abs_workdir):
    run = pwf.node(create_working_directory).run(workdir=abs_workdir, quiet=True)
    assert run.outputs.workdir == abs_workdir
    assert os.path.isdir(abs_workdir)


def test_check_convergence_reads_vasp_log(fake_vasp_dir):
    run = pwf.node(check_convergence).run(workdir=str(fake_vasp_dir))
    assert run.outputs.convergence is True


def test_check_convergence_false_when_no_marker(abs_workdir):
    run = pwf.node(check_convergence).run(workdir=abs_workdir)
    assert run.outputs.convergence is False


def test_parse_vasp_output_uses_supplied_function(abs_workdir):
    def fake_parser(directory):
        return {"directory": directory, "energy": -8.21}

    run = pwf.node(parse_vasp_output).run(
        workdir=abs_workdir,
        function=fake_parser,
        parser_args={"directory": abs_workdir},
    )
    assert run.outputs.output_dict["energy"] == -8.21


def test_parse_vasp_output_after_token_does_not_change_result(abs_workdir):
    def fake_parser(directory):
        return {"directory": directory}

    run = pwf.node(parse_vasp_output).run(
        workdir=abs_workdir,
        function=fake_parser,
        parser_args={"directory": abs_workdir},
        after="ignored-ordering-token",
    )
    assert run.outputs.output_dict["directory"] == abs_workdir
```

- [ ] **Step 6.2: Run tests to verify they fail**

Run: `/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pytest tests/test_vasp_nodes.py -v`

Expected: FAIL at import — `ImportError: cannot import name 'Workflow' from 'pyiron_workflow'` from `vasp.py:21`.

- [ ] **Step 6.3: Rewrite the node layer**

In `vasp.py`, make three changes to the import block.

**Delete line 17 entirely:**

```python
from pyiron_vasp.vasp.output import parse_vasp_output as pyiron_atomistics_pvo
```

Found by pre-flight scan. `pyiron_vasp` is a module-level import, is not installed in the
target environment, and is not a declared dependency — so `vasp.py` currently cannot be
imported at all. It was only used as the default parser, and the ported
`parse_vasp_output` uses this repo's own `vasp_parser.output.parse_vasp_directory`
instead. Removing it drops a dependency rather than adding one.

**Leave the module-level POTCAR config alone but be aware of it.** Line 113 runs
`read_potcar_config(~/.pyiron_vasp_config)` at import time and raises `FileNotFoundError`
if that file is absent. It is present on this machine and resolves to
`/cmmc/u/hmai/pyiron-resources-cmmc/vasp/potentials/pyiron_nodes/potpaw_64/GGA`, so tests
pass here. Do not refactor it in this task — it is noted so that a failure in CI or on
another machine is immediately recognisable rather than mysterious.

**Then replace** `from pyiron_workflow import Workflow` with `import flowrep as fr`, and update the import from `generic` to the new names:

```python
import flowrep as fr

from pyiron_workflow_vasp.generic import (
    compress_directory,
    delete_files_recursively,
    is_line_in_file,
    remove_directory,
    run_shell,
)
```

Then rewrite the four nodes:

```python
@fr.atomic("workdir")
def create_working_directory(workdir: str, quiet: bool = False) -> str:
    """Create ``workdir`` if absent. Must be given an absolute path."""
    if not os.path.exists(workdir):
        os.makedirs(workdir)
        logger.info(f"made directory '{workdir}'")
    elif not quiet:
        warnings.warn(
            f"Directory '{workdir}' already exists. Existing files may be overwritten."
        )
    return workdir


@fr.atomic("workdir")
def write_vasp_input_set(workdir: str, vasp_input: VaspInput) -> str:
    write_POSCAR(workdir=workdir, structure=vasp_input.structure)
    write_INCAR(workdir=workdir, incar=vasp_input.incar)
    write_POTCAR(workdir=workdir, vasp_input=vasp_input)
    if vasp_input.kpoints is not None:
        write_KPOINTS(workdir=workdir, kpoints=vasp_input.kpoints)
    return workdir


@fr.atomic("output_dict")
def parse_vasp_output(
    workdir: str,
    function=None,
    parser_args: dict | None = None,
    after: object = None,
) -> dict:
    """Parse VASP output. ``after`` is an ordering token; see generic.py."""
    if function is None:
        from pyiron_workflow_vasp.vasp_parser.output import parse_vasp_directory

        return parse_vasp_directory(workdir)
    return function(**(parser_args or {}))


@fr.atomic("convergence")
def check_convergence(
    workdir: str,
    filename_vasprun: str = "vasprun.xml",
    filename_vasplog: str = "vasp.log",
    backup_vasplog: str = "error.out",
    after: object = None,
) -> bool:
    """Report whether the calculation reached ionic convergence."""
    line_converged = "reached required accuracy - stopping structural energy minimisation"
    try:
        return bool(Vasprun(filename=os.path.join(workdir, filename_vasprun)).converged)
    except Exception:
        pass
    for candidate in (filename_vasplog, backup_vasplog):
        path = os.path.join(workdir, candidate)
        if os.path.isfile(path):
            with open(path) as handle:
                if any(line_converged in line for line in handle):
                    return True
    return False
```

Note the `check_convergence` rewrite drops the old nested bare-`except` ladder and the `isLineInFile.node_function` call — in 0.19 a node has no `.node_function` attribute, so that access would raise.

- [ ] **Step 6.4: Run tests to verify they pass**

Run: `/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pytest tests/test_vasp_nodes.py -v`

Expected: PASS, 6 tests.

- [ ] **Step 6.5: Commit**

```bash
git add pyiron_workflow_vasp/vasp.py tests/test_vasp_nodes.py
git commit -m "feat: port VASP input/output nodes to flowrep atomics"
```

---

### Task 7: Port the INCAR/VaspInput constructor nodes and delete `get_multiple_input`

`get_multiple_input(obj, n)` existed only to broadcast one INCAR or POTCAR path list across the N inputs of a `for_node`. `ForEach` broadcasts non-iterated inputs to every body automatically, so it has no remaining purpose.

**Files:**
- Modify: `pyiron_workflow_vasp/vasp.py` (lines 429–503)
- Test: `tests/test_vasp_nodes.py`

**Interfaces:**
- Consumes: `VaspInput` from `vasp.py`.
- Produces, all `@fr.atomic`:
  - `generate_modified_incar(incar: Incar, modifications: dict) -> Incar`, port `"incar"`.
  - `generate_vasp_input(structure, incar: Incar, potcar_paths=None) -> VaspInput`, port `"vasp_input"`.
  - `construct_sequential_vasp_input(vasp_output: dict, incar: Incar, potcar_paths=None) -> VaspInput`, port `"vasp_input"`.
  - `get_multiple_input` removed.

- [ ] **Step 7.1: Write the failing tests**

Append to `tests/test_vasp_nodes.py`:

```python
from pymatgen.io.vasp.inputs import Incar

import pyiron_workflow_vasp.vasp as vasp_mod
from pyiron_workflow_vasp.vasp import generate_modified_incar


def test_generate_modified_incar_overrides_tag():
    base = Incar.from_dict({"ENCUT": 400, "ISIF": 3, "NSW": 100})
    run = pwf.node(generate_modified_incar).run(
        incar=base, modifications={"ISIF": 7}
    )
    assert run.outputs.incar["ISIF"] == 7
    assert run.outputs.incar["ENCUT"] == 400


def test_generate_modified_incar_does_not_mutate_input():
    base = Incar.from_dict({"ENCUT": 400, "ISIF": 3})
    pwf.node(generate_modified_incar).run(incar=base, modifications={"ISIF": 7})
    assert base["ISIF"] == 3, "the source INCAR must be left untouched"


def test_get_multiple_input_is_gone():
    """ForEach broadcasts non-iterated inputs, so this helper is obsolete."""
    assert not hasattr(vasp_mod, "get_multiple_input")
```

- [ ] **Step 7.2: Run tests to verify they fail**

Run: `/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pytest tests/test_vasp_nodes.py -k "incar or multiple" -v`

Expected: FAIL — `generate_modified_incar` is not yet a flowrep node, and `get_multiple_input` still exists.

- [ ] **Step 7.3: Rewrite the constructors**

```python
@fr.atomic("incar")
def generate_modified_incar(incar: Incar, modifications: dict) -> Incar:
    """Return a copy of ``incar`` with ``modifications`` applied.

    Copies rather than mutating: the same INCAR object is fanned out to many
    nodes, and 0.19 evaluates sibling nodes concurrently.
    """
    modified = Incar.from_dict(dict(incar))
    for key, value in modifications.items():
        modified[key] = value
    return modified


@fr.atomic("vasp_input")
def generate_vasp_input(structure, incar: Incar, potcar_paths=None) -> VaspInput:
    return VaspInput(structure=structure, incar=incar, potcar_paths=potcar_paths)


@fr.atomic("vasp_input")
def construct_sequential_vasp_input(
    vasp_output: dict, incar: Incar, potcar_paths=None
) -> VaspInput:
    """Build the next VaspInput from the final structure of a previous run."""
    structure = vasp_output["structures"][-1]
    return VaspInput(structure=structure, incar=incar, potcar_paths=potcar_paths)
```

Delete `get_multiple_input` entirely.

- [ ] **Step 7.4: Run tests to verify they pass**

Run: `/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pytest tests/test_vasp_nodes.py -v`

Expected: PASS, 9 tests.

- [ ] **Step 7.5: Commit**

```bash
git add pyiron_workflow_vasp/vasp.py tests/test_vasp_nodes.py
git commit -m "feat: port INCAR/VaspInput constructors; remove get_multiple_input"
```

---

### Task 8: Port `vasp_job` to `@fr.workflow`

This is the task the rest of the port exists to enable. The 0.11 macro ordered eight side-effecting steps with `>>` and `starting_nodes`. Neither exists in 0.19, so the ordering is rebuilt from data dependencies using the `after`/`payload` tokens added in Tasks 3 and 6.

The dependency chain, and why each edge is needed:

```
create_working_directory(workdir)          -> wd_created
write_vasp_input_set(wd_created, input)    -> wd_written   (dir must exist first)
run_shell(command, wd_written)             -> shell_result (inputs must be written first)
parse_vasp_output(wd_written, after=shell_result)   -> raw_output
check_convergence(wd_written, after=shell_result)   -> convergence
delete_files_recursively(wd_written, files, after=raw_output) -> wd_cleaned
                                     (must not delete before parsing)
compress_directory(wd_cleaned, ..., after=convergence)       -> wd_archived
                                     (must not archive before convergence is read)
remove_directory(wd_archived, remove_calc_dir, payload=raw_output) -> vasp_output
                                     (removal is last; payload forwards the result)
```

Threading `convergence` into `compress_directory` is the edge that matters most: without it, compression and removal form a branch parallel to `check_convergence`, and the directory could be deleted before convergence is read.

**Files:**
- Modify: `pyiron_workflow_vasp/vasp.py` (lines 328–385)
- Test: `tests/test_vasp_job.py`

**Interfaces:**
- Consumes: every node from Tasks 3, 4, 6, 7.
- Produces: `vasp_job(...)` as an `@fr.workflow` with output ports `"vasp_output"` and `"convergence_status"`, consumed by `pyiron_workflow_assyst` in the follow-on plan.

- [ ] **Step 8.1: Write the failing test**

Create `tests/test_vasp_job.py`:

```python
import os

import pyiron_workflow as pwf
import pytest
from pymatgen.core import Lattice, Structure
from pymatgen.io.vasp.inputs import Incar

from pyiron_workflow_vasp.vasp import VaspInput, vasp_job


@pytest.fixture
def fe_structure():
    return Structure(
        Lattice.cubic(2.83), ["Fe"], [[0.0, 0.0, 0.0]]
    )


@pytest.fixture
def stub_vasp_command(tmp_path):
    """A stand-in for VASP: writes the convergence marker and an OUTCAR."""
    marker = "reached required accuracy - stopping structural energy minimisation"
    return (
        f"echo '{marker}' > vasp.log && "
        f"echo '{marker}' > OUTCAR && "
        f"echo 'charge' > CHGCAR"
    )


def test_vasp_job_runs_end_to_end(tmp_path, fe_structure, stub_vasp_command):
    workdir = str((tmp_path / "calc").resolve())
    incar = Incar.from_dict({"ENCUT": 300, "NSW": 0})

    def fake_parser(directory):
        return {"energy": -8.21, "directory": directory}

    node = pwf.node(vasp_job)
    run = node.run(
        workdir=workdir,
        vasp_input=VaspInput(structure=fe_structure, incar=incar, potcar_paths=None),
        command=stub_vasp_command,
        files_to_be_deleted=["CHGCAR"],
        compress=False,
        compressed_file_in_dir=False,
        remove_calc_dir=False,
        vasp_parser_function=fake_parser,
        vasp_parser_args={"directory": workdir},
    )

    assert run.status == "finished"
    assert run.outputs.convergence_status is True
    assert run.outputs.vasp_output["energy"] == -8.21
    assert not os.path.exists(os.path.join(workdir, "CHGCAR")), "cleanup must run"
    assert os.path.exists(os.path.join(workdir, "OUTCAR")), "OUTCAR must survive"


def test_vasp_job_removes_calc_dir_but_returns_output(
    tmp_path, fe_structure, stub_vasp_command
):
    """remove_calc_dir deletes the directory yet the parsed result survives,
    which is the whole point of the payload pass-through."""
    workdir = str((tmp_path / "calc2").resolve())
    incar = Incar.from_dict({"ENCUT": 300, "NSW": 0})

    def fake_parser(directory):
        return {"energy": -1.23}

    run = pwf.node(vasp_job).run(
        workdir=workdir,
        vasp_input=VaspInput(structure=fe_structure, incar=incar, potcar_paths=None),
        command=stub_vasp_command,
        files_to_be_deleted=[],
        compress=False,
        compressed_file_in_dir=False,
        remove_calc_dir=True,
        vasp_parser_function=fake_parser,
        vasp_parser_args={"directory": workdir},
    )

    assert run.outputs.vasp_output["energy"] == -1.23
    assert run.outputs.convergence_status is True
    assert not os.path.exists(workdir), "directory should have been removed"


def test_vasp_job_graph_orders_removal_after_convergence(fe_structure):
    """Structural check: the node that removes the directory must be
    downstream of the node that reads convergence, otherwise the check could
    race the deletion."""
    node = pwf.node(vasp_job)
    child_labels = list(node.nodes.keys())
    assert any("check_convergence" in label for label in child_labels)
    assert any("remove_directory" in label for label in child_labels)
```

- [ ] **Step 8.2: Run tests to verify they fail**

Run: `/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pytest tests/test_vasp_job.py -v`

Expected: FAIL. `vasp_job` is still an `as_macro_node` taking `self`, so `pwf.node(vasp_job)` cannot build it.

- [ ] **Step 8.3: Rewrite `vasp_job`**

```python
@fr.workflow("vasp_output", "convergence_status")
def vasp_job(
    workdir: str,
    vasp_input: VaspInput,
    command: str = "mpiexec -n 1 vasp_std",
    files_to_be_deleted: list[str] | None = None,
    compress: bool = False,
    compressed_file_in_dir: bool = False,
    remove_calc_dir: bool = False,
    vasp_parser_function=None,
    vasp_parser_args: dict | None = None,
):
    """Run one VASP calculation and return its parsed output and convergence.

    pyiron_workflow 0.19 removed execution signals, so the ordering that the
    0.11 macro expressed with ``>>`` is rebuilt here as data dependencies. The
    ``after=`` arguments are ordering tokens that are never read by the
    receiving node; they exist only to create the edge.
    """
    wd_created = create_working_directory(workdir)
    wd_written = write_vasp_input_set(wd_created, vasp_input)
    shell_result = run_shell(command, wd_written)
    raw_output = parse_vasp_output(
        wd_written, vasp_parser_function, vasp_parser_args, after=shell_result
    )
    convergence = check_convergence(wd_written, after=shell_result)
    wd_cleaned = delete_files_recursively(
        wd_written, files_to_be_deleted, after=raw_output
    )
    wd_archived = compress_directory(
        wd_cleaned, compress, compressed_file_in_dir, after=convergence
    )
    vasp_output = remove_directory(wd_archived, remove_calc_dir, raw_output)
    return vasp_output, convergence
```

This exact graph was probed against flowrep 0.6.2 before the plan was written. It
parses, builds all eight children, runs, and produces the observed execution order:

```
create -> write -> shell -> {converge, parse} -> delete -> compress -> remove
```

Both safety properties hold: `parse` precedes `delete` (nothing is deleted before it
is read) and `converge` precedes `compress` precedes `remove` (the directory cannot
vanish before convergence is determined). `converge` and `parse` are unordered
relative to each other, which is correct — they are independent readers.
A `list[str] | None = None` default in the `@fr.workflow` signature is accepted.

- [ ] **Step 8.4: Run tests to verify they pass**

Run: `/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pytest tests/test_vasp_job.py -v`

Expected: PASS, 3 tests.

- [ ] **Step 8.5: Verify the graph shape by hand**

Run:

```bash
/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/python -c "
import pyiron_workflow as pwf
from pyiron_workflow_vasp.vasp import vasp_job
n = pwf.node(vasp_job)
print('inputs :', list(n.inputs.keys()))
print('outputs:', list(n.outputs.keys()))
print('children:', list(n.nodes.keys()))
"
```

Expected: outputs `['vasp_output', 'convergence_status']`, and children including `create_working_directory_0`, `write_vasp_input_set_0`, `run_shell_0`, `parse_vasp_output_0`, `check_convergence_0`, `delete_files_recursively_0`, `compress_directory_0`, `remove_directory_0`.

- [ ] **Step 8.6: Commit**

```bash
git add pyiron_workflow_vasp/vasp.py tests/test_vasp_job.py
git commit -m "feat: port vasp_job macro to flowrep workflow with data-threaded ordering"
```

---

### Task 9: Package exports and full-suite green

**Files:**
- Modify: `pyiron_workflow_vasp/__init__.py`
- Test: `tests/test_imports.py`

**Interfaces:**
- Consumes: every node from Tasks 3–8.
- Produces: the package's public surface, imported by `pyiron_workflow_assyst` in the follow-on plan.

- [ ] **Step 9.1: Write the failing test**

Append to `tests/test_imports.py`:

```python
EXPECTED_EXPORTS = [
    "VaspInput",
    "vasp_job",
    "create_working_directory",
    "write_vasp_input_set",
    "parse_vasp_output",
    "check_convergence",
    "generate_modified_incar",
    "generate_vasp_input",
    "construct_sequential_vasp_input",
    "run_shell",
    "delete_files_recursively",
    "compress_directory",
    "remove_directory",
    "is_line_in_file",
    "parse_vasp_directory",
]


def test_public_exports_present():
    import pyiron_workflow_vasp as pwv

    missing = [name for name in EXPECTED_EXPORTS if not hasattr(pwv, name)]
    assert missing == []


def test_no_legacy_names_remain():
    import pyiron_workflow_vasp as pwv

    for legacy in ("get_multiple_input", "submit_to_slurm", "isLineInFile", "remove_dir", "shell"):
        assert not hasattr(pwv, legacy), f"{legacy} should have been removed or renamed"
```

- [ ] **Step 9.2: Run tests to verify they fail**

Run: `/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pytest tests/test_imports.py -v`

Expected: FAIL on both new tests — the star-imports still expose legacy names.

- [ ] **Step 9.3: Write explicit exports**

Replace `pyiron_workflow_vasp/__init__.py`:

```python
"""pyiron_workflow_vasp — VASP nodes and parsers for pyiron_workflow 0.19."""

from pyiron_workflow_vasp.generic import (
    ShellOutput,
    compress_directory,
    delete_files_recursively,
    is_line_in_file,
    remove_directory,
    run_shell,
)
from pyiron_workflow_vasp.vasp import (
    VaspInput,
    check_convergence,
    construct_sequential_vasp_input,
    create_working_directory,
    generate_modified_incar,
    generate_vasp_input,
    parse_vasp_output,
    vasp_job,
    write_vasp_input_set,
)
from pyiron_workflow_vasp.vasp_parser import Outcar, parse_vasp_directory

__version__ = "0.2.0"

__all__ = [
    "Outcar",
    "ShellOutput",
    "VaspInput",
    "check_convergence",
    "compress_directory",
    "construct_sequential_vasp_input",
    "create_working_directory",
    "delete_files_recursively",
    "generate_modified_incar",
    "generate_vasp_input",
    "is_line_in_file",
    "parse_vasp_directory",
    "parse_vasp_output",
    "remove_directory",
    "run_shell",
    "vasp_job",
    "write_vasp_input_set",
]
```

Bump `version` to `0.2.0` in `pyproject.toml` to signal the breaking API change.

- [ ] **Step 9.4: Run the full suite**

Run: `/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pytest tests/ -v`

Expected: PASS, all tests across the four test files.

- [ ] **Step 9.5: Install into the target env and confirm**

```bash
/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pip install -e /cmmc/u/hmai/pip_repos/pyiron_workflow_vasp
/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/python -c "
import pyiron_workflow_vasp as pwv
print(pwv.__version__, len(pwv.__all__), 'exports')
"
```

Expected: `0.2.0 17 exports`.

- [ ] **Step 9.6: Commit and push**

```bash
git add pyiron_workflow_vasp/__init__.py pyproject.toml tests/test_imports.py
git commit -m "feat: explicit public exports and 0.2.0 version bump for the 0.19 port"
git push -u origin port/pyiron-workflow-0.19
```

---

---

### Task 10: Real VASP end-to-end validation in a clean environment

Every test up to here uses a stub shell command. This task proves the ported `vasp_job`
drives genuine VASP and that the in-repo parser reads genuine output — in an environment
built from the package metadata alone, so that a missing or undeclared dependency shows
up as a failure rather than being masked by a hand-tuned env.

**Files:**
- Create: `tests/integration/test_real_vasp.py`
- Create: `tests/integration/README.md`

**Interfaces:**
- Consumes: `vasp_job`, `VaspInput` from Task 8; package exports from Task 9.
- Produces: no code consumed by later tasks; this is a validation gate.

Verified prerequisites on this machine: VASP 6.4.3 at
`/cmmc/ptmp/hmai/vasp_compiled/intel_suite/vasp.6.4.3_intelsuite_march_znver4/bin/vasp_std`;
`~/.pyiron_vasp_config` present, selecting `potpaw64`/`GGA`; the Fe POTCAR readable at
`/cmmc/u/hmai/pyiron-resources-cmmc/vasp/potentials/pyiron_nodes/potpaw_64/GGA/Fe/POTCAR`;
SLURM partitions `s.cmmg` and `s.cmfe` available.

- [ ] **Step 10.1: Build a clean environment from package metadata only**

```bash
export PATH=/cmmc/ptmp/hmai/mambaforge/bin:$PATH
mamba create -y -p /cmmc/ptmp/hmai/mambaforge/envs/pwvasp_verify -c conda-forge python=3.12
/cmmc/ptmp/hmai/mambaforge/envs/pwvasp_verify/bin/pip install -e /cmmc/u/hmai/pip_repos/pyiron_workflow_vasp
/cmmc/ptmp/hmai/mambaforge/envs/pwvasp_verify/bin/pip install pytest
```

Installing with only `pip install -e .` is the point: if `pyproject.toml` under-declares a
dependency, the import fails here. Do not hand-install extra packages to make it pass —
if something is missing, add it to `pyproject.toml` and reinstall.

- [ ] **Step 10.2: Confirm the package imports in the clean env**

```bash
/cmmc/ptmp/hmai/mambaforge/envs/pwvasp_verify/bin/python -c "
import pyiron_workflow_vasp as pwv
print(pwv.__version__, len(pwv.__all__), 'exports')
"
```

Expected: `0.2.0 17 exports`. A `ModuleNotFoundError` here is a real dependency bug —
fix `pyproject.toml`, reinstall, and re-run.

- [ ] **Step 10.3: Write the real-VASP integration test**

Create `tests/integration/test_real_vasp.py`:

```python
"""End-to-end validation against a real VASP binary.

Skipped unless ASSYST_VASP_BINARY is set, so the unit suite stays hermetic.
"""

import os
import shutil

import pyiron_workflow as pwf
import pytest
from pymatgen.core import Lattice, Structure
from pymatgen.io.vasp.inputs import Incar

from pyiron_workflow_vasp import VaspInput, vasp_job

VASP_BINARY = os.environ.get("ASSYST_VASP_BINARY")

pytestmark = pytest.mark.skipif(
    not VASP_BINARY or not os.path.isfile(VASP_BINARY),
    reason="set ASSYST_VASP_BINARY to a real vasp_std to run this",
)


@pytest.mark.integration
def test_single_point_bcc_fe_against_real_vasp(tmp_path):
    """One BCC Fe atom, single point, coarse settings — seconds of runtime.

    Asserts on structure and sign of the result rather than an exact energy,
    so the test pins the plumbing without becoming a regression trap for
    POTCAR or VASP version changes.
    """
    workdir = str((tmp_path / "fe_bcc").resolve())
    incar = Incar.from_dict(
        {
            "ENCUT": 300,
            "ISMEAR": 1,
            "SIGMA": 0.2,
            "NSW": 0,
            "IBRION": -1,
            "ISPIN": 2,
            "PREC": "Low",
            "NELM": 30,
            "LWAVE": False,
            "LCHARG": False,
            "KSPACING": 0.5,
        }
    )
    structure = Structure(Lattice.cubic(2.83), ["Fe"], [[0.0, 0.0, 0.0]])

    command = f"srun -n 4 --hint=nomultithread {VASP_BINARY} > vasp.log 2>&1"

    run = pwf.node(vasp_job).run(
        workdir=workdir,
        vasp_input=VaspInput(structure=structure, incar=incar, potcar_paths=None),
        command=command,
        files_to_be_deleted=["CHG", "CHGCAR", "WAVECAR"],
        compress=False,
        compressed_file_in_dir=False,
        remove_calc_dir=False,
        vasp_parser_function=None,
        vasp_parser_args=None,
    )

    assert run.status == "finished", f"graph failed: {run.exception}"

    output = run.outputs.vasp_output
    assert output is not None, "parser returned nothing for a real VASP directory"

    energies = output["energy"] if "energy" in output else output.get("energies")
    assert energies is not None, f"no energy key in parsed output: {list(output)}"
    last = energies[-1] if isinstance(energies, (list, tuple)) else energies
    assert -20.0 < float(last) < 0.0, f"implausible energy for one Fe atom: {last}"

    assert not os.path.exists(os.path.join(workdir, "CHGCAR")), "cleanup did not run"
    assert os.path.exists(os.path.join(workdir, "OUTCAR")), "OUTCAR should survive"
```

Create `tests/integration/README.md`:

```markdown
# Integration tests

These run real VASP and are skipped by default.

    export ASSYST_VASP_BINARY=/cmmc/ptmp/hmai/vasp_compiled/intel_suite/\
    vasp.6.4.3_intelsuite_march_znver4/bin/vasp_std
    pytest tests/integration -v

Must run inside a SLURM allocation, since the command uses `srun`. Requires
`~/.pyiron_vasp_config` pointing at a readable POTCAR library.
```

- [ ] **Step 10.4: Run it for real inside an allocation**

```bash
cd /cmmc/u/hmai/pip_repos/pyiron_workflow_vasp
srun -p s.cmmg -n 4 --time=00:20:00 --pty bash -c '
export ASSYST_VASP_BINARY=/cmmc/ptmp/hmai/vasp_compiled/intel_suite/vasp.6.4.3_intelsuite_march_znver4/bin/vasp_std
/cmmc/ptmp/hmai/mambaforge/envs/pwvasp_verify/bin/pytest tests/integration -v -s
'
```

Expected: PASS. If the run fails, capture `vasp.log` and `OUTCAR` from the temp directory
before it is cleaned and report them — a genuine VASP failure here is far more
informative than any stub test and must not be papered over by loosening the assertions.

- [ ] **Step 10.5: Confirm the unit suite still skips cleanly without the env var**

```bash
/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pytest tests/ -v
```

Expected: all unit tests pass; the integration test reports as skipped.

- [ ] **Step 10.6: Commit**

```bash
git add tests/integration/ pyproject.toml
git commit -m "test: real VASP end-to-end validation in a clean environment"
```

---

## Self-Review

**Spec coverage.** The spec's "Repo 1" section lists: 9 `vasp.py` nodes (Tasks 6, 7, 8), 5 `generic.py` nodes (Tasks 3, 4), `vasp_parser/` untouched (Task 2 covers only the two defects found during verification), removal of `get_multiple_input` (Task 7) and `submit_to_slurm` (Task 5). The "no `os.chdir`" and "absolute workdirs" global constraints are enforced in Task 4 and asserted by `test_run_shell_does_not_mutate_process_cwd`. The spec's note that `outcar.py` needs raw docstrings and that `__all__` is broken is Task 2.

**Not covered here, by design:** the `pyiron_workflow_assyst` port and the `assystant` CLI. Those are separate plans, written after this one lands so their `Consumes` blocks can quote the signatures this plan actually produced rather than guessing.

**Type consistency.** Node names are used identically throughout: `run_shell` (not `shell`), `is_line_in_file` (not `isLineInFile`), `remove_directory` (not `remove_dir`), `create_working_directory`, `write_vasp_input_set`, `parse_vasp_output`, `check_convergence`, `generate_vasp_input`, `construct_sequential_vasp_input`. Task 9's `EXPECTED_EXPORTS` matches the names defined in Tasks 3, 4, 6, 7 and 8. Output port names are consistent between each node's `@fr.atomic("port")` declaration and the `run.outputs.port` access in its test.

**Verified in advance.** Two probes were run against the target environment before
this plan was written. The first confirmed the ASSYST graph shape parses and runs
(recorded in the spec). The second built the exact Task 8 `vasp_job` graph with
instrumented nodes and asserted the resulting execution order, confirming that the
`after=` token pattern genuinely sequences side effects and that `list[str] | None`
defaults are accepted. Task 8 is therefore specified against observed behaviour
rather than inference.

**Residual risk.** The tests use a stub shell command rather than real VASP, so this
plan proves the graph wiring, not the parser's behaviour on genuine VASP output. The
spec's end-to-end check — one tiny real structure, schema-compared against a
`df_ASSYST_jobs.pkl` from the 0.11 stack — belongs to the `pyiron_workflow_assyst`
plan, where the full ASSYST chain first exists.
