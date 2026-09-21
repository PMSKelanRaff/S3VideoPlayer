"""
PSCI (Pavement Surface Condition Index) scoring.

Implements Table 1 ("The PSCI Rating System") from the Rural Flexible Roads Manual
(Department of Transport, Tourism and Sport, Issue 1 Rev. 1, Nov 2013 -- the authoritative,
publicly published source for the Irish PSCI scheme used to rate non-national roads;
https://www.roadguidelines.ie/wp-content/uploads/2025/05/Rural-flexiblel-roads-manual.pdf).

PSCI is NOT an ASTM-style summed deduct-value system. Table 1 defines, for each rating
10 (excellent) down to 1 (failed), a set of primary/secondary indicators (defect type +
extent/severity). A section is rated at the WORST band for which any indicator is met --
"worst governs" -- not by summing deductions. See the manual's Section 3 guidance.

Where Table 1 gives an explicit numeric threshold (a percentage extent, or the 75mm rutting
depth), that exact figure is used below and is not an invented value. Where Table 1 itself is
only descriptive (patching condition, surface distortion, pothole frequency, edge breakup
length), this module exposes that as a categorical input for the rater to select -- the same
judgement call the manual asks a human surveyor to make by comparing the section to its
photographs and descriptions, rather than inventing numeric thresholds the source doesn't
give.

Every caller should go through compute_psci_rating() below.
"""

from dataclasses import dataclass, field
from typing import List

# The one place two independently-observed extents are combined: Table 1 groups Ravelling and
# Bleeding into a single "Surface Defects" category ("Ravelling or Bleeding <10%", etc.) but
# does not say how to combine them if both are present. We take the worse (larger) of the two
# rather than summing, since summing risks double-counting the same affected area. This is a
# documented implementation choice, not a value from the standard.
def _surface_defect_pct(ravelling_pct: float, bleeding_pct: float) -> float:
    return max(ravelling_pct, bleeding_pct)


@dataclass
class PSCIRatingInputs:
    """One rated PSCI section's inputs, per Table 1. Field names/options match
    survey_core.psci_field_names() and the *_FIELDS constants there."""
    ravelling_pct: float = 0.0
    bleeding_pct: float = 0.0
    other_cracking_pct: float = 0.0
    structural_distress_pct: float = 0.0  # rutting / alligator cracking / poor patching extent
    rutting_depth_mm: float = 0.0
    surface_distortion: str = "None"      # PSCI_SURFACE_DISTORTION_LEVELS
    patching_condition: str = "None"      # PSCI_PATCHING_CONDITIONS
    pothole_frequency: str = "None"       # PSCI_POTHOLE_FREQUENCIES
    edge_breakup_extent: str = "None"     # PSCI_EDGE_BREAKUP_EXTENTS
    disintegration_present: bool = False
    localised_structural_distress: bool = False

    @classmethod
    def from_dict(cls, values: dict) -> "PSCIRatingInputs":
        values = values or {}
        return cls(
            ravelling_pct=float(values.get("RavellingPct", 0) or 0),
            bleeding_pct=float(values.get("BleedingPct", 0) or 0),
            other_cracking_pct=float(values.get("OtherCrackingPct", 0) or 0),
            structural_distress_pct=float(values.get("StructuralDistressPct", 0) or 0),
            rutting_depth_mm=float(values.get("RuttingDepthMm", 0) or 0),
            surface_distortion=values.get("SurfaceDistortion", "None") or "None",
            patching_condition=values.get("PatchingCondition", "None") or "None",
            pothole_frequency=values.get("PotholeFrequency", "None") or "None",
            edge_breakup_extent=values.get("EdgeBreakupExtent", "None") or "None",
            disintegration_present=bool(values.get("DisintegrationPresent", False)),
            localised_structural_distress=bool(values.get("LocalisedStructuralDistress", False)),
        )

    def to_dict(self) -> dict:
        return {
            "RavellingPct": self.ravelling_pct,
            "BleedingPct": self.bleeding_pct,
            "OtherCrackingPct": self.other_cracking_pct,
            "StructuralDistressPct": self.structural_distress_pct,
            "RuttingDepthMm": self.rutting_depth_mm,
            "SurfaceDistortion": self.surface_distortion,
            "PatchingCondition": self.patching_condition,
            "PotholeFrequency": self.pothole_frequency,
            "EdgeBreakupExtent": self.edge_breakup_extent,
            "DisintegrationPresent": self.disintegration_present,
            "LocalisedStructuralDistress": self.localised_structural_distress,
        }


@dataclass
class PSCIBandEvaluation:
    """One rating band (1-9) as evaluated against a set of inputs, for the audit trail."""
    rating: int
    label: str
    reasons: List[str] = field(default_factory=list)  # empty if this band's indicators weren't met


@dataclass
class PSCIResult:
    """Audit-friendly PSCI outcome: the governing rating plus the reasons it was chosen,
    and every band's evaluation so a viewer can show the full worst-governs comparison,
    not just the final number."""
    rating: int
    reasons: List[str]
    all_bands: List[PSCIBandEvaluation]


_BAND_LABELS = {
    9: "Minor Surface Defects",
    8: "Moderate Surface Defects",
    7: "Extensive Surface Defects",
    6: "Moderate Other Pavement Defects",
    5: "Significant Other Pavement Defects",
    4: "Structural Distress Present",
    3: "Significant Areas of Structural Distress",
    2: "Large Areas of Structural Distress",
    1: "Extensive Structural Distress",
}


