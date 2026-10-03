"""Loading of raw accelerometer files.

Two input formats are supported:

* ``.gt3x`` -- ActiGraph raw files, read with pygt3x.
* ``.csv``  -- raw triaxial data from any device, with one row per sample.

Both are returned in the same shape: a DataFrame with columns
``X``, ``Y``, ``Z`` and ``Datetime``, sorted by time.

"""

import csv
import datetime as _datetime
import os
import re

import numpy as np
import pandas as pd
from pygt3x.reader import FileReader

SUPPORTED_EXTENSIONS = (".gt3x", ".csv")

# Accepted spellings for each required column. Headers are normalised by
# lower-casing and stripping everything that is not a letter or digit, so
# "Accelerometer X", "accel_x" and "X (g)" all collapse onto entries here.
AXIS_ALIASES = {
    "X": ("x", "xg", "accx", "accelx", "accelerationx", "accelerometerx", "xaxis", "axisx"),
    "Y": ("y", "yg", "accy", "accely", "accelerationy", "accelerometery", "yaxis", "axisy"),
    "Z": ("z", "zg", "accz", "accelz", "accelerationz", "accelerometerz", "zaxis", "axisz"),
}

# Timestamp aliases, most preferred group first. 
DATETIME_ALIAS_GROUPS = (
    ("datetime", "datetimes", "datetimelocal", "localdatetime"),
    ("local", "localtime", "timelocal", "clocktime"),
    ("timestamp", "timestamps", "time", "times", "date", "dt"),
    ("utc", "utctime", "datetimeutc", "utcdatetime", "timeutc", "gmt"),
    ("unixts", "unixtime", "unixtimestamp", "unix", "epoch", "epochtime", "epochms", "unixtimestampms"),
)
EPOCH_ALIASES = DATETIME_ALIAS_GROUPS[-1]

# Fraction of rows that may share a timestamp with an earlier row before the
# file is treated as having lost its sub-second precision (the classic symptom
# of a timestamp column that has been through Excel).
_MAX_DUPLICATE_TIMESTAMP_FRACTION = 0.2

# Plausible range for the median vector magnitude of worn raw data in g.
# Data in m/s^2 lands near 9.81; activity counts land far higher.
_MIN_PLAUSIBLE_MEDIAN_VM = 0.5
_MAX_PLAUSIBLE_MEDIAN_VM = 4.0


def _normalise(name):
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _map_columns(columns):
    """Map the file's headers onto canonical names.

    Returns ``{canonical: original}``. Raises when a required column is
    missing or when two headers claim the same canonical name.
    """
    normalised = {}
    for original in columns:
        normalised.setdefault(_normalise(original), []).append(original)

    mapping = {}
    for canonical, aliases in AXIS_ALIASES.items():
        matches = []
        for alias in aliases:
            matches.extend(normalised.get(alias, []))
        # Preserve file order, drop duplicates.
        matches = list(dict.fromkeys(matches))

        if len(matches) > 1:
            raise ValueError(
                f"Ambiguous columns for {canonical}: {matches}. "
                "Rename or remove the extra column so only one remains."
            )
        if matches:
            mapping[canonical] = matches[0]

    # Timestamp columns are resolved by preference rather than all at once, so a
    # file carrying both "local" and "utc" is not an ambiguity error.
    for group in DATETIME_ALIAS_GROUPS:
        matches = []
        for alias in group:
            matches.extend(normalised.get(alias, []))
        matches = list(dict.fromkeys(matches))

        if len(matches) > 1:
            raise ValueError(
                f"Ambiguous timestamp columns: {matches}. These are equally "
                "preferred, so rename the one you want to 'Datetime'."
            )
        if matches:
            mapping["Datetime"] = matches[0]
            break

    missing = [c for c in ("Datetime", "X", "Y", "Z") if c not in mapping]
    if missing:
        raise ValueError(
            "CSV is missing required column(s): "
            + ", ".join(missing)
            + ". Expected headers Datetime, X, Y, Z (case-insensitive; "
            "common variants such as 'timestamp', 'local', 'utc' or "
            "'Accelerometer X' are also accepted). Found: "
            f"{[str(c) for c in columns]}"
        )

    return mapping


