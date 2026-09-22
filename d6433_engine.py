"""
ASTM D6433 calculation engine mechanics.

What this module implements as independently-verified pure logic:
  - routing: one deduct-value lookup per (distress, severity, density), via an
    injected DeductCurveProvider
  - Total Deduct Value = sum of the individual deduct values (plain arithmetic)
  - q = count of individual deduct values greater than 2 (the DEFINITION of q is
    corroborated by multiple independent secondary sources -- see the Phase 3 audit;
    NOTE: this helper is not currently called from calculate_sample_unit_pci --
    see the Phase 4 audit for why q's role inside the still-unresolved correction
    procedure isn't safe to assume)
  - selecting the maximum CDV across whatever iterations a provider produces, and
    PCI = 100 - that maximum -- computed here, independently of the provider,
    because "take the largest CDV" is corroborated separately from the disputed
    iteration mechanics (see CDVComputation's docstring in d6433_types.py)

What this module deliberately does NOT implement: the iterative correction procedure
that turns (deduct values) into a Corrected Deduct Value. The Phase 3 audit initially
labeled this "VERIFIED" based on a secondary-source paraphrase ("the lowest deduct
value greater than two is changed to five, repeat until q=1"). Working through that
rule by hand while building this module (e.g. deduct values [10, 8, 3]: q=3;
replacing the lowest qualifying value, 3, with 5 gives [10, 8, 5] -- still all >2, so
q is STILL 3, not decreasing) shows it does not obviously converge as described. That
means the paraphrase was imprecise, misremembered, or missing a detail I don't have
confirmed -- exactly the situation where the project's standing instruction applies:
isolate the unverified portion behind an interface rather than guess a fix (e.g.
guessing the real rule reduces to 2, not 5, would itself be an invented ASTM value).
So the entire correction procedure -- including its iteration mechanics -- is the
DeductCurveProvider's responsibility (compute_corrected_deduct_value), not this
module's. This is a correction to the Phase 3 audit's labeling, flagged explicitly
here and in the Phase 3B completion report rather than silently changed.

This module contains NO ASTM D6433 deduct-value curve data and NO CDV
correction-chart/procedure data. UnavailableDeductCurveProvider is what runs in
production until a real, licensed provider is installed. See
fake_deduct_curve_provider.py (a separate, clearly-named module) for the test-only
stand-in -- that module must never be imported here or from any production entry
point.
"""
from typing import List, Protocol

from d6433_types import (
    D6433CalculationInput, DeductValueResult, D6433SampleUnitResult, CDVComputation,
)


class DeductCurveDataUnavailable(Exception):
    """Raised when a calculation needs deduct-curve/correction data that hasn't
    been installed yet."""


class DeductCurveProvider(Protocol):
    is_verified_reference_data: bool

    def deduct_value(self, distress_id: str, severity: str, density_pct: float) -> float: ...

    def compute_corrected_deduct_value(self, deduct_values: List[float]) -> CDVComputation: ...


class UnavailableDeductCurveProvider:
    """Production placeholder. Every call fails clearly and immediately -- per the
    project's production-safety requirement: no fake curve values ship as a
    production default, and production mode must fail clearly, not silently, when
    D6433 reference data isn't installed yet."""
    is_verified_reference_data = False

    def deduct_value(self, distress_id: str, severity: str, density_pct: float) -> float:
        raise DeductCurveDataUnavailable(
            f"No ASTM D6433 deduct-value curve installed for "
            f"distress={distress_id!r} severity={severity!r}. Install a verified "
            f"DeductCurveProvider before calculating a PCI."
        )

    def compute_corrected_deduct_value(self, deduct_values: List[float]):
        raise DeductCurveDataUnavailable(
            "No ASTM D6433 CDV correction procedure installed. Install a verified "
            "DeductCurveProvider before calculating a PCI."
        )


def compute_deduct_values(
    calculation_input: D6433CalculationInput, provider: DeductCurveProvider
) -> List[DeductValueResult]:
    return [
        DeductValueResult(
            observation_id=obs.observation_id, distress_id=obs.distress_id,
            severity=obs.severity, density_pct=obs.density_pct,
            deduct_value=provider.deduct_value(obs.distress_id, obs.severity, obs.density_pct),
        )
        for obs in calculation_input.normalized_observations
    ]


def compute_tdv(deduct_values: List[float]) -> float:
    return sum(deduct_values)


def compute_q(deduct_values: List[float]) -> int:
    """q = count of individual deduct values greater than 2. This definition is
    corroborated (Phase 3 audit); the correction procedure that USES q is not
    implemented here -- see module docstring."""
    return sum(1 for v in deduct_values if v > 2)


def calculate_sample_unit_pci(
    calculation_input: D6433CalculationInput, provider: DeductCurveProvider
) -> D6433SampleUnitResult:
    dv_results = compute_deduct_values(calculation_input, provider)
    deduct_values = [r.deduct_value for r in dv_results]
    tdv = compute_tdv(deduct_values)

    if deduct_values:
        cdv_computation = provider.compute_corrected_deduct_value(deduct_values)
        iterations = cdv_computation.iterations
        if not iterations:
            raise ValueError(
                "DeductCurveProvider.compute_corrected_deduct_value() returned no "
                "iterations for a non-empty deduct value list"
            )
        # Independently derived, not trusted from the provider -- see
        # CDVComputation's docstring for why this specific step is engine-owned.
        max_cdv = max(it.corrected_deduct_value for it in iterations)
    else:
        # No normalized observations at all -- CDV can never exceed TDV, and TDV is
        # trivially 0 here, so CDV=0 is a mathematical certainty, not a curve lookup.
        # No provider call is made; PCI=100 needs no reference data to be correct.
        iterations, max_cdv = [], 0.0

    pci = 100.0 - max_cdv

    return D6433SampleUnitResult(
        section_id=calculation_input.section_id,
        sample_unit_id=calculation_input.sample_unit_id,
        deduct_value_results=dv_results,
        total_deduct_value=tdv,
        cdv_iterations=iterations,
        max_corrected_deduct_value=max_cdv,
        pci=pci,
        unsupported_observations=calculation_input.unsupported_observations,
        is_verified_reference_data=provider.is_verified_reference_data,
        all_observations_scored=not calculation_input.unsupported_observations,
    )
