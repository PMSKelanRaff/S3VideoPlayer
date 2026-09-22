"""
Methodology-agnostic pavement inspection observation model -- see the project's
Phase 2 architecture audit.

An InspectionObservation represents one physical, inspector-confirmed pavement
distress: what was actually observed, not a raw UI dropdown selection and not a
calculated value. It carries no D6433-calculated values -- density, deduct value,
TDV, CDV, PCI all live in d6433_types.py, produced by d6433_adapter.py /
d6433_engine.py from observations like this one, never stored on the observation
itself. (This project previously also had a PSCI scoring path; PSCI has been removed
as a project decision -- see D6433_ARCHITECTURE.md -- and this model never depended
on it.)

Per the Phase 3 audit's frame/evidence design: `evidence` is a tuple of one or more
S3 frame/image references, because multiple frames may be evidence for the SAME
physical distress -- one InspectionObservation should represent one physical defect,
not one per frame it happens to appear in, so a future D6433 calculation doesn't
double-count it. The PCI Viewer UI (pci_viewer.py) still creates one observation
record per frame today, not yet wired to this model at all; see the Phase 3 audit
for the migration this implies once the UI is updated to produce
InspectionObservations directly.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple

from distress_catalog import NO_SEVERITY, get_distress


class ObservationValidationError(ValueError):
    """Base class for InspectionObservation validation failures."""


class UnknownDistressError(ObservationValidationError):
    pass


class InvalidSeverityError(ObservationValidationError):
    pass


class InvalidQuantityError(ObservationValidationError):
    pass


class InvalidQuantityUnitError(ObservationValidationError):
    pass


class MissingEvidenceError(ObservationValidationError):
    pass


class InvalidSampleUnitAssociationError(ObservationValidationError):
    pass


@dataclass(frozen=True)
class InspectionObservation:
    observation_id: str
    section_id: str
    sample_unit_id: str
    distress_id: str                  # references distress_catalog.DistressDefinition.distress_id
    severity: str                     # NO_SEVERITY ("") or one of that distress's severities
    quantity: float
    quantity_unit: str
    evidence: Tuple[str, ...]         # one or more S3 frame/image references -- see module docstring
    recorded_at: datetime
    recorded_by: Optional[str] = None
    source: str = "manual_ui"         # "manual_ui" | "csv_import" | "cv_detection" (future, unimplemented)
    notes: Optional[str] = None


def validate_observation(obs: InspectionObservation) -> None:
    """Structural validation only: is this a well-formed observation. Whether it's
    usable for a *particular* scoring methodology (e.g. current D6433 scope) is that
    methodology's adapter's job -- see d6433_adapter.normalize_observation()."""
    try:
        distress = get_distress(obs.distress_id)
    except KeyError:
        raise UnknownDistressError(f"Unknown distress_id: {obs.distress_id!r}")

    valid_severities = distress.severities or [NO_SEVERITY]
    if obs.severity not in valid_severities:
        raise InvalidSeverityError(
            f"{obs.severity!r} is not a valid severity for {obs.distress_id!r}; "
            f"expected one of {valid_severities!r}"
        )

    if obs.quantity is None:
        raise InvalidQuantityError(f"quantity is required (distress={obs.distress_id!r})")
    if obs.quantity < 0:
        raise InvalidQuantityError(f"quantity must be non-negative, got {obs.quantity!r}")

    if not obs.quantity_unit:
        raise InvalidQuantityUnitError("quantity_unit is required")
    catalog_unit = distress.quantity_unit
    if catalog_unit is not None and obs.quantity_unit != catalog_unit:
        raise InvalidQuantityUnitError(
            f"quantity_unit {obs.quantity_unit!r} does not match the catalog's "
            f"quantity_unit {catalog_unit!r} for {obs.distress_id!r}"
        )

    if not obs.evidence:
        raise MissingEvidenceError(
            f"observation {obs.observation_id!r} has no visual evidence -- "
            f"evidence must contain at least one S3 frame/image reference"
        )

    if not obs.sample_unit_id:
        raise InvalidSampleUnitAssociationError("sample_unit_id is required")
    if not obs.section_id:
        raise InvalidSampleUnitAssociationError("section_id is required")