def _detect_delimiter(path):
    """Sniff the field separator so tab- and semicolon-delimited files load."""
    with open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
        sample = handle.read(8192)
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _parse_epoch(series, column_name):
    """Parse a numeric Unix epoch column, inferring its unit from magnitude."""
    values = pd.to_numeric(series, errors="coerce")
    typical = float(np.nanmedian(np.abs(values)))

    if not np.isfinite(typical) or typical <= 0:
        raise ValueError(
            f"Timestamp column '{column_name}' holds no usable numbers."
        )

    for limit, unit in ((1e11, "s"), (1e14, "ms"), (1e17, "us"), (np.inf, "ns")):
        if typical < limit:
            break

    parsed = pd.to_datetime(values, unit=unit, errors="coerce")

    # A column written out in scientific notation (1.75086E+12) has lost every
    # digit below the hour. Catch it here rather than silently collapsing the
    # recording to a handful of rows when duplicates are dropped.
    distinct = parsed.dropna().nunique()
    if len(parsed) > 10 and distinct < len(parsed) * 0.5:
        raise ValueError(
            f"Timestamp column '{column_name}' has lost its precision: "
            f"{len(parsed)} rows share only {distinct} distinct times. This "
            "usually means the column was saved in scientific notation (for "
            "example 1.75086E+12) after being opened in a spreadsheet. Use a "
            "text date column instead, or re-export without opening the file."
        )

    return parsed


def _parse_datetimes(series, column_name):
    """Parse a timestamp column to tz-naive wall-clock time.

    Time zones are stripped rather than carried through: nonwear bouts are
    reported as wall-clock WearTimeStart/WearTimeEnd, compared against
    tz-naive logbook times downstream, and days are cut on the clock the data
    was recorded in.
    """
    if _normalise(column_name) in EPOCH_ALIASES or pd.api.types.is_numeric_dtype(series):
        return _parse_epoch(series, column_name)

    try:
        parsed = pd.to_datetime(series, errors="coerce")
        mixed_offsets = parsed.dtype == object
    except ValueError:
        # pandas refuses to parse a column whose rows carry different UTC
        # offsets, which is what a recording spanning a daylight-saving change
        # looks like.
        parsed, mixed_offsets = None, True

    if not mixed_offsets:
        tz = getattr(parsed.dtype, "tz", None)
        if tz is not None:
            # Single fixed offset: keep the reading, drop the zone.
            return parsed.dt.tz_localize(None)
        return parsed

    # Offsets vary within the file. Normalise to UTC, then re-express everything
    # in the offset of the first sample, so the samples stay evenly spaced.
    # Following the local clock instead would repeat or skip an hour, which
    # would put a false gap or a false overlap into the signal.
    as_utc = pd.to_datetime(series, errors="coerce", utc=True)
    if as_utc.dropna().empty:
        return as_utc

    non_null = series.dropna()
    offset = None
    if not non_null.empty:
        try:
            offset = pd.Timestamp(str(non_null.iloc[0])).utcoffset()
        except (ValueError, TypeError):
            offset = None

    if offset is None:
        return as_utc.dt.tz_localize(None)
    return as_utc.dt.tz_convert(_datetime.timezone(offset)).dt.tz_localize(None)


def _check_actigraph_header(path):
    """Raise a helpful error for an unmodified ActiGraph CSV export."""
    with open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
        first_line = handle.readline()

    if first_line.lstrip().startswith("---"):
        raise ValueError(
            f"{os.path.basename(path)} looks like a raw ActiGraph CSV export "
            "with its 10-line metadata header still attached, and it has no "
            "per-sample timestamps. Re-export it with the header removed and "
            "a Datetime column included, or use the .gt3x file "
            "instead."
        )


