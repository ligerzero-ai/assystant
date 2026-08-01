# SDD ledger — plan: /cmmc/u/hmai/pip_repos/assystant/docs/plans/2026-08-01-pyiron-workflow-assyst-0.19-port.md

Repo: /cmmc/u/hmai/pip_repos/pyiron_workflow_assyst
Branch: port/pyiron-workflow-0.19
Branch base: 9e7c55a547959fbf59312ea9209ead8edad4887f
Env: /cmmc/ptmp/hmai/mambaforge/envs/assystant
Depends on: pyiron_workflow_vasp 0.2.0 (branch port/pyiron-workflow-0.19, complete)

Pre-port cosmetic edit to workflow.py stashed ("pre-port cosmetic edit") — file is rewritten in Task 5 anyway.

KEY DECISION (user-approved 2026-08-01): RCORE is DERIVED FROM THE POTCAR ACTUALLY USED, not a hardcoded table.
  Evidence: repo HEAD's dict holds PLAIN-POTCAR radii (Ti=1.4817, Cr=1.3229, Zr=1.5875 A) but the code writes
  SEMICORE variants. Verified by writing real POTCARs: Ti_sv/Cr_pv/Zr_sv/V_sv/Mo_sv, whose RCORE matches the
  INSTALLED dict 7/7 (Ti=Cr=1.2171, Zr=Mo=1.3229). Every past campaign ran the installed values.
  Porting repo HEAD unchanged would have made the filter STRICTER than all existing data, using radii for
  POTCARs that are never written. Deriving from the POTCAR is correct for both default and custom selections.
  NOTE: repo's structure_filter_utils.py has only ONE dict (git log: single commit) — the duplicate-dict finding
  recorded in reference_assyst_overlap_rule applies to the INSTALLED snapshot only.

VERIFIED BEFORE PLANNING (probed live, not assumed):
  - A @fr.workflow (vasp_job) can be called INSIDE another @fr.workflow, its TWO outputs tuple-unpacked, and that
    call placed inside a zip accumulator loop. Probe built for_each_0/for_each_1 and returned correct counts.
    This is the crux of the ASSYST port.
  - Full pyiron_workflow_vasp 0.2.0 signatures quoted verbatim into the plan.

Task 1: complete (commit c815bd6). 2 pass + 1 expected fail (test_assyst_exports_present -> Task 6).
!! MAJOR CORRECTION, found by the Task 1 implementer and independently verified by the controller:
  My plan's stated bug mechanism ("MIC fails in heavily sheared cells") is WRONG. Over ~4000 randomised
  skewed cells, pymatgen distance_matrix is numerically ROBUST for genuine cross-atom pairs.
  THE REAL DEFECT: distance_matrix covers only CROSS-atom pairs, and np.fill_diagonal(dm, inf) discards
  every atom's distance to its OWN PERIODIC IMAGE. Controller-measured consequences:
    - 1-ATOM CELL RETURNS inf -> the min_dist floor is COMPLETELY INERT. A 1-atom cubic H cell with
      a=0.9 A passes a min_dist=1.0 floor. ASSYST unary generation runs min_atoms=1, so this reached
      production and the floor has never rejected a single one-atom structure.
    - Short lattice vector hides contacts in multi-atom cells too: 2-atom cell with a 0.95 A lattice
      vector reports min 4.2426 A vs a true 0.95 A contact.
  Mitigation that limited real damage: filter_distance_by_species uses get_all_neighbors, which DOES
  see self-images, so the RCORE half of the filter caught most of these. Only the flat floor was blind.
  ACTIONS: plan Task 3 reframed with correct mechanism + 2 new tests (single-atom inert, short-vector);
  memory reference_assyst_overlap_rule corrected.
  The brief's literal sheared_cell lattice produced NO discrepancy (mic=d_true=0.06); the implementer
  derived a working one (mic=1.4824, d_true=0.9000) and documented the true mechanism in its docstring.