def _band_9(i: PSCIRatingInputs) -> List[str]:
    pct = _surface_defect_pct(i.ravelling_pct, i.bleeding_pct)
    if 0 < pct < 10:
        return [f"Surface defects (ravelling/bleeding) {pct:.0f}% (<10%)"]
    return []


def _band_8(i: PSCIRatingInputs) -> List[str]:
    pct = _surface_defect_pct(i.ravelling_pct, i.bleeding_pct)
    if 10 <= pct <= 30:
        return [f"Surface defects (ravelling/bleeding) {pct:.0f}% (10-30%)"]
    return []


def _band_7(i: PSCIRatingInputs) -> List[str]:
    pct = _surface_defect_pct(i.ravelling_pct, i.bleeding_pct)
    if pct > 30:
        return [f"Surface defects (ravelling/bleeding) {pct:.0f}% (>30%)"]
    return []


def _band_6(i: PSCIRatingInputs) -> List[str]:
    reasons = []
    if 0 < i.other_cracking_pct < 20:
        reasons.append(f"Other cracking {i.other_cracking_pct:.0f}% (<20%)")
    if i.patching_condition == "Good":
        reasons.append("Patching present, in good condition")
    if i.surface_distortion == "Some":
        reasons.append("Surface distortion requiring some reduction in speed")
    return reasons


def _band_5(i: PSCIRatingInputs) -> List[str]:
    reasons = []
    if i.other_cracking_pct > 20:
        reasons.append(f"Other cracking {i.other_cracking_pct:.0f}% (>20%)")
    if i.patching_condition == "Fair":
        reasons.append("Patching present, in fair condition")
    if i.surface_distortion == "Significant":
        reasons.append("Surface distortion requiring reduction in speed")
    if i.localised_structural_distress:
        reasons.append("Very localised structural distress (<5m² or a few isolated potholes)")
    return reasons


def _band_4(i: PSCIRatingInputs) -> List[str]:
    reasons = []
    if 5 <= i.structural_distress_pct <= 25:
        reasons.append(f"Structural distress (rutting/alligator cracking/poor patching) "
                        f"{i.structural_distress_pct:.0f}% (5-25%)")
    if i.edge_breakup_extent == "Short lengths":
        reasons.append("Edge breakup/cracking: short lengths")
    if i.pothole_frequency == "Frequent":
        reasons.append("Potholes: frequent")
    return reasons


def _band_3(i: PSCIRatingInputs) -> List[str]:
    reasons = []
    if 25 < i.structural_distress_pct <= 50:
        reasons.append(f"Structural distress (rutting/alligator cracking/poor patching) "
                        f"{i.structural_distress_pct:.0f}% (25-50%)")
    if i.edge_breakup_extent == "Continuous lengths":
        reasons.append("Edge breakup/cracking: continuous lengths")
    if i.pothole_frequency == "More Frequent":
        reasons.append("Potholes: more frequent")
    return reasons


def _band_2(i: PSCIRatingInputs) -> List[str]:
    reasons = []
    if i.structural_distress_pct > 50:
        reasons.append(f"Structural distress (rutting/alligator cracking/very poor patching) "
                        f"{i.structural_distress_pct:.0f}% (>50%)")
    if i.rutting_depth_mm > 75:
        reasons.append(f"Severe rutting: {i.rutting_depth_mm:.0f}mm (>75mm)")
    if i.patching_condition == "Very Poor":
        reasons.append("Patching condition: very poor / extensive")
    if i.pothole_frequency == "Many":
        reasons.append("Potholes: many")
    return reasons


def _band_1(i: PSCIRatingInputs) -> List[str]:
    reasons = []
    if i.disintegration_present:
        reasons.append("Road disintegration present")
    if i.patching_condition == "Failed":
        reasons.append("Patching condition: failed")
    return reasons


_BAND_CHECKS = {
    9: _band_9, 8: _band_8, 7: _band_7, 6: _band_6, 5: _band_5,
    4: _band_4, 3: _band_3, 2: _band_2, 1: _band_1,
}


def evaluate_psci_bands(inputs: PSCIRatingInputs) -> List[PSCIBandEvaluation]:
    """Runs every band's check against `inputs`, worst (1) to best (9), for the audit trail."""
    return [
        PSCIBandEvaluation(rating, _BAND_LABELS[rating], _BAND_CHECKS[rating](inputs))
        for rating in sorted(_BAND_LABELS, reverse=False)
    ]


def compute_psci_rating(inputs: PSCIRatingInputs) -> PSCIResult:
    """Rates one PSCI section per Table 1's worst-governs rule: the final rating is the
    worst (lowest-numbered) band for which at least one indicator was met, defaulting to
    10 ("No Visible Defects") when nothing was recorded."""
    bands = evaluate_psci_bands(inputs)
    triggered = [b for b in bands if b.reasons]
    if not triggered:
        return PSCIResult(rating=10, reasons=["No visible defects recorded."], all_bands=bands)
    worst = min(triggered, key=lambda b: b.rating)
    return PSCIResult(rating=worst.rating, reasons=worst.reasons, all_bands=bands)


def compute_psci_score(defect_values: dict) -> int:
    """Convenience wrapper for callers that only need the bare 1-10 rating (e.g. map marker
    colour) from a UI-collected values dict. Prefer compute_psci_rating() for anything that
    should show its reasoning."""
    return compute_psci_rating(PSCIRatingInputs.from_dict(defect_values)).rating
