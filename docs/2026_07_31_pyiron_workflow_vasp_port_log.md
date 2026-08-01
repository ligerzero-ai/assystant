# SDD ledger — plan: /cmmc/u/hmai/pip_repos/assystant/docs/plans/2026-07-31-pyiron-workflow-vasp-0.19-port.md

Repo: /cmmc/u/hmai/pip_repos/pyiron_workflow_vasp
Branch: port/pyiron-workflow-0.19
Branch base: 8e4b669 (chore: ignore notebook checkpoints and caches)
Env: /cmmc/ptmp/hmai/mambaforge/envs/assystant

Pre-flight scan found 3 plan defects, all fixed in the plan before dispatch:
- __init__.py star-imports vasp -> blocks submodule imports for Tasks 3-5. Neutralised in Task 1 (new Step 1.4).
- vasp.py:17 imports pyiron_vasp at module level (not installed, undeclared). Deleted in Task 6.
- vasp.py:113 read_potcar_config() runs at import time; ~/.pyiron_vasp_config present on this box. Noted, not refactored.
Task 10 added: real VASP end-to-end in a clean env built from package metadata only.

Task 1: implemented (commit fdadd78, DONE_WITH_CONCERNS) — harness, 0.19 floors, __init__ neutralised.
  concern: pytest/pytest-cov pip-installed into assystant env (were absent) — accepted, pure dev deps.
  concern: Step 1.2 predicted ImportError(Workflow); actual ModuleNotFoundError(pyiron_vasp). Same root cause; Task 6 deletes that import.
  concern: os.chdir still at generic.py ~97,117 — that is Task 4's job, correctly out of scope here.
Task 1: review dispatched.
Wave A dispatched in parallel (disjoint files, isolated via worktree):
  - Task 2 (vasp_parser/) in worktree /cmmc/ptmp/hmai/wt-task2-parser on branch port/task2-parser
  - Tasks 3-5 (generic.py) on port/pyiron-workflow-0.19
Task 1: complete (commits 8e4b669..fdadd78, review clean — no Critical/Important against brief).
  Reviewer independently reproduced all output; confirmed filterwarnings is load-bearing (8 backslash occurrences across 6 lines in outcar.py).
Task 1: minor (deferred): tests/test_imports.py:2 `import flowrep as fr` is unused (F401). Came from the plan text, not the implementer. FOLD INTO TASK 9 (that task already edits test_imports.py).
Task 1: minor (deferred): fake_vasp_dir CHGCAR/WAVECAR are plaintext placeholders, not binary — adequate for deletion tests, thin if tarball tests ever need binary-safe paths.
Task 1: reviewer raised fixture breadth (no non-converged variant). ADJUDICATED NOT A GAP: Task 6's test_check_convergence_false_when_no_marker uses the empty abs_workdir fixture as the non-converged case. Coverage exists.
Task 2: implemented (commit 8960885 on port/task2-parser, DONE) — 6 docstrings fixed, vasp_parser/__all__ corrected.
  Implementer ran byte-level + tokenize scans across vasp_parser/, found no further invalid escapes.
  Scope clean: only vasp_parser/ touched. MERGE PENDING — held until the generic.py agent finishes to avoid a git index race.
Task 2: review dispatched (verifying raw-prefix vs docstring-text alteration, and independently re-running the escape scan).
Task 2: complete (commit 8960885, review clean — APPROVED, zero findings at any severity).
  Reviewer independently confirmed: all 6 sites used r-prefix (body text byte-for-byte unchanged, no doc regression);
  fresh tokenize + compile-gate scan over ALL 6 .py files in the package found 0 invalid escapes;
  __all__ verified accurate by AST; vasp_parser/ still free of any pyiron_workflow import.
  STILL PENDING: merge port/task2-parser -> port/pyiron-workflow-0.19 (waiting on generic.py agent).
MERGED port/task2-parser -> port/pyiron-workflow-0.19 at b9c7e6a. Full suite: 15 passed, 1 expected fail (Task 9 target).
Tasks 3-5: implemented (cc397f5 Task 3, d2d8d8e Task 4, 3cd1532 Task 5). 7/11/13 tests passing respectively.
Tasks 3-5: FINDING (Important, self-reported by implementer): test_run_shell_is_concurrency_safe is false assurance —
  it passes against the pre-fix os.chdir body, so it guards nothing. Asserting on `pwd` stdout cannot detect the race
  because subprocess.run(cwd=) scopes the child.
  Controller probed the true failure mode: the save/restore pair races (thread B captures thread A's workdir as its
  "original"), corrupting PROCESS cwd. Measured 27/30 trials corrupted with the old body. This was MY plan's test-design
  error, not the implementer's.
