"""
D6433 calculation type system -- see the project's Phase 3 architecture audit.

These types represent D6433-specific calculated/intermediate values. They are
deliberately separate from inspection_observation.InspectionObservation, which stays
methodology-agnostic and carries no calculated values. This project has no PSCI
methodology anymore (removed as a project decision -- see D6433_ARCHITECTURE.md);
none of these types ever depended on it.

No ASTM D6433 numeric reference data (deduct curves, correction values, thresholds)
is defined in this module -- it only shapes what such data would flow through, once
a verified DeductCurveProvider (see d6433_engine.py) supplies it.
"""
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class NormalizedD6433Observation:
    """One InspectionObservation, translated into a D6433-ready density input.
    Produced only for observations d6433_adapter.py could confidently normalize --
    see that module's docstring for exactly which ones qualify today."""
    observation_id: str
    distress_id: str
    severity: str
    density_pct: float
    evidence: Tuple[str, ...]


@dataclass(frozen=True)
class UnsupportedObservation:
    """An InspectionObservation the adapter could NOT normalize for D6433 -- excluded
    from the calculation, but never silently dropped; carried through on
    D6433CalculationInput / D6433SampleUnitResult so nothing disappears unexplained."""
    observation_id: str
    distress_id: str
    reason: str


@dataclass(frozen=True)
class D6433CalculationInput:
    section_id: str
    sample_unit_id: str
    sample_unit_area_sq_m: float
    normalized_observations: List[NormalizedD6433Observation]
    unsupported_observations: List[UnsupportedObservation]


@dataclass(frozen=True)
class DeductValueResult:
    observation_id: str
    distress_id: str
    severity: str
    density_pct: float
    deduct_value: float


@dataclass(frozen=True)
class CDVIteration:
    """One pass of the ASTM correction procedure, kept (not discarded) for
    auditability. Populated by whichever DeductCurveProvider computed it -- the
    engine itself does not construct these; see d6433_engine.py's module docstring
    for why the correction procedure's mechanics are not implemented independently
    of a provider in this phase."""
    deduct_values_used: Tuple[float, ...]
    total_deduct_value: float
    q: int
    corrected_deduct_value: float


@dataclass(frozen=True)
class CDVComputation:
    """A DeductCurveProvider's full answer for one sample unit's correction
    procedure: every iteration it ran (in whatever order/manner the procedure
    produces them -- unresolved, see d6433_engine.py). Deliberately does NOT carry
    a self-reported maximum: "the PCI is 100 minus the largest CDV produced" is one
    of the few parts of the correction procedure the Phase 3/4 audits could
    corroborate independently of the disputed iteration mechanics, so the engine
    derives the maximum itself from `iterations` rather than trusting a provider to
    report it -- see d6433_engine.calculate_sample_unit_pci()."""
    iterations: List[CDVIteration]


@dataclass(frozen=True)
class D6433SampleUnitResult:
    """The full calculation trace for one sample unit -- nothing here is hidden;
    every intermediate value used to reach `pci` is present, per the project's
    auditability requirement."""
    section_id: str
    sample_unit_id: str
    deduct_value_results: List[DeductValueResult]
    total_deduct_value: float
    cdv_iterations: List[CDVIteration]
    max_corrected_deduct_value: float
    pci: float
    unsupported_observations: List[UnsupportedObservation]
    is_verified_reference_data: bool   # False for any result computed with a non-ASTM provider (e.g. the fake)
    all_observations_scored: bool      # False whenever unsupported_observations is non-empty


@dataclass(frozen=True)
class D6433SectionResult:
    """Section-level aggregation is NOT implemented yet -- the aggregation rule
    itself is an unresolved reference dependency (see the Phase 3 audit). This type
    exists so the interface shape is settled; section_pci stays None until that rule
    is supplied."""
    section_id: str
    sample_unit_results: List[D6433SampleUnitResult]
    section_pci: Optional[float] = None
