"""
core/filename_parser.py

Parses the new flat-dataset filename convention:

    {category}_{HHMM}_{imageID}.ext

Examples:
    highRes_1013_CB3758F596E3457A1296F45867E46BD78.jpg
    edgeCase_2155_F817D4732D1AA446D41CC20FCAAA7619D.png

Returns a dict with:
    category     (str)  - e.g. "highRes"
    arrival_time (str)  - "HH:MM" e.g. "10:13"
    image_uuid   (str)  - the hex identifier, no extension
    is_night     (bool) - True when arrival_time hour >= 12
"""

from pathlib import Path
from .logger import get_logger

log = get_logger(__name__)


def parse_filename(filename: str) -> dict:
    """
    Parse a new-system image filename into its semantic components.

    Args:
        filename: bare filename or full path, e.g.
                  "highRes_1013_CB3758F596E3457A1296F45867E46BD78.jpg"

    Returns:
        {
            "category":     str,   # "highRes"
            "arrival_time": str,   # "10:13"
            "image_uuid":   str,   # "CB3758F596E3457A1296F45867E46BD78"
            "is_night":     bool,  # True if hour >= 12
        }

    Raises:
        ValueError: if the filename does not conform to the
                    {category}_{HHMM}_{imageID} pattern.
    """
    stem = Path(filename).stem  # strip directory and extension

    # Split on underscores — we expect at least 3 parts:
    #   parts[0]  = category   (e.g. "highRes")
    #   parts[1]  = HHMM       (e.g. "1013")
    #   parts[2:] = imageID segments (join back with '_' for safety)
    parts = stem.split("_")

    if len(parts) < 3:
        raise ValueError(
            f"Filename '{filename}' does not match the expected pattern "
            f"'{{category}}_{{HHMM}}_{{imageID}}.ext'. Got {len(parts)} segment(s)."
        )

    category = parts[0]
    hhmm_raw = parts[1]
    image_uuid = "_".join(parts[2:])  # re-join in case imageID itself contains '_'

    # Validate and parse the HHMM time token
    if len(hhmm_raw) != 4 or not hhmm_raw.isdigit():
        raise ValueError(
            f"Filename '{filename}': time segment '{hhmm_raw}' is not a valid "
            f"4-digit HHMM string (e.g. '1013' for 10:13)."
        )

    if hhmm_raw == "9999":
        arrival_time = None
        is_night = False
    else:
        hour = int(hhmm_raw[:2])
        minute = int(hhmm_raw[2:])

        if not (0 <= hour <= 23) or not (0 <= minute <= 59):
            raise ValueError(
                f"Filename '{filename}': HHMM '{hhmm_raw}' encodes an invalid time "
                f"(hour={hour}, minute={minute})."
            )

        arrival_time = f"{hour:02d}:{minute:02d}"
        is_night = hour >= 12

    return {
        "category": category,
        "arrival_time": arrival_time,
        "image_uuid": image_uuid,
        "is_night": is_night,
    }


def safe_parse_filename(filename: str) -> dict | None:
    """
    Like parse_filename() but returns None instead of raising.
    Logs a warning so callers don't swallow errors silently.
    """
    try:
        return parse_filename(filename)
    except ValueError as e:
        log.warning(f"[filename_parser] Skipping '{filename}': {e}")
        return None