Tasks 3-5: fix round 1/5 dispatched — replace test to assert on os.getcwd() after concurrent runs; must be verified to
  FAIL against the old body and PASS against the new one; must restore CWD in finally to avoid poisoning later tests.
Tasks 3-5: fix round 1/5 (1 addressed, 0 open; commit 30f382b). Implementer verified BOTH directions:
  new test_run_shell_concurrent_calls_preserve_process_cwd FAILS against restored pre-fix os.chdir body (2/2 runs),
  PASSES 13/13 against ported body. CWD restored in finally. It is now a genuine regression test.
Tasks 3-5: task review dispatched over fdadd78..30f382b (vasp_parser/ excluded as already-approved Task 2 scope).
Tasks 6-8 (vasp.py) dispatched in parallel with that review — reviewer reads only, implementer writes only vasp.py + its tests.
Tasks 3-5: complete (commits fdadd78..30f382b, review clean — no Critical/Important).
  Reviewer INDEPENDENTLY reproduced the regression-test claim: pre-fix body -> FAILS 3/3 with
  "process cwd corrupted"; ported body -> 13/13 pass. finally-restore genuinely protects the pytest process.
  Controller's self-inclusion suspicion about compress_directory was INVESTIGATED AND DISPROVEN by the reviewer:
  os.walk sees the empty tarball but the exclusion check drops it before content is added. Code is correct.
Tasks 3-5: minor (deferred): generic.py:175-176 compress_directory excludes by BARE FILENAME
  (file == f"{base}.tar.gz") where the original compared the full path. A legitimately-named file in a
  SUBDIRECTORY sharing the tarball basename is silently dropped from the archive. Behavioural deviation from
  the original, unlikely with real VASP filenames. -> RAISE AT FINAL REVIEW.
Tasks 3-5: minor (deferred): tests/test_generic.py:34-42 does not assert the tarball excludes ITSELF.
  Property verified manually by the reviewer but unguarded going forward. -> RAISE AT FINAL REVIEW.
Tasks 3-5: minor (deferred): dead imports (Optional, fnmatch) left in generic.py — correct restraint, cleanup deferred.
CONTROLLER ERROR (deferred): merge commit b9c7e6a is authored "Han Mai", not "Han Lin Mai" — I ran `git merge`
  without -c user.name. Do NOT rewrite history now (an agent is committing on this branch). Fix at branch-finish.
Tasks 6-8: implemented (62ceb41 Task 6, 378739e Task 7, 8f197c4 Task 8). Suite: 27 passed, 1 expected fail.
  flowrep child labels for vasp_job confirmed EXACTLY as the controller's pre-plan probe predicted:
  create_working_directory_0, write_vasp_input_set_0, run_shell_0, parse_vasp_output_0, check_convergence_0,
  delete_files_recursively_0, compress_directory_0, remove_directory_0.
  UNPLANNED CHANGE by implementer: write_POSCAR/get_default_POTCAR_paths need ase.Atoms but the brief's fixture
  supplies a pymatgen Structure; implementer normalised via AseAtomsAdaptor at the write_vasp_input_set call site.
  Flagged to reviewer as the central quality question (right layer? does it lose per-site MAGMOM? tested or
  incidental?) — per-site magmoms matter for downstream ASSYST campaigns.
Tasks 6-8: task review dispatched (opus — ordering correctness is the crux; asked for empirical edge/order
  verification, not source reading).
Task 9 (exports) dispatched in parallel — disjoint files (__init__.py, pyproject.toml, test_imports.py); reviewer reads only.
  Folded in the deferred Task 1 minor: remove unused `import flowrep as fr` from test_imports.py.
  Instructed to SKIP the brief's `git push`.
Task 9: implemented (commit 1447858). Suite 30 passed / 0 failed. 17 exports, matching Step 9.5 exactly.
  Test count 30 explained: Task 9's brief adds 2 tests to test_imports.py (3->5). 13 generic + 9 vasp_nodes + 3 vasp_job + 5 imports = 30.
  Controller's "expect 28" was arithmetic error on my part, not an agent deviation.