Tasks 2-3: implemented (2886151 RCORE-from-POTCAR, ef46186 min-distance fix). Suite 14 passed / 1 expected fail.
  Controller verified the implementation reproduces the measured values EXACTLY:
    1-atom a=0.9 -> mic=inf, neighbor_list=0.9 ; short-vector -> mic=4.2426, neighbor_list=0.95 ;
    is_valid_structure(tiny, min_dist=1.0) = False under the default backend.
  MY BRIEF WAS WRONG TWICE, both caught by the implementer:
   (a) get_default_POTCAR_paths signature. Real:
       get_default_POTCAR_paths(structure: Atoms, pseudopot_lib_path: str, pseudopot_functional="GGA", potcar_df=...)
       -> needs an ase.Atoms and a required lib path, NOT a symbol list. Flagged as unverified in the plan; adapted.
   (b) THREE Task-3 assertions were empirically FALSE: filter_distance_by_species's RCORE check independently
       rejects those fixtures regardless of backend, so `is_valid_structure(..., min_dist_backend="mic") is True`
       could never hold. Implementer replaced them with structures that ISOLATE the backend property rather than
       relaxing the assertions. -> reviewer explicitly tasked with mutation-testing whether the replacements
       genuinely catch "default silently becomes mic", since rewriting an unsatisfiable test is how suites rot.
  Noted for Task 5: workflow.py still imports the now-removed RCORE name (dead until __init__ is restored).
Tasks 2-3 review dispatched (opus; science-critical). Task 4 (perturb.py) dispatched in parallel — disjoint file.

Task 4: complete (commit c62afee). 7/7 perturb tests; suite 21 passed / 1 expected fail.
  Mutation-proven: bypassing is_valid_structure (`if True:`) made the contract test FAIL (caught an invalid Fe
  candidate with c=0.997 A); restoring made it PASS. The generator's "everything it emits passes the filter"
  contract is genuinely guarded.
Task 5 (graph port) dispatched with live-verified signatures for vasp_job / generate_vasp_input /
  construct_sequential_vasp_input / get_ASSYST_deformed_structures, plus the two carried-over corrections
  (.iloc[-1] most-recent row; str() coercion before Structure.from_str) and the RCORE import cleanup.
  Runs in parallel with the Tasks 2-3 review (disjoint files; reviewer restores anything it mutates).

Tasks 2-3 REVIEW (opus): no Critical. Implementation verified sound and thorough:
  - All 7 reference RCORE values reproduced via a GENUINE POTCAR read (zero warnings), <5e-5 A. Same for a
    7-element mixed structure. Reviewer additionally checked ALL 70 RCORE_FALLBACK rows vs their default
    POTCARs: 0 mismatches.
  - Parsing robustness: scanned all 343 POTCARs in the library; TITEL/RCORE alternate strictly so zip cannot
    mis-pair. H_AE (TITEL, no RCORE) correctly raises rather than mis-aligning. 5-element concatenation parses
    identically to five singles.
  - mic branch is behaviourally bit-exact with the old code -> the reproducibility guarantee genuinely holds.
  - New default backend exact against a +-6-image brute force on 284 random skewed cells (worst error 0.0).
  - THE THREE REPLACED TESTS ARE HONEST, NOT WEAKENED: reviewer mutated the default backend to "mic" and TWO of
    the replacements caught it, where the brief's originals would NOT have. My assertions were genuinely
    unsatisfiable (RCORE rejects those fixtures independently: allowed H-H = 0.9314 A > 0.9 A).
  DATASET-IMPACT NUMBER worth keeping: mic vs neighbor_list flip the <1 A verdict on 12/284 (~4%) of random
  skewed cells. That is the scale of the change to which structures enter training data.
  IMPORTANT 1: get_minimum_distance's OWN default backend is unpinned — mutating it to "mic" leaves the whole
    suite green (every test passes backend= explicitly). Public function; bare callers silently regress.
  IMPORTANT 2: nothing distinguishes a real POTCAR read from the fallback, because RCORE_FALLBACK is by
    construction identical to the POTCAR values. Forcing the default branch to raise leaves the suite green.
    A genuine read failure (missing dep, misconfigured library, potpaw_54 site) is invisible to CI.
  Reviewer restore confirmed: git diff ef46186 -- (its files) is EMPTY.
