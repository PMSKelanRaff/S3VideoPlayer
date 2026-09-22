# D6433 PCI architecture

This document tracks the ASTM D6433 Pavement Condition Index implementation. It's
the reference for anyone picking this work up: what exists, what's still blocked on
licensed reference data, and how the pieces fit together.

## PSCI removal

This application previously also implemented PSCI (the Irish Rural Flexible Roads
Manual, DTTAS Nov 2013, Table 1 scheme) alongside the D6433 work below. **PSCI has
been removed as a project decision: this application targets ASTM D6433 PCI, not
PSCI.** Removed: `psci_scoring.py`, `psci_adapter.py`, `psci_viewer.py`,
`test_psci_scoring.py`, `test_psci_adapter.py`. `psci_viewer.py` was replaced by
`pci_viewer.py` (initially named `pavement_inspection_viewer.py`, renamed to match
the project's target methodology), which keeps the generic distress-observation
entry grid (built from the same methodology-agnostic `distress_catalog.py`) but has
no rating calculation of any kind -- PSCI's was removed outright, and D6433's
remains blocked on reference data (see below). **Being named "PCI Viewer" reflects
the target, not a claim that it currently calculates one** -- no PCI or other rating
is computed or displayed anywhere in this application; see "Not yet built" below.
No PSCI logic was ported into the D6433 path; nothing about the current block on
D6433 was worked around by reusing PSCI code. `survey_core.py` kept its
methodology-agnostic shared infrastructure (S3/RSP parsing, `RATING_COLORS`) and
lost its PSCI-only field-name constants. See the dependency audit and file
disposition further down for the full accounting.

## Architecture

```
S3 image/frame
      |
      v
Inspector UI (pci_viewer.py -- records distress selections; not yet wired to
               InspectionObservation, see "Not yet built")
      |
      v
distress_catalog.py  (methodology-agnostic vocabulary: what a distress IS)
      |
      v
inspection_observation.py  (methodology-agnostic observation: what was
      |                      physically OBSERVED)
      v
d6433_adapter.py
      |
      v
d6433_types.py  (NormalizedD6433Observation, D6433CalculationInput, ...)
      |
      v
d6433_engine.py
      |
      v
DeductCurveProvider  (injected -- see below; BLOCKED on authoritative D6433 data)
      |
      v
D6433SampleUnitResult  (PCI + full calculation trace)
```

## Raw observation vs. calculated input

`InspectionObservation` (`inspection_observation.py`) is **what an inspector
physically observed**: a distress, a severity, a quantity, and the S3 frame(s) that
serve as visual evidence. It carries no D6433-calculated values -- no density, no
deduct value, nothing methodology-specific. It's traceable back to its
visual evidence via `evidence` (a tuple of one or more S3 frame references), designed
so one physical defect is one observation, not one per frame it happens to appear in
-- see the module docstring for why, and "Not yet built" below for what this implies
for the UI.

`d6433_adapter.py` is the boundary where methodology-specific interpretation starts.
`normalize_observation()` turns a validated `InspectionObservation` into either a
`NormalizedD6433Observation` (density-ready) or an `UnsupportedObservation` (excluded,
with an explicit reason -- never silently dropped). Only four distresses are in scope
today (`D6433_IN_SCOPE_DISTRESS_IDS`: potholes, alligator_cracking, raveling,
bleeding), and even within that set, only observations whose catalog
`quantity_kind == "area_pct"` (raveling, bleeding) actually normalize -- potholes
(count-based) and alligator cracking (mixed units in the source CSV) come back
`UnsupportedObservation` rather than being forced through a guessed formula.

## Engine and provider responsibilities

`d6433_engine.py` implements only the calculation mechanics that could be
independently verified: routing one deduct-value lookup per (distress, severity,
density), summing to TDV, and PCI = 100 - max(CDV). It contains **no ASTM curve data
and no correction-procedure logic** -- both are delegated to an injected
`DeductCurveProvider`:

```python
class DeductCurveProvider(Protocol):
    is_verified_reference_data: bool
    def deduct_value(self, distress_id, severity, density_pct) -> float: ...
    def compute_corrected_deduct_value(self, deduct_values: List[float]) -> CDVComputation: ...
```

Note that `compute_corrected_deduct_value` owns the **entire** TDV/q/iteration
procedure, not just a chart lookup. This is a deliberate, documented correction to
the Phase 3 audit: that audit labeled the iterative correction mechanism ("reduce the
lowest deduct value over 2 to 5, repeat until q=1") as verified, based on a
secondary-source paraphrase. Working through it by hand while implementing this
module showed it doesn't obviously converge as described (see `d6433_engine.py`'s
module docstring for the worked counterexample). Rather than guess a fix, the whole
procedure -- not just the missing curve numbers -- is isolated behind the provider.

One exception, added in the Phase 4 architecture verification: `compute_corrected_deduct_value` returns every iteration it ran, but **not** a self-reported maximum. `d6433_engine.calculate_sample_unit_pci()` derives `max_corrected_deduct_value` itself via `max()` over the returned iterations. "PCI is 100 minus the largest CDV produced" was corroborated independently of the disputed reduction mechanics (Phase 3/4 audits), so this one step is safe to own as engine logic rather than trust a provider to self-report -- see `CDVComputation`'s docstring in `d6433_types.py`.

Two providers exist today, both in this repository:

- **`UnavailableDeductCurveProvider`** (`d6433_engine.py`) -- the production
  placeholder. Every method raises `DeductCurveDataUnavailable` immediately. This is
  what any real calculation gets until a licensed provider is installed; there is no
  silent fallback and no invented default.
- **`FakeDeductCurveProvider`** (`fake_deduct_curve_provider.py`) -- test-only,
  loudly documented as such in its module docstring. Requires exact pre-registered
  `(distress, severity, density)` / `(deduct_values tuple)` matches; anything
  unconfigured raises `KeyError` rather than falling back to a guessed number. Not
  imported anywhere in production code.

Every `D6433SampleUnitResult` carries `is_verified_reference_data` (copied from
whichever provider computed it) so a result can never be mistaken for a real ASTM PCI
if it came from the fake provider -- this is a structural safeguard, not a
documentation-only convention.

## Auditability

`D6433SampleUnitResult` is not a black box: it exposes `deduct_value_results` (one
per scored observation), `total_deduct_value`, `cdv_iterations` (the provider's full
trace), `max_corrected_deduct_value`, `pci`, and `unsupported_observations` (so
nothing excluded from the score is invisible). `all_observations_scored` is `False`
whenever any observation was excluded, so a caller can distinguish "this PCI reflects
everything observed" from "this PCI is partial."

## Phase 4: architecture verification

Performed before loading any real reference data, to check the provider boundary is
right *before* real curve data makes it expensive to move. No ASTM values were added
in this phase; the one code change was the max-CDV fix described above.

### Provider boundary verdict

- **Should the provider supply only deduct-value reference data?** In the ideal
  end-state, yes -- but not yet. The provider currently also owns the entire CDV
  correction *procedure* (not just its numeric chart), because the Phase 3/4 audits
  could not verify even the procedure's algorithmic shape (see the worked
  counterexample above) -- only its two endpoints (`q`'s definition, and "take the
  max CDV") are corroborated independently of that disputed mechanic, and both of
  those are now engine-owned. Narrowing the provider further would require guessing
  the real iteration rule, which is exactly what's prohibited. **This is the
  correct scope for the provider today, not a gap to close.**
- **Which parts should remain deterministic engine logic?** Routing (one DV lookup
  per distress/severity/density), TDV (sum), max-CDV selection, and PCI arithmetic --
  all implemented, all provider-independent, all tested with fake data.
- **Should the provider perform any part of the CDV iteration algorithm?** Yes, all
  of it, for now -- see above. `compute_q()` exists as a corroborated, tested, pure
  utility but is **not called** from `calculate_sample_unit_pci()`, because we don't
  know how q is actually used inside the real procedure (Phase 3 assumed one thing;
  Phase 4 showed that assumption doesn't hold up). It's kept available for whoever
  implements a real provider later, not wired into the pipeline today.
- **Can the interface represent all required D6433 inputs/outputs?** For the
  currently-normalizable distresses (raveling, bleeding -- both `area_pct`), yes.
  **Known limitation, left unresolved rather than fixed:** `deduct_value()`'s
  signature hardcodes `density_pct: float` as the sole numeric input. Real ASTM
  count-based curves (e.g. Potholes) may take a raw count, a count-per-area, or an
  equivalent-area conversion as their actual X-axis -- unverified. Because the
  adapter already refuses to normalize count-kind or mixed-unit distresses (see
  above), this limitation is never exercised today. Widening the interface now would
  mean guessing which of those shapes is correct; it's flagged here for whoever
  brings a count-based distress into scope, not fixed speculatively.
- **Can it support different distress types/severities/units without special-case
  logic scattered through the engine?** Yes for what's implemented -- `deduct_value`
  is called identically regardless of which distress/severity it's for; there is no
  per-distress branching anywhere in `d6433_engine.py` or `d6433_adapter.py`'s
  normalization path (the *scope* filter in `d6433_adapter.py` is a single
  frozenset membership check, not per-distress logic).

**No architectural correction is being made to the provider interface itself in this
phase**, beyond the max-CDV fix above -- every other candidate change would require
assuming an unverified D6433 requirement.

### Algorithm / reference-data / project-data / unresolved

| Calculation component | Category | Current status | Required source | Unit-testable now? |
|---|---|---|---|---|
| Distress normalization (label/severity -> `distress_id`/`severity`) | Algorithm | Implemented, tested | Project code (`distress_catalog.py`, `inspection_observation.validate_observation`) | Yes |
| Quantity/unit validation | Algorithm (validation logic) + unresolved (which unit is *actually* correct per ASTM distress) | Implemented, tested | Validation: project code. Correct unit per distress: ASTM D6433 | Yes (validation logic only) |
| Density | Algorithm (the formula) + unresolved (whether density_pct is the right X-axis for a given distress's curve) | Implemented, tested, applied only to `area_pct`-kind distresses | Formula: corroborated (Phase 3). Applicability per distress: ASTM D6433 | Yes (arithmetic only) |
| Deduct-value lookup | Reference data | Not implemented -- `UnavailableDeductCurveProvider` raises | Licensed ASTM D6433 Appendix X4 | Only via fake provider (routing, not values) |
| TDV | Algorithm | Implemented, tested | Corroborated (Phase 3) -- plain sum | Yes |
| q (count of DV > 2) | Algorithm (definition) | Implemented, tested, **not wired into the pipeline** (see above) | Corroborated definition (Phase 3); role in the real procedure unresolved | Yes, as an isolated utility |
| CDV correction procedure (iteration mechanics) | Unresolved | Not implemented at all -- fully delegated to provider | Licensed ASTM D6433 | Only via fake provider (pre-scripted `CDVComputation`) |
| Maximum CDV selection | Algorithm | Implemented, tested, engine-derived (Phase 4 fix) | Corroborated (Phase 3/4) | Yes |
| PCI (100 - max CDV) | Algorithm | Implemented, tested | Corroborated (Phase 3) | Yes |
| Sample-unit aggregation (multiple observations -> one PCI) | Algorithm | Implemented, tested | Project code | Yes |
| Section aggregation | Unresolved | Not implemented -- `D6433SectionResult.section_pci` stays `None` | Licensed ASTM D6433 | No -- nothing to test |
| Rounding | Unresolved | Not implemented anywhere (no rounding applied) | Licensed ASTM D6433 | A "no rounding occurs" regression guard could be added; no correctness test possible yet |

### Data model verification

Checked against the requested field list: one physical observation, multiple frame
references, sample-unit association, sample-unit area, distress type, severity,
quantity, quantity unit, density, deduct value, TDV, q, CDV iterations, max CDV,
final PCI, section-level result, audit trace. **Every one of these is represented**
across `InspectionObservation` and `d6433_types.py` -- see the file docstrings for
exactly which field. No missing fields were found, and none were added speculatively.
One deliberate non-field, confirmed correct rather than an oversight: there is no
top-level "q" on `D6433SampleUnitResult` -- only per-iteration `CDVIteration.q` --
because a single sample-unit-level q isn't well-defined until the real procedure
(which q would it be: initial, final, something else?) is known.

### Multi-frame evidence -- confirmed behavior

Nothing in `inspection_observation.py`, `d6433_adapter.py`, or `d6433_engine.py`
iterates over frames to create observations -- there is no code path that could turn
3 frames into 3 observations, because observation *creation* from frames isn't
implemented at all yet (only creation via direct construction, validation,
normalization, and calculation are). `InspectionObservation.evidence` already holds
an arbitrary number of frame references for one observation, and
`test_d6433_engine.TestMultiFrameEvidenceDoesNotDuplicateObservation` proves a single
observation with 3 frames of evidence contributes exactly one deduct value, not
three. **Future UI rule** (unchanged from the Phase 3 audit, restated here since nothing
about it changed): the UI must create one `InspectionObservation` per physically
distinct defect and attach every frame that shows it to that one observation's
`evidence`, not create a new observation each time a chainage boundary or frame
change occurs.

### Density handling audit

| Distress | quantity_kind (catalog) | Density (quantity/area*100) applicable? | Status |
|---|---|---|---|
| Raveling | `area_pct` | Yes -- structurally applied | Implemented; whether `area_pct` is the *correct* real-ASTM treatment is still unverified (inherited from Phase 3, not newly confirmed) |
| Bleeding | `area_pct` | Yes -- structurally applied | Same caveat as Raveling |
| Potholes | `count` | No -- not applied | Correctly excluded (`UnsupportedObservation`); real ASTM treatment (raw count vs. count-per-area vs. equivalent-area) unresolved |
| Alligator Cracking | `None` (mixed sq.m/% within the source bucket list itself, per Phase 1) | Unknown | Correctly excluded; ambiguous even at the UI/catalog level, not just against ASTM |
| Patching, Rutting, Depression, Disintegration, Edge Breakup, Other Cracking | n/a | n/a | Out of `D6433_IN_SCOPE_DISTRESS_IDS` entirely -- excluded before quantity_kind is ever checked |

The catalog's `quantity_kind`/`quantity_unit` values remain, as documented since
Phase 1, a transcription of the current UI's own bucket-label text -- not an ASTM
authority. Nothing in this phase elevated that status.

## Blocked reference data

Nothing below is guessed or hard-coded anywhere in this codebase:

1. ASTM D6433 Appendix X4 deduct-value curves (needed for `deduct_value()`), at
   minimum for potholes, alligator cracking, raveling, bleeding.
2. The CDV correction procedure itself, including its exact iteration mechanics
   (needed for `compute_corrected_deduct_value()`) -- see the note above; this is
   *more* unresolved than the Phase 3 audit originally concluded.
3. Verified severity criteria (diameter/depth for potholes, crack width for
   alligator cracking) for the in-scope distresses.
4. Sample-unit area data for the actual roads being surveyed (not captured
   anywhere in this application yet).
5. The section-level PCI aggregation rule (`D6433SectionResult.section_pci` stays
   `None` until this is supplied).

## Not yet built (intentionally, per project instruction)

- No D6433 measurement UI yet. `pci_viewer.py` still records raw distress/severity
  bucket selections per frame (the same grid the old PSCI viewer used, now with no
  calculation behind it at all); it does not construct `InspectionObservation`s.
- No wiring from the per-frame recording flow into `InspectionObservation`. Today's
  UI still creates one observation record per frame;
  `InspectionObservation.evidence` is designed to hold multiple frame references per
  physical observation instead, to avoid double-counting the same defect across
  frames once the UI is updated to match -- that UI/workflow change
  (review-a-sample-unit, then record one observation) is future work, not done here.
- No `SampleUnit`/`Section` persistence -- `sample_unit_area_sq_m` is currently a
  parameter callers must supply, not sourced from anywhere in the app.
- No section-level aggregation implementation (the rule is unresolved, see above).
- No PCI (or any other rating) is calculated or displayed anywhere in the
  application right now -- not PSCI (removed) and not D6433 (blocked).
