"""
Methodology-agnostic distress catalog for the PCI Viewer's detailed distress entry
grid (see the screenshot this was originally built from), sourced from the site's
own distress recording scheme (PCIVariables.txt).

Bucket label text for all 17 cells was corrected (2026-10-06) to match, byte-for-byte,
the Descriptor column of the [Distresses] table in the legacy C# application's
VPCI_Config.accdb (the authoritative lookup data for legacy_pci_engine.py) -- the
original hand-transcribed labels differed from the database in whitespace only,
except for Disintegration, whose original labels ("<1%, c.3%, c.5%, c.10%, c.20%,
>30%") described different thresholds than the database's actual buckets ("1%, 2%,
5%, 10%, 15%, >20%") and have been corrected to match. Bucket order (and therefore
each bucket's position) is unchanged and was independently verified against the
database's own combobox-population order (ORDER BY [Quantity], "0" first) before
this correction. See legacy_pci_data.py for the verbatim database extraction this
was checked against.

This module describes the distress *vocabulary* only -- distress types, their severity
levels, and the dropdown bucket labels an inspector picks from. It does not know about,
and must not know about, any particular scoring methodology. How a given distress
feeds a scoring engine is that engine's adapter's concern -- see legacy_pci_adapter.py
for the only scoring methodology this project implements (the legacy C# VPCI engine).
ASTM D6433 and PSCI were both explored as alternative methodologies for this catalog
and have since been removed as a project decision; this catalog never depended on
either.

`quantity_kind`/`quantity_unit` are a literal transcription of what each distress's own
bucket labels say (e.g. a label list that's all "...%" is quantity_kind="area_pct"; a
list of bare "0, 1, 2, 3..." is quantity_kind="count"). Distresses whose own
bucket-label list mixes units internally (Patching, Alligator Cracking, Rutting all mix
bare/area-based low buckets with %-based high buckets in the source data) are left as
None rather than guessing a single unit for them.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional

# Key used in a DistressDefinition.options dict for distresses with no severity split.
NO_SEVERITY = ""


@dataclass(frozen=True)
class DistressDefinition:
    distress_id: str                    # internal id, e.g. "potholes"
    label: str                          # UI label, e.g. "Potholes"
    severities: List[str]               # e.g. ["Low", "Medium", "High"]; [] if not split by severity
    options: Dict[str, List[str]]       # severity -> ordered dropdown bucket labels (NO_SEVERITY key if severities == [])
    quantity_kind: Optional[str] = None  # "area_pct" | "count" | None (mixed/undetermined) -- see module docstring
    quantity_unit: Optional[str] = None  # "percent" | "cracks" | "count" | None

    def storage_key(self, severity: str = NO_SEVERITY) -> str:
        """Stable dict key this distress/severity's selection is stored/exported
        under. Deliberately snake_case and distinct from any CamelCase field naming
        a scoring adapter might use, so the two can never collide."""
        if severity and severity != NO_SEVERITY:
            return f"{self.distress_id}_{severity.lower()}"
        return self.distress_id


_SAME_FOR_ALL = lambda severities, buckets: {sev: list(buckets) for sev in severities}  # noqa: E731

DISTRESS_DEFINITIONS: List[DistressDefinition] = [
    DistressDefinition(
        distress_id="potholes", label="Potholes", severities=["Low", "Medium", "High"],
        options={
            "Low": ["0", "1", "c. 3", "c. 10", "c. 20", "c.40", "> 50"],
            "Medium": ["0", "1", "c. 2", "c. 3", "c. 6", "c.10", "> 15"],
            "High": ["0", "1", "2", "3", "4", "5", "> 5"],
        },
        quantity_kind="count", quantity_unit="count",
    ),
    DistressDefinition(
        distress_id="patching", label="Patching", severities=["Low", "Medium", "High"],
        options=_SAME_FOR_ALL(["Low", "Medium", "High"],
                               ["0", "1", "c. 2%", "c. 5%", "c. 10%", "c.20%", "> 30%"]),
        # Mixed unit within the same bucket list ("1" alongside "%" buckets) -- left
        # undetermined rather than guessed.
    ),
    DistressDefinition(
        distress_id="alligator_cracking", label="Alligator Cracking", severities=["Low", "Medium", "High"],
        options=_SAME_FOR_ALL(["Low", "Medium", "High"],
                               ["0", "< 1 sq.m", "c. 2 sq.m", "c. 1%", "c. 3%", "c.10%", "> 20%"]),
        # Mixed unit within the same bucket list (sq.m for small buckets, % for larger
        # ones) -- left undetermined rather than guessed.
    ),
    DistressDefinition(
        # No "Low" severity for Rutting -- matches both PCIVariables.txt (only "rutting
        # med"/"rutting high" are defined) and the screenshot (blank Low cell).
        distress_id="rutting", label="Rutting", severities=["Medium", "High"],
        options=_SAME_FOR_ALL(["Medium", "High"],
                               ["0", "<1 sq.m", "c. 2 sq.m", "c. 5 sq.m", "c. 5%", "c.10%", "> 15%"]),
        # Mixed unit within the same bucket list, same as Alligator Cracking above.
    ),
    DistressDefinition(
        distress_id="raveling", label="Raveling", severities=[],
        options={NO_SEVERITY: ["0", "< 1%", "c. 2%", "c. 5%", "c. 10%", "c. 25%", "> 40%"]},
        quantity_kind="area_pct", quantity_unit="percent",
    ),
    DistressDefinition(
        distress_id="depression", label="Depression", severities=[],
        options={NO_SEVERITY: ["0", "< 1%", "c. 3%", "c. 5%", "c. 10%", "c. 20 %", "> 30%"]},
        quantity_kind="area_pct", quantity_unit="percent",
    ),
    DistressDefinition(
        # Original hand-transcribed labels ("<1%, c.3%, c.5%, c.10%, c.20%, >30%")
        # described different thresholds than the database's real buckets -- corrected
        # to match VPCI_Config.accdb's [Distresses] rows for Disintegration exactly
        # (see module docstring).
        distress_id="disintegration", label="Disintegration", severities=[],
        options={NO_SEVERITY: ["0", "1%", "2%", "5%", "10%", "15%", "> 20%"]},
        quantity_kind="area_pct", quantity_unit="percent",
    ),
    DistressDefinition(
        distress_id="bleeding", label="Bleeding", severities=[],
        options={NO_SEVERITY: ["0", "< 2%", "c. 5%", "c. 10%", "c. 25%", "c. 50 %", "> 60%"]},
        quantity_kind="area_pct", quantity_unit="percent",
    ),
    DistressDefinition(
        distress_id="edge_breakup", label="Edge Breakup", severities=[],
        options={NO_SEVERITY: ["0", "c. 3%", "c. 10%", "c. 20%", "c. 50%", "c. 100%", "> 150%"]},
        # Labels are all "%", but whether the >150% ceiling is really a simple area
        # fraction is an open question -- recorded as-labeled, not resolved here.
        quantity_kind="area_pct", quantity_unit="percent",
    ),
    DistressDefinition(
        distress_id="other_cracking", label="Other Cracking", severities=[],
        options={NO_SEVERITY: ["0", "< 2 cracks", "c. 3 cracks", "c. 8 cracks", "c. 15 cracks",
                                "c. 30 cracks", "> 50 cracks"]},
        quantity_kind="count", quantity_unit="cracks",
    ),
]

_BY_ID = {d.distress_id: d for d in DISTRESS_DEFINITIONS}


def get_distress(distress_id: str) -> DistressDefinition:
    return _BY_ID[distress_id]


def detailed_field_names() -> List[str]:
    """Every stored/exported key for the detailed distress grid, in a stable order:
    one per (distress, severity) cell, e.g. potholes_low, potholes_medium, ...,
    raveling, depression, ... Matches the order fields are built in the UI grid."""
    names = []
    for d in DISTRESS_DEFINITIONS:
        severities = d.severities or [NO_SEVERITY]
        for sev in severities:
            names.append(d.storage_key(sev))
    return names