Tasks 2-3: fix round 1/5 dispatched (2 Importants + 2 cheap minors; 4 minors recorded as follow-ups).

Tasks 2-3: fix round 1/5 (both Importants + both cheap Minors addressed; commit a1b4869).
  3 new tests: default-backend pinned; default POTCAR path emits no warning; an element ABSENT from
  RCORE_FALLBACK (Ce) resolves, which only a genuine read can achieve. Filter tests 12 -> 15.
  BONUS change not requested: same lazy-fallback treatment applied to the potcar_paths branch, which the
  implementer says was eagerly evaluating RCORE_FALLBACK[sym] even for symbols that resolved fine (would
  KeyError on an off-table element like Ce even when the fallback was unused).
  -> re-reviewer explicitly tasked with scrutinising this unrequested change across all four resolve/fallback
     combinations, since unrequested changes are where regressions hide.
Tasks 2-3: scoped re-review dispatched. Runs concurrently with Task 5 (disjoint files; reviewer warned that
  workflow.py / test_collect.py / test_workflow.py dirt belongs to the Task 5 agent).

Tasks 2-3: re-review APPROVED — all 4 findings ADDRESSED with independently reproduced mutation proof.
  Bonus potcar_paths lazy fix SCRUTINISED and CONFIRMED REAL: old code raised bare KeyError('Ce') when Ce
  resolved but a different required element (Fe) was missing. All 4 resolve/fallback combinations verified.
  All 7 campaign reference values still reproduce via real reads with zero warnings. mic branch byte-identical.
Tasks 2-3: complete (commits c815bd6..a1b4869, review clean after 1 fix round).

Task 5: implemented (commit 3877e29) — but see CRITICAL below. Graph children:
  vasp_job_0 (ISIF7), vasp_job_1 (ISIF5), vasp_job_2 (ISIF2), collect_structures_0, for_each_0 (base fan-out),
  get_ASSYST_deformed_structures_0, for_each_1 (permutation fan-out), concat_and_save_0, plus
  set_ionic_steps_0 / generate_modified_incar_{0-3} / join_path_{0-2} / build_parser_args_{0-2} /
  construct_sequential_vasp_input_{0-1} / constant_*.
!! CRITICAL (controller-found): the DEFAULT configuration crashes.
  UPSTREAM BUG, reproduced independently by the controller: pyiron_workflow 0.19.0's ForEach/Transform1toN
  wraps every element in a spurious 1-TUPLE when a zip-loop iterable has length EXACTLY 1. Lengths 2,3 fine.
    n=1 -> body gets {'type':'tuple','value':(0,),'name':('n0',)} ; n=2 -> correct ints/strs.
  WHY CRITICAL: image_selection_eVatom_threshold DEFAULTS to -1 = "keep only the final image", so
  collect_structures returns EXACTLY ONE base structure in the normal case. That is the production path.
  Controller ran the real graph with a single-image parser + default threshold: CRASHES with
    TypeError: join() argument must be str, bytes, or os.PathLike object, not 'tuple'  (workflow.py:113 join_path)
  The agent's claimed workaround does not exist in the code. Its e2e test passes ONLY because
  tests/test_workflow.py:58 overrides the threshold to 0.1, selecting TWO images and dodging the bug entirely.
  A test that avoids the broken default reads as coverage while providing none.
  Fix round 1/5 dispatched: explicit documented unwrap_singleton workaround on both loop bodies; e2e test must
  use the DEFAULT threshold; second test keeps the multi-image path; mutation proof required showing the
  default-threshold test fails without the workaround while the multi-image one passes either way.
