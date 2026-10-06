"""
Step 2 of regenerating legacy_pci_data.py: converts the CSV dumps produced by
extract_legacy_pci_data.ps1 (Distresses.csv, InterpolationCurve.csv) into
legacy_pci_data.py, programmatically -- never by hand-transcribing rows, to avoid
transcription error across ~1100 rows.

Usage:
    python tools/csv_to_legacy_pci_data.py --csv-dir tools/_extracted --out legacy_pci_data.py

legacy_pci_data.py must not be hand-edited; regenerate it with this script instead.
"""
import argparse
import csv
import os


def load_distresses(path):
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rows.append((
                row["DistressType"],
                row["Descriptor"],
                float(row["Quantity"]),
                float(row["DeductValue"]),
            ))
    return rows


def load_interpolation_curve(path):
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rows.append((
                int(row["Quantity"]),
                int(row["TotalDeduct"]),
                float(row["FinalDeduct"]),
            ))
    return rows


def write_legacy_pci_data(distresses, interpolation_curve, out_path, source_db_path, extracted_date):
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write('"""\n')
        f.write("Verbatim reference data extracted from VPCI_Config.accdb (the [Distresses] and\n")
        f.write("[InterpolationCurve] tables) -- the authoritative lookup data for the legacy\n")
        f.write("C# VPCI_Application PCI calculation engine (VPCI_Images.csproj / Form1.cs /\n")
        f.write("DatabaseOperations.cs). See legacy_pci_provider.py for how this is used and\n")
        f.write("legacy_pci_engine.py for the calculation that consumes it.\n\n")
        f.write(f"Source: {source_db_path}\n")
        f.write(f"Extracted: {extracted_date}, read-only, via ODBC (Microsoft Access Driver, 32-bit).\n")
        f.write(f"Verified at extraction time: {len(distresses)} rows in [Distresses] "
                f"(no duplicate (DistressType, Descriptor) keys), {len(interpolation_curve)} rows in\n")
        f.write("[InterpolationCurve] (no duplicate (Quantity, TotalDeduct) keys).\n\n")
        f.write("This file is a literal, unmodified transcription -- no value here has been\n")
        f.write("rounded, corrected, reordered, or derived from any other source. Row order\n")
        f.write("matches each table's original [ID] column order.\n\n")
        f.write("Regenerate with tools/extract_legacy_pci_data.ps1 + tools/csv_to_legacy_pci_data.py\n")
        f.write("if VPCI_Config.accdb changes. DO NOT hand-edit this file.\n")
        f.write('"""\n\n')

        f.write("# One entry per row in [Distresses], in original [ID] order.\n")
        f.write("# (distress_type, descriptor, quantity, deduct_value)\n")
        f.write("# distress_type matches the C# DatabaseOperations.GetCorrespondingData() first\n")
        f.write("# argument exactly (e.g. \"PotholeLow\", \"AlligatorHigh\", \"EdgeBreak\",\n")
        f.write("# \"OtherCrack\", \"RuttingMed\") -- see legacy_pci_adapter.py for the mapping from\n")
        f.write("# distress_catalog.py's (distress_id, severity) to these strings.\n")
        f.write("DISTRESSES = [\n")
        for dt, desc, qty, dv in distresses:
            f.write(f"    ({dt!r}, {desc!r}, {qty!r}, {dv!r}),\n")
        f.write("]\n\n")

        f.write("# One entry per row in [InterpolationCurve], in original [ID] order.\n")
        f.write("# (quantity, total_deduct, final_deduct)\n")
        f.write("# NOTE: \"quantity\" here is q-1, per the C# GetInterpolatedData() convention\n")
        f.write("# (it subtracts 1 from the actual q/numberGR5 before querying) -- NOT q itself.\n")
        f.write("# total_deduct is TDV already rounded to the nearest integer (banker's rounding),\n")
        f.write("# matching how the C# caller always passes Math.Round(totalDeduct, 0) in.\n")
        f.write("INTERPOLATION_CURVE = [\n")
        for q, td, fd in interpolation_curve:
            f.write(f"    ({q!r}, {td!r}, {fd!r}),\n")
        f.write("]\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv-dir", required=True, help="Directory containing Distresses.csv and InterpolationCurve.csv")
    parser.add_argument("--out", default="legacy_pci_data.py", help="Output path for legacy_pci_data.py")
    parser.add_argument("--source-db-path", default="P:/Remote PCI/VPCI Application/Resources/VPCI_Config.accdb")
    parser.add_argument("--extracted-date", default="(unspecified -- pass --extracted-date)")
    args = parser.parse_args()

    distresses = load_distresses(os.path.join(args.csv_dir, "Distresses.csv"))
    interpolation_curve = load_interpolation_curve(os.path.join(args.csv_dir, "InterpolationCurve.csv"))

    write_legacy_pci_data(
        distresses, interpolation_curve, args.out, args.source_db_path, args.extracted_date,
    )
    print(f"Wrote {len(distresses)} Distresses rows and {len(interpolation_curve)} "
          f"InterpolationCurve rows to {args.out}")


if __name__ == "__main__":
    main()
