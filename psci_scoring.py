"""
PLACEHOLDER PSCI (Pavement Surface Condition Index) scoring.

*** This is NOT the official TII PSCI scoring table/formula. ***

The real PSCI methodology (e.g. TII DN-PAV-03024) scores a section from defect
type + severity + extent via a defined deduction table, and we don't have that
table yet. This module exists purely so the PSCI Viewer's map and CSV export
have *a* 1-10 score to show while the rest of the survey UI is being built out.

Every caller goes through compute_psci_score() below — once the official
scoring rules are available, replace only the body of this function.
"""

from survey_core import SEVERITY_DEFECTS, SEVERITY_LEVELS, SINGLE_DEFECTS

# Arbitrary placeholder weights: higher severity / more occurrences deduct more
# from a perfect score of 10. Not derived from any official PSCI table.
_SEVERITY_WEIGHT = {"Low": 1, "Medium": 2, "High": 4}
_SINGLE_DEFECT_WEIGHT = 2


def compute_psci_score(defect_values: dict) -> int:
    """Returns a placeholder 1 (worst) - 10 (perfect) score for one rated section's
    defect counts. `defect_values` uses the same field names as
    survey_core.defect_field_names() (e.g. "Potholes_Low", "Raveling")."""
    if not defect_values:
        return 10

    deduction = 0
    for defect in SEVERITY_DEFECTS:
        for level in SEVERITY_LEVELS:
            field = f"{defect.replace(' ', '')}_{level}"
            deduction += defect_values.get(field, 0) * _SEVERITY_WEIGHT[level]
    for defect in SINGLE_DEFECTS:
        field = defect.replace(' ', '')
        deduction += defect_values.get(field, 0) * _SINGLE_DEFECT_WEIGHT

    score = round(10 - deduction)
    return max(1, min(10, score))