TODO FOR USER: report the ForEach length-1 bug upstream to pyiron/pyiron_workflow (needs their consent to file).

Task 5: fix round 1/5 (Critical addressed; commit c5335bc). unwrap_singleton added with docstring + module-level
  comment naming affected version, symptom, root cause and reproducer; applied at the top of BOTH loop bodies.
  e2e test now uses the DEFAULT threshold (-1); a second test keeps the multi-image path. Mutation-proven with
  the required asymmetry: removing the workaround fails the default-threshold test with the exact TypeError while
  the multi-image test stays green.
  CONTROLLER RE-VERIFIED INDEPENDENTLY with the original reproducer: default path status=finished, workdirs
  [ISIF7, ISIF5, ISIF2, ISIF2_accur_relaxstep0, +rattle_1/shear_1/triax_1], train_df 4 rows.
Task 5: review dispatched (opus). Asked specifically whether the workaround is COMPLETE — the base loop is the
  one that crashed, so the permutation loop's coverage may be incidental; reviewer must construct a
  single-surviving-permutation case.
Task 6: complete (commit 0c26f50). 33 passed / 0 failed. 13 exports; RCORE deliberately gone (replaced by
  resolve_rcore). unwrap_singleton and build_parser_args correctly EXCLUDED from __all__ (temporary workaround /
  internal wiring) but still reachable via the module. New test iterates __all__ and asserts each name resolves —
  guards the "__all__ advertises a nonexistent name" defect that hit the sibling package.

Task 5: fix round 2/5 (Critical + 2 Importants + 4 minors; commit 7b8bbe4). Suite 33 -> 35 passing.
  Implementer's mutation table claims all 6 physics mutations now caught; re-reviewer tasked with
  INDEPENDENTLY re-running all six rather than trusting the table.
  IMPORTANT 1 confirmed real by the reviewer BEFORE the fix: deleting only the perm-loop unwraps left the
  suite green (default perm loop has length 3), so its coverage WAS incidental — as suspected when dispatched.
  New test at n_rattle=1, n_stretch=0 now guards it.
  IMPORTANT 2: collect_structures now raises a named NoConvergedImagesError instead of dying with an opaque
  IndexError deep in flowrep. Realistic path: ISIF2 final image with scf_convergence False.

USER DECISIONS (2026-08-01):
  1. ionic_steps: KEEP UNIFORM (all three ISIF stages get ionic_steps). This is a DELIBERATE deviation from
     every past campaign. Pre-port behaviour applied ionic_steps ONLY to ISIF7 (workflow.py:358-360) while
     ISIF5/ISIF2 used the raw INCAR NSW; in production that was ISIF7 NSW=100/200 vs ISIF5/ISIF2 NSW=500
     (calculate_assyst.py:256,270). New data is therefore NOT directly comparable to existing datasets for
     the ISIF5/ISIF2 stages. MUST be documented prominently in the workflow docstring.
  2. Upstream bug: FILED as https://github.com/pyiron/pyiron_workflow/issues/915
     "ForEach: zip loops over length-1 iterables wrap elements in a spurious 1-tuple (length-0 raises IndexError)"
     Reproducer verified in a clean env containing only pyiron_workflow + deps.
     -> the unwrap_singleton comment in workflow.py should cite this issue URL so whoever removes the
        workaround knows exactly what to re-test.
Task 5: scoped re-review of fix round 2 dispatched.

Task 5: fix round 2 RE-REVIEW — all findings ADDRESSED, no new breakage, MERGE APPROVED.
  Reviewer INDEPENDENTLY re-ran all six physics mutations; its table matched the implementer's exactly, and
  no mutation left the suite green. Confirmed the perm-loop unwrap test is genuinely load-bearing (deleting
  only those unwraps fails exactly that one test while the two e2e tests stay green).
  Exercised NoConvergedImagesError through the REAL graph (not a direct call): raised cleanly through flowrep's
  DAG executor with job name and both counts in the message.
  Brittleness check on the new disk-reading assertions: pytest.approx for floats (INCAR round-trip exact,
  no drift), bool/int type identity verified as real types not strings, no dict-ordering dependence,
  tmp_path-derived paths with no cross-test collision. No spurious-failure risk.