!! OPEN ITEM — MUST VERIFY BEFORE FINAL REVIEW:
  Working tree has an UNCOMMITTED edit to vasp.py removing `after=convergence` from the compress_directory call.
  This is the Tasks 6-8 REVIEWER running a negative control (does the ordering guarantee actually depend on that edge?).
  Committed state 8f197c4 HAS the edge; Task 9's commit staged only its own 3 files so it was NOT captured.
  ACTION: after the reviewer reports, confirm `git diff --stat` is clean and the edge is present in HEAD.
  If the reviewer exits without restoring, run: git checkout -- pyiron_workflow_vasp/vasp.py
Task 9: minor (deferred): untracked pyiron.log runtime artifact from the e2e test — add to .gitignore at branch finish.
RESOLVED open item: reviewer restored vasp.py; worktree clean, `after=convergence` present at vasp.py:317 in HEAD.
Tasks 6-8: REVIEW FOUND 3 CRITICAL + 1 IMPORTANT. Ordering itself verified CORRECT (reviewer dumped Macro.edges AND
  instrumented every node body with delays; observed create->write->shell->converge->parse->delete->compress->remove;
  check_convergence is a transitive ancestor of remove_directory, so the race cannot occur).
  C1 (test vacuous): test_vasp_job_graph_orders_removal_after_convergence only checks label substrings. Mutation-tested:
     deleting after=convergence still passes all 3 tests. PLAN BUG (mine).
  C2 (construct_sequential_vasp_input broken): parse_vasp_directory returns a DataFrame of Structure.to_json() STRINGS;
     brief prescribed vasp_output["structures"][-1], dropping the Structure.from_str(fmt="json") + AseAtomsAdaptor
     round-trip the pre-port original had. Sequential ISIF7->5->2 chaining is DEAD. Zero coverage. PLAN BUG (mine).
     -> load-bearing for the pyiron_workflow_assyst port that follows.
  C3 (None default crashes): vasp_job defaults files_to_be_deleted=None; delete_files_recursively has no None guard.
     Reproduced by controller: TypeError. Pre-port default was ["CHG","CHGCAR","WAVECAR"]. PLAN BUG (mine).
  IMPORTANT: AseAtomsAdaptor normalisation is at the wrong layer AND mutates the caller's VaspInput in place
     (contradicts the copy-don't-mutate care in generate_modified_incar; 0.19 runs siblings concurrently).
     -> move into VaspInput.__post_init__.
  DOCUMENTED, NOT CHANGED: per-site MAGMOM on the input structure never reaches the INCAR. Pre-existing; ASSYST sets
     MAGMOM as an explicit INCAR string so behaviour is correct in practice, but it needs a written contract.
Tasks 6-8: fix round 1/5 dispatched (scope expanded to generic.py + .gitignore). Mutation-test proof required for C1.
Tasks 6-8: fix round 1/5 (all 3 Critical + 1 Important + all minors addressed; commit dbce3fb). Suite 34 passed / 0 failed.
  C1: ordering test rewritten to compute transitive ancestors from node.edges; implementer mutation-tested both directions.
  C2: JSON round-trip restored. NOTABLE DISCOVERY: pymatgen's orjson reader REJECTS numpy.str_ despite it subclassing
      str, so an explicit str() coercion was required. Added DataFrame-shaped test asserting last-ionic-step structure.
  C3: None guarded in generic.py::delete_files_recursively; test added that omits files_to_be_deleted.
  IMPORTANT: normalisation moved into VaspInput.__post_init__; in-place mutation of caller's object removed.
  MAGMOM limitation documented in docstring, behaviour deliberately unchanged.
Tasks 6-8: scoped re-review dispatched (must independently re-run the C1 mutation test and MUST leave tree clean).
Task 10 (real VASP in clean env) dispatched in parallel — creates tests/integration/ only; reviewer reads only.
  Instructed: pip install -e . ONLY, so a missing dep fails loudly; do not weaken assertions; sbatch fallback if
  interactive srun is unobtainable.
Tasks 6-8: re-review — ALL findings ADDRESSED, no new breakage. Reviewer independently reproduced the C1 mutation test
  (edge removed -> FAIL, restored -> PASS) and independently confirmed the orjson/numpy.str_ claim is real, not masking
  another bug. Confirmed __post_init__ rebinds only the new instance and never touches the caller's object.
  Tree verified clean and byte-identical to dbce3fb.
Tasks 6-8: complete (commits 30f382b..dbce3fb, review clean after 1 fix round).
Task 9: complete (commit 1447858, verified by suite going 27+1fail -> 30 pass and 17 exports).
Task 10: complete (commit 03459fb). REAL VASP RAN under SLURM 21618940 (s.cmfe, 4 tasks): converged,
  TOTEN = -6.566 eV, E0 = -6.560 eV for one BCC Fe atom — physically sound.
  One legitimate retry: first job crashed in MPI_Init because the sbatch script lacked the Intel modules the
  binary is linked against; fixed by loading intel/2024.0 impi/2021.11 mkl/2024.0.
  Clean env from `pip install -e .` alone resolved everything — NO missing dependency. 17 exports confirmed.
!! CRITICAL FOUND BY THE REAL TEST — the whole reason for running it:
  vasp_parser/outcar.py:43 calls zopen(filename, "r"); monty 2026.7.16 raises
  RuntimeError("Implicit text/binary mode is not allowed"). Controller reproduced independently.
  output.py has TWELVE bare `except:` clauses (69,79,86,299,314,319,324,329,334,352,357,532) that swallow it and
  substitute np.nan. So parse_vasp_directory SILENTLY returns NaN for energy/forces/stresses/magmoms/structures on
  every genuine OUTCAR. An ASSYST campaign would produce all-NaN datasets and appear to have succeeded.
  Survived the entire port because NO unit test ever fed the parser a real OUTCAR — the stubs hid it.
  Task 10 agent correctly refused to pin monty to mask it and left the integration test failing. Right call.
Task 11 dispatched: fix zopen mode; add a VASP-free regression test on a committed real OUTCAR fixture asserting
  FINITE energy (not merely "no exception" — the exception is swallowed); convert bare excepts to
  `except Exception:` + warnings.warn so parse failures stop being invisible. No monty pin.
Task 11: complete (377b4c6 fix, 0e77a87 regression test). Suite 37 passed / 1 skipped.
  zopen fixed to explicit "rt"; all 12 bare excepts -> `except Exception as e` + warnings.warn naming the failed field.
  Regression test PROVEN: reverting zopen makes all 3 new tests FAIL (one on raw RuntimeError, two on
  "energy came back as a bare NaN"); restoring -> 37 pass. Real OUTCAR fixture committed so this needs no VASP.
  No monty pin added — explicit mode works on old and new monty alike.
!! CONTROLLER RE-RAN THE REAL INTEGRATION TEST — NOW PASSES (SLURM 21619048, s.cmfe, 4 tasks, 5.3s).
  Task 11's agent could not: cmmg QOS saturated (37,720 CPU running vs 35,840 cap, user's own SMAX campaign).
  s.cmfe had headroom. NOTE for future runs: sbatch --output MUST go to shared storage (/cmmc/ptmp), not /tmp —
  /tmp is node-local and a first attempt (21619038) completed with its output stranded on the compute node.
  Real parsed values, all finite: energy=-6.56617606, energy_zero=-6.56047397, forces=[[0,0,0]] (symmetry-correct
  for one atom at origin), stresses=-179.32493 isotropic, scf_convergence=True, convergence=True, real structure JSON.
  This is the end-to-end proof that the ported vasp_job + parser work on genuine VASP output.
  minor (deferred): magmoms=[] despite ISPIN=2 (LORBIT unset, so OUTCAR carries no per-ion moments) — expected, noted.
  minor (deferred): PytestUnknownMarkWarning for @pytest.mark.integration — register the marker in pyproject.toml.