def _validate_units(df, path):
    """Fail loudly on data that is not in units of g."""
    magnitude = np.sqrt(df["X"] ** 2 + df["Y"] ** 2 + df["Z"] ** 2)
    median_vm = float(np.nanmedian(magnitude))

    if not np.isfinite(median_vm):
        raise ValueError(
            f"{os.path.basename(path)}: X/Y/Z contain no usable numeric values."
        )

    if median_vm > _MAX_PLAUSIBLE_MEDIAN_VM:
        hint = (
            "This looks like acceleration in m/s^2 -- "
            "convert to g."
            if median_vm < 30
            else "This looks like activity counts rather than raw "
            "acceleration; nonwear detection here requires raw data."
        )
        raise ValueError(
            f"{os.path.basename(path)}: median vector magnitude is "
            f"{median_vm:.2f}, but raw data in g sits near 1.0. {hint}"
        )

    if median_vm < _MIN_PLAUSIBLE_MEDIAN_VM:
        raise ValueError(
            f"{os.path.basename(path)}: median vector magnitude is "
            f"{median_vm:.3f}, far below the ~1.0 expected of raw data in g. "
            "Check that gravity has not been removed from the signal and that "
            "the correct columns were selected."
        )


def load_gt3x(path):
    with FileReader(path) as reader:
        dfraw = reader.to_pandas()
        dfraw['Datetime'] = pd.to_datetime(dfraw.index, unit='s')
        return dfraw


def load_csv(path):
    """Load raw triaxial accelerometer data from a CSV file.

    The file needs one row per sample with a timestamp column and three
    acceleration columns in units of g. Any magnitude column in the file is
    ignored.
    """
    _check_actigraph_header(path)
    name = os.path.basename(path)

    # skipinitialspace handles "utc, local, x" style headers, where every field
    # after the first carries a leading space in both header and data rows.
    raw = pd.read_csv(path, sep=_detect_delimiter(path), skipinitialspace=True)
    raw.columns = [str(c).strip() for c in raw.columns]
    if raw.empty:
        raise ValueError(f"{name} contains no data rows.")

    mapping = _map_columns(raw.columns)

    # Say which columns were used. When a file offers several time columns the
    # choice changes which calendar day a wear bout is credited to, so it
    # should not be silent.
    print(
        "%s: using %s"
        % (name, ", ".join("%s -> %s" % (k, mapping[k]) for k in ("Datetime", "X", "Y", "Z")))
    )

    df = pd.DataFrame(
        {
            "X": pd.to_numeric(raw[mapping["X"]], errors="coerce"),
            "Y": pd.to_numeric(raw[mapping["Y"]], errors="coerce"),
            "Z": pd.to_numeric(raw[mapping["Z"]], errors="coerce"),
            "Datetime": _parse_datetimes(raw[mapping["Datetime"]], mapping["Datetime"]),
        }
    )

    bad_rows = int(df.isna().any(axis=1).sum())
    if bad_rows:
        df = df.dropna().reset_index(drop=True)
        if df.empty:
            raise ValueError(
                f"{name}: no rows had a valid timestamp and numeric X/Y/Z. "
                "Check the date format and decimal separator."
            )

    df = df.sort_values("Datetime")
    before = len(df)
    df = df.drop_duplicates(subset="Datetime", keep="first").reset_index(drop=True)
    dropped = before - len(df)

    # Losing a few repeated timestamps is normal; losing many means the
    # timestamp column does not resolve individual samples, and silently
    # keeping one row per distinct time would throw most of the recording away.
    if before and dropped > before * _MAX_DUPLICATE_TIMESTAMP_FRACTION:
        raise ValueError(
            f"{name}: {dropped} of {before} rows share a timestamp with an "
            f"earlier row, using column '{mapping['Datetime']}'. That column "
            "does not resolve individual samples -- check that it has "
            "sub-second precision and was not rounded by a spreadsheet."
        )

    if len(df) < 2:
        raise ValueError(f"{name}: fewer than two distinct timestamps.")

    _validate_units(df, path)

    return df[["X", "Y", "Z", "Datetime"]]


def is_supported_input(filename):
    return filename.lower().endswith(SUPPORTED_EXTENSIONS)


def load_raw_accel_file(path):
    """Load a .gt3x or .csv file into the common raw-data DataFrame."""
    extension = os.path.splitext(path)[1].lower()

    if extension == ".gt3x":
        return load_gt3x(path)
    if extension == ".csv":
        return load_csv(path)

    raise ValueError(
        f"Unsupported input file type '{extension}'. "
        f"Supported types: {', '.join(SUPPORTED_EXTENSIONS)}."
    )
