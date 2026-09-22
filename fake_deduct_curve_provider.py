"""
TEST-ONLY fake DeductCurveProvider.

The values a test configures into this class are NOT ASTM D6433 data. This class
exists solely to test d6433_engine.py's mechanics (provider routing, TDV, PCI
arithmetic, and correct threading of whatever a provider returns into
D6433SampleUnitResult) independently of the still-unresolved real curve data and
the still-unresolved CDV correction procedure -- see d6433_engine.py's module
docstring for why that procedure isn't implemented independently of a provider yet.

Do NOT import this module from production code (pci_viewer.py, d6433_adapter.py, or
any real calculation entry point). Nothing in this repository wires it in as a
default -- production code that needs a provider and doesn't have a real one gets
d6433_engine.UnavailableDeductCurveProvider, which fails loudly instead.

Every lookup requires an exact pre-registered match; there is no fallback/default
value, so a test can never accidentally rely on an un-configured (and therefore
meaningless) fake number.
"""
from typing import Dict, List, Tuple

from d6433_types import CDVComputation


class FakeDeductCurveProvider:
    is_verified_reference_data = False  # always False -- this is never real ASTM data

    def __init__(
        self,
        deduct_values: Dict[Tuple[str, str, float], float] = None,
        cdv_computations: Dict[Tuple[float, ...], CDVComputation] = None,
    ):
        self._deduct_values = dict(deduct_values or {})
        self._cdv_computations = dict(cdv_computations or {})

    def deduct_value(self, distress_id: str, severity: str, density_pct: float) -> float:
        key = (distress_id, severity, density_pct)
        if key not in self._deduct_values:
            raise KeyError(f"FakeDeductCurveProvider has no fake deduct_value configured for {key!r}")
        return self._deduct_values[key]

    def compute_corrected_deduct_value(self, deduct_values: List[float]) -> CDVComputation:
        key = tuple(deduct_values)
        if key not in self._cdv_computations:
            raise KeyError(f"FakeDeductCurveProvider has no fake CDVComputation configured for {key!r}")
        return self._cdv_computations[key]