FINAL WHOLE-BRANCH REVIEW (opus, 16 commits): APPROVE WITH FIXES.
  Ordering re-verified at HEAD by dumping pwf.node(vasp_job).edges — all 3 guarantees hold. No regression.
  API judged SOUND to build ASSYST on, conditional on the fixes below.
  NEW F1 (Important): compress_directory changed tarball arcname from directory-rooted (calc/OUTCAR) to FLAT
    (OUTCAR). Archives from this branch extract into CWD; all prior campaign archives are directory-rooted.
    On-disk FORMAT SPLIT that ASSYST would inherit. Was in no ledger entry.
  NEW F2 (Important): the warnings added in Task 11 SPAM and misfire — measured 40 warnings over 20 normal dirs,
    2/dir, none deduplicated (path embedded in message defeats Python's dedup filter) => ~48k on a 24k-job campaign.
    Two fire on DESIGNED fallback paths (missing vasprun.xml, missing POTCAR in archived dirs), not failures.
    output.py:83 and :94 are UNREACHABLE (is_line_in_file swallows FileNotFoundError internally).
  NEW F3 (Important): THIRD vacuous test found — test_vasp_nodes.py:110 asserts isinstance on a local the test
    itself bound; cannot fail. Reviewer mutation-proved: making __post_init__ mutate the caller's object still
    passed all 12 tests. (Running total: 3 vacuous tests found across this branch. All were MY plan's test designs.)
  NEW F4 (downstream-critical): construct_sequential_vasp_input uses .iloc[0] = OLDEST row. parse_vasp_directory
    returns one row per OUTCAR* plus one per custodian error archive, sorted ASCENDING by calc_start_time. ASSYST
    runs custodian with max_errors=10, so a restart would feed the CRASHED structure into the next ISIF stage.
    -> switch to .iloc[-1] + two-row test.
  Minor 6 (magmoms=[] under ISPIN=2): ACCEPTED — confirmed correct. outcar.py:565 only fills magmoms inside
    `if local_spin_trigger`, set solely by the Wigner-Seitz block that VASP prints only for LORBIT>=10/RWIGS.
  Minors 1-5: all fix. Minor 4 (merge commit b9c7e6a authored "Han Mai") -> CONTROLLER handles via history rewrite
    after the fix wave lands; agent explicitly told not to touch history.
  Follow-ups recorded, NOT fixed (pre-existing, not port regressions): output.py:552-554 assigns kpoints/INCAR
    positionally after sorting => mis-pairing; output.py:259 np.isnan() on a dict; unit suite not hermetic
    (imports vasp.py -> read_potcar_config at module scope; test_vasp_job passes potcar_paths=None -> reads real POTCARs).
Fix wave dispatched (single consolidated dispatch per skill; F3 requires mutation proof).
Fix wave: complete (2780429 F1/F1b + dead imports, 6728930 F2 + pytest marker, 640d057 F4 + F3 + layering comment).
  Suite 41 passed / 1 skipped (was 37+1). F3 mutation-proven: __post_init__ mutating caller -> FAIL (volume 45.33
  vs 22.67), reverted -> PASS.
  Scoped re-review dispatched. Asked for TWO things beyond verdicts: (a) confirm F2's guard did not silence GENUINE
  failures — a corrupt-but-present vasprun.xml must still warn; (b) mutation-test F4 by reverting to .iloc[0].
Re-review of fix wave: ALL 5 findings ADDRESSED, no new breakage. MERGE APPROVED.
  F2 verified BOTH ways: OUTCAR-only dir (no vasprun.xml, no POTCAR) -> 0 warnings; genuinely CORRUPT vasprun.xml
  -> still warns (XMLSyntaxError). The guard did not silence real failures.
  F4 mutation-tested: reverting to .iloc[0] makes the new two-row test FAIL (3.9 A vs 3.5 A).
  F3 mutation-tested: __post_init__ mutating caller -> FAIL (45.33 vs 22.67).
BRANCH FINISHED:
  - Rewrote history to fix the one mis-authored commit. Root cause: global git config user.name = "Han Mai";
    the 18 good commits set it explicitly, my `git merge` inherited the config. All 19 now "Han Lin Mai".
    Verified `git diff backup/pre-author-fix HEAD` is EMPTY (content identical) and suite still 41 passed / 1 skipped.
    Backup ref kept at backup/pre-author-fix.
  - Worktree /cmmc/ptmp/hmai/wt-task2-parser removed; branch port/task2-parser deleted (merged).
  - PUSHED to origin (ligerzero-ai/pyiron_workflow_vasp), branch port/pyiron-workflow-0.19. NOT merged to main.
    NOTE: this repo is a FORK of pyiron/pyiron_workflow_vasp — git's suggested PR URL points at UPSTREAM.
    Do not open a PR against the pyiron org by accident.
FINAL STATE: 19 commits, 41 tests passed + 1 skipped (real-VASP, verified passing under SLURM 21619048).
