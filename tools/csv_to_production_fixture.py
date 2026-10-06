"""
Converts the CSV produced by extract_production_join.ps1 into
legacy_pci_production_fixture.py -- a local, immutable, network-independent test
fixture of real C# production InputData/VPCIData pairs, for differential testing
(see test_legacy_pci_golden.py).

Never hand-edit legacy_pci_production_fixture.py; regenerate it with this script.

Usage:
    python tools/csv_to_production_fixture.py --csv tools/_extracted/production_join.csv \
        --out legacy_pci_production_fixture.py \
        --source-db-name "Adman L3001 Rathangan Jan23 PCI.accdb" \
        --extracted-date 2026-10-06
"""
import argparse
import csv

COLUMN_TO_STORAGE_KEY = {
    "In_PotholesLow": "potholes_low",
    "In_PotholesMed": "potholes_medium",
    "In_PotholesHigh": "potholes_high",
    "In_PatchingLow": "patching_low",
    "In_PatchingMed": "patching_medium",
    "In_PatchingHigh": "patching_high",
    "In_AlligatorLow": "alligator_cracking_low",
    "In_AlligatorMed": "alligator_cracking_medium",
    "In_AlligatorHigh": "alligator_cracking_high",
    "In_RuttingMed": "rutting_medium",
    "In_RuttingHigh": "rutting_high",
    "In_Ravelling": "raveling",
    "In_Depression": "depression",
    "In_Disintergration": "disintegration",
    "In_Bleeding": "bleeding",
    "In_EdgeBreakup": "edge_breakup",
    "In_OtherCracking": "other_cracking",
}


def load_rows(csv_path):
    cases = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            labels = {storage_key: row[col] for col, storage_key in COLUMN_TO_STORAGE_KEY.items()}
            cases.append({
                "case_id": row["InputID"],
                "road": row["Road"],
                "chainage": float(row["Chainage"]),
                "labels": labels,
                "expected_pci": int(round(float(row["V_PCI"]))),
                "expected_tdv": float(row["V_TotalDeduct"]),
                "expected_q": int(round(float(row["V_ColumnGreater5"]))),
                "expected_structure_deduct_value": float(row["V_StructureDeductValue"]),
                "expected_structure_deduct_percentage": int(round(float(row["V_StructureDeductPercentage"]))),
                "expected_surface_deduct_value": float(row["V_SurfaceDeductValue"]),
                "expected_surface_deduct_percentage": int(round(float(row["V_SurfaceDeductPercentage"]))),
                "expected_other_deduct_value": float(row["V_OtherDeductValue"]),
            })
    return cases


def write_fixture(cases, out_path, source_db_name, extracted_date):
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write('"""\n')
        f.write("Real C# production InputData/VPCIData cases, for differential testing of\n")
        f.write("legacy_pci_engine.py against the actual VPCI_Application.exe (see\n")
        f.write("test_legacy_pci_golden.py). This fixture is EXTRACTED data, not written by\n")
        f.write("hand -- see tools/extract_production_join.ps1 +\n")
        f.write("tools/csv_to_production_fixture.py to regenerate it.\n\n")
        f.write(f"Source: {source_db_name} (InputData INNER JOIN VPCIData ON Road, Chainage=ChainageStart)\n")
        f.write(f"Extracted: {extracted_date}, READ-ONLY, from a local copy of the production\n")
        f.write("backup -- the production file itself was never opened directly; see the\n")
        f.write("project's production-validation deliverable report for the full safety\n")
        f.write("chain (local copy, size-verified, locked read-only, never touched again).\n\n")
        f.write(f"{len(cases)} rows -- every InputData row in this database joined 1:1 with a\n")
        f.write("VPCIData row (zero orphans either direction), i.e. this is the FULL rated,\n")
        f.write("non-skipped population of this database, not a cherry-picked sample.\n\n")
        f.write("`labels` is {storage_key: selected_descriptor} exactly as\n")
        f.write("legacy_pci_adapter.build_cells_from_labels() expects -- i.e. literally what\n")
        f.write("InputData stored for that sample unit's 17 combo-box selections.\n\n")
        f.write("`expected_*` fields come directly from the matching VPCIData row -- see\n")
        f.write("legacy_pci_engine.py's module docstring for the column-name swap this\n")
        f.write("extraction already accounts for (VPCIData's un-suffixed distress columns\n")
        f.write("hold Quantity; its \"...Quantity\"-suffixed columns hold DeductValue -- the\n")
        f.write("opposite of what their names say, confirmed against this exact data). There\n")
        f.write("is no stored maxDeduct/finalDeduct column in VPCIData; finalDeduct is exactly\n")
        f.write("recoverable as 100-PCI (not independent information, see the deliverable\n")
        f.write("report), so it is not duplicated here.\n")
        f.write('"""\n')
        f.write("from dataclasses import dataclass\n")
        f.write("from typing import Dict, List\n\n\n")
        f.write("@dataclass(frozen=True)\n")
        f.write("class ProductionGoldenCase:\n")
        f.write("    case_id: str\n")
        f.write("    road: str\n")
        f.write("    chainage: float\n")
        f.write("    labels: Dict[str, str]\n")
        f.write("    expected_pci: int\n")
        f.write("    expected_tdv: float\n")
        f.write("    expected_q: int\n")
        f.write("    expected_structure_deduct_value: float\n")
        f.write("    expected_structure_deduct_percentage: int\n")
        f.write("    expected_surface_deduct_value: float\n")
        f.write("    expected_surface_deduct_percentage: int\n")
        f.write("    expected_other_deduct_value: float\n\n\n")
        f.write("PRODUCTION_GOLDEN_CASES: List[ProductionGoldenCase] = [\n")
        for c in cases:
            f.write("    ProductionGoldenCase(\n")
            f.write(f"        case_id={c['case_id']!r}, road={c['road']!r}, chainage={c['chainage']!r},\n")
            f.write(f"        labels={c['labels']!r},\n")
            f.write(f"        expected_pci={c['expected_pci']!r}, expected_tdv={c['expected_tdv']!r}, "
                    f"expected_q={c['expected_q']!r},\n")
            f.write(f"        expected_structure_deduct_value={c['expected_structure_deduct_value']!r}, "
                    f"expected_structure_deduct_percentage={c['expected_structure_deduct_percentage']!r},\n")
            f.write(f"        expected_surface_deduct_value={c['expected_surface_deduct_value']!r}, "
                    f"expected_surface_deduct_percentage={c['expected_surface_deduct_percentage']!r},\n")
            f.write(f"        expected_other_deduct_value={c['expected_other_deduct_value']!r},\n")
            f.write("    ),\n")
        f.write("]\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--out", default="legacy_pci_production_fixture.py")
    parser.add_argument("--source-db-name", default="(unspecified -- pass --source-db-name)")
    parser.add_argument("--extracted-date", default="(unspecified -- pass --extracted-date)")
    args = parser.parse_args()

    cases = load_rows(args.csv)
    write_fixture(cases, args.out, args.source_db_name, args.extracted_date)
    print(f"Wrote {len(cases)} production golden cases to {args.out}")


if __name__ == "__main__":
    main()
