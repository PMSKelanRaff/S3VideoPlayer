"""Shared S3 imagery + RSP metadata logic used by both the Image Viewer and Pavement
Inspection Viewer modules."""
import re
import os

# Red-to-green gradient for a 1-10 condition/QA rating, shared by every module so
# a given score always renders as the same colour everywhere in the app.
RATING_COLORS = {
    1: "#D32F2F",   # Red
    2: "#F4511E",   # Deep Orange
    3: "#FB8C00",   # Orange
    4: "#FFB300",   # Amber
    5: "#FDD835",   # Dark Yellow
    6: "#FFEE58",   # Yellow
    7: "#D4E157",   # Lime
    8: "#9CCC65",   # Yellow-Green
    9: "#66BB6A",   # Light Green
    10: "#00E676"   # Bright Green
}


def parse_rsp_file(path):
    """Universal parser for RSP files. Returns a list of per-frame metadata dicts,
    in file order, each with Filename/Date/Chainage(m)/Lat/Lng/Alt."""
    metadata_list = []
    filename_val = os.path.splitext(os.path.basename(path))[0].upper()
    date_val = "Unknown"

    with open(path, 'r', encoding='utf-8-sig', errors='ignore') as f:
        content = f.read()
        lines = re.split(r'\r\n|\r|\n', content)

        for line in lines:
            parts = [p.strip().replace('"', '') for p in line.split(',')]

            if line.startswith("5011,") and len(parts) >= 6:
                date_val = f"{parts[3]}/{parts[4]}/{parts[5]}"
            elif line.startswith("5003,") and len(parts) >= 4:
                filename_val = parts[3]
            elif line.startswith("5280,") and len(parts) > 7:
                try:
                    chainage_km = float(parts[1])
                    chainage_m = round(chainage_km * 1000, 3)
                except ValueError:
                    chainage_m = 0.0

                metadata_list.append({
                    "Filename": filename_val,
                    "Date": date_val,
                    "Chainage": chainage_m,
                    "Lat": parts[5],
                    "Lng": parts[6],
                    "Alt": parts[7]
                })

    return metadata_list


def compute_section_boundaries(chainage_from_m, chainage_to_m, section_length_m=100.0):
    """Splits [chainage_from_m, chainage_to_m] into fixed-length sections, returning the
    chainage (m) at the end of each section. Any final remainder shorter than
    section_length_m is folded into the previous section instead of forming its own
    short section (e.g. a 620m run becomes five 100m sections plus one 120m section,
    not five 100m sections plus a trailing 20m one)."""
    length = chainage_to_m - chainage_from_m
    if length <= 0:
        return [chainage_to_m]

    n_sections = int(length // section_length_m)
    if n_sections == 0:
        return [chainage_to_m]

    boundaries = [chainage_from_m + section_length_m * i for i in range(1, n_sections)]
    boundaries.append(chainage_to_m)
    return boundaries


class S3FrameSource:
    """Wraps an S3 client + bucket for listing/fetching survey frame images."""

    def __init__(self, s3_client, bucket_name):
        self.s3 = s3_client
        self.bucket_name = bucket_name

    def list_frames(self, prefix):
        """Returns sorted S3 keys under prefix ending in .jpg/.jpeg."""
        image_keys = []
        paginator = self.s3.get_paginator('list_objects_v2')
        pages = paginator.paginate(Bucket=self.bucket_name, Prefix=prefix)
        for page in pages:
            if 'Contents' in page:
                for obj in page['Contents']:
                    key = obj['Key']
                    if key.lower().endswith(('.jpg', '.jpeg')):
                        image_keys.append(key)
        image_keys.sort()
        return image_keys

    def fetch_image_bytes(self, key):
        response = self.s3.get_object(Bucket=self.bucket_name, Key=key)
        return response['Body'].read()

    @staticmethod
    def filter_by_chainage(image_keys, metadata_list, chainage_from_m, chainage_to_m):
        """Pairs image_keys with metadata_list by index (RSP frame order matches sorted
        key order) and keeps only frames whose Chainage falls within [from, to]."""
        filtered_keys = []
        filtered_metadata = []
        for idx, key in enumerate(image_keys):
            meta = metadata_list[idx] if idx < len(metadata_list) else None
            if meta is not None:
                c = meta.get("Chainage", 0.0)
                if chainage_from_m <= c <= chainage_to_m:
                    filtered_keys.append(key)
                    filtered_metadata.append(meta)
        return filtered_keys, filtered_metadata
