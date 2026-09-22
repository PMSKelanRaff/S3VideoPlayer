"""
D6433 adapter: translates InspectionObservation (methodology-agnostic, see
inspection_observation.py) into D6433CalculationInput (see d6433_types.py).

Per the project's Phase 3 scope decision, only a subset of the distress catalog has
a defensible path into this calculation today -- see D6433_IN_SCOPE_DISTRESS_IDS.
Within that subset, only observations whose catalog quantity_kind is "area_pct" can
actually be normalized into a density with the one formula the Phase 3 audit could
verify (density = quantity / sample_unit_area * 100, for area/length-based
quantities). Count-based distresses (Potholes) and distresses whose own bucket
labels mix units within one option list (Alligator Cracking, per distress_catalog.py)
are NOT normalized here -- their correct D6433 quantity treatment is still
unresolved, and this adapter refuses to guess it.

Every observation this adapter can't normalize comes back as an
UnsupportedObservation with an explicit reason -- never silently dropped.

This module contains NO deduct-value formulas and NO ASTM curve data -- see
d6433_engine.py for where those plug in.
"""
from typing import List, Union

from distress_catalog import get_distress
from inspection_observation import InspectionObservation, validate_observation
from d6433_types import (
    D6433CalculationInput, NormalizedD6433Observation, UnsupportedObservation,
)

# Distresses with a defensible D6433 path per the Phase 3 audit -- deliberately not
# the full 19-type ASTM catalog, and not simply every distress the UI happens to
# offer. See the audit for why the remaining catalog entries are excluded.
D6433_IN_SCOPE_DISTRESS_IDS = frozenset({"potholes", "alligator_cracking", "raveling", "bleeding"})


def compute_density(quantity: float, sample_unit_area_sq_m: float) -> float:
    """density = quantity / sample_unit_area * 100 -- the one density formula the
    Phase 3 audit could verify from independent secondary sources, for area/length
    -based quantities. Deliberately not applied to count-based distresses; see
    normalize_observation()."""
    if sample_unit_area_sq_m <= 0:
        raise ValueError("sample_unit_area_sq_m must be positive")
    if quantity < 0:
        raise ValueError("quantity must be non-negative")
    return quantity / sample_unit_area_sq_m * 100


def normalize_observation(
    obs: InspectionObservation, sample_unit_area_sq_m: float
) -> Union[NormalizedD6433Observation, UnsupportedObservation]:
    """Returns a NormalizedD6433Observation if this observation can be confidently
    scored today, else an UnsupportedObservation with a clear reason. Raises only for
    a structurally malformed observation (validate_observation's errors) -- being
    out of D6433 scope is an expected, non-exceptional outcome, not a bug."""
    validate_observation(obs)

    if obs.distress_id not in D6433_IN_SCOPE_DISTRESS_IDS:
        return UnsupportedObservation(
            observation_id=obs.observation_id, distress_id=obs.distress_id,
            reason=f"'{obs.distress_id}' is not in the current D6433 scope "
                   f"(see D6433_IN_SCOPE_DISTRESS_IDS / the Phase 3 audit).",
        )

    distress = get_distress(obs.distress_id)
    if distress.quantity_kind != "area_pct":
        return UnsupportedObservation(
            observation_id=obs.observation_id, distress_id=obs.distress_id,
            reason=f"'{obs.distress_id}' has quantity_kind={distress.quantity_kind!r}, "
                   f"not 'area_pct' -- its D6433 density treatment is unresolved, see the audit.",
        )

    density_pct = compute_density(obs.quantity, sample_unit_area_sq_m)
    return NormalizedD6433Observation(
        observation_id=obs.observation_id, distress_id=obs.distress_id,
        severity=obs.severity, density_pct=density_pct, evidence=obs.evidence,
    )


def build_calculation_input(
    section_id: str, sample_unit_id: str, sample_unit_area_sq_m: float,
    observations: List[InspectionObservation],
) -> D6433CalculationInput:
    normalized: List[NormalizedD6433Observation] = []
    unsupported: List[UnsupportedObservation] = []

    for obs in observations:
        if obs.sample_unit_id != sample_unit_id:
            raise ValueError(
                f"Observation {obs.observation_id!r} belongs to sample unit "
                f"{obs.sample_unit_id!r}, not {sample_unit_id!r}"
            )
        result = normalize_observation(obs, sample_unit_area_sq_m)
        if isinstance(result, NormalizedD6433Observation):
            normalized.append(result)
        else:
            unsupported.append(result)

    return D6433CalculationInput(
        section_id=section_id, sample_unit_id=sample_unit_id,
        sample_unit_area_sq_m=sample_unit_area_sq_m,
        normalized_observations=normalized, unsupported_observations=unsupported,
    )