Task 5: complete (commits 3877e29..7b8bbe4, review clean after 2 fix rounds).
Task 7 (docs-only): complete (commit 6c3bb19). ionic_steps deviation documented in the workflow docstring as a
  knowing, dated, user-approved change so a future reader does not "fix" it back; issue 915 URL cited in both
  the module comment and unwrap_singleton's docstring; length-0 IndexError behaviour recorded alongside length-1.
  Verified docstring/comment-only: no executable line altered.
FINAL WHOLE-BRANCH REVIEW dispatched (opus, 10 commits). Asked explicitly whether a training dataset produced
  by this package would be trustworthy, and to assess resolve_rcore's per-candidate POTCAR disk reads inside
  perturb.py's retry loop at campaign scale.

FINAL WHOLE-BRANCH REVIEW (opus, 10 commits): APPROVE WITH FIXES.
  Implementation verified CORRECT from artefacts on disk (not from the suite): ISIF chain feeds forward
  2.83 -> 2.90 -> 2.90; statics INCARs carry KSPACING 0.25 / EDIFF 1e-5 / EDIFFG 1e-4 / LREAL False / NSW 0;
  NO KPOINTS file written anywhere so KSPACING is genuinely live; permutations genuinely deformed
  (vol 21.7/40.1/59.9 vs 24.4); no unsanctioned drift vs the pre-port macro.
  C1 (Critical): the user-approved uniform-ionic_steps deviation COULD NOT FAIL — fixture had raw NSW=5 == 
    ionic_steps=5, so the assertion passed under BOTH semantics. Reverting the change left 35/35 green.
  C2 (Critical): statics on the WRONG GEOMETRY was invisible. base_structure->structure (statics on the
    unrelaxed input) and perm_structure->structure (statics on the undeformed cell) each left 35/35 green.
    Most dataset-corrupting silent failure in the package; entirely unguarded.
  I1/I2/I3/I4 + minors as recorded. Filter performance measured and NOT a concern (lru_cache 2599 hits/1 miss;
    is_valid_structure ~8-9 ms dominated by neighbour searches; negligible vs DFT).
FIX WAVE: commits ce8b802, 4f7093c, cca1924, 4c73c96. Suite 35 -> 37.
  A MINOR CASCADED INTO A REAL BUG: fixing the "ISIF not in incar tests the fixture" minor (by adding ISIF to
  the fixture) exposed that accurate_incar was LEAKING a caller-supplied ISIF into the statics INCARs.
  Every real campaign INCAR carries ISIF, so this was live. Fixed with strip_isif(); re-reviewer confirmed the
  leak was real against 6c3bb19, the fix correctly scoped (pops only ISIF), load-bearing and non-vacuously tested.
RE-REVIEW: APPROVE. All Critical/Important mutation-verified ADDRESSED by the reviewer's own mutations.
  I2 verified with a REAL non-convergent run (all three flags False across all 4 rows), not just column presence.
  I3 kwargs checked against inspect.signature rather than eyeballed.
  concat_and_save tested with disjoint schemas: pd.concat outer-joins, convergence columns broadcast correctly.
  I4 PARTIALLY ADDRESSED (non-blocking): assertions are now genuinely falsifiable, but the test's rationale
    comment overclaims — widening max_attempts 200x never fails it, because shear is geometrically infeasible
    for that fixture at any budget rather than cap-limited. -> DEFERRED: correct the comment.
FINAL STATE: 15 commits, all authored "Han Lin Mai", 37 tests passing, tree clean.
