"""
Only used if input sample frequency is not accepted by agcounts. In this case, resamples to 30Hz.
Resampling uses ``scipy.signal.resample_poly`` (polyphase FIR). The anti-alias
filter it applies matters when downsampling: taking every Nth sample of a
100 Hz recording, or interpolating it linearly, folds motion above 15 Hz back
into the retained band as spurious low-frequency content.
"""

from fractions import Fraction

import numpy as np
import pandas as pd
from scipy.signal import resample_poly

# 
TARGET_HZ = 30
EPOCH_SECONDS = 5

# A gap larger than this many nominal sample periods starts a new segment.
GAP_TOLERANCE = 2.0

# Factors up to this size are used exactly, without looking for a smaller
# approximation. resample_poly upsamples by `up` internally and builds a filter
# of roughly 20 * max(up, down) taps, so an exact ratio for an awkward rate such
# as 33.333 Hz (10000/11111) would mean a 222,000-tap filter. Past this bound an
# approximation is sought instead -- see _resample_ratio.
MAX_EXACT_FACTOR = 1000

# Hard ceiling on either factor. A ratio needing more than this is refused
# rather than silently approximated badly.
MAX_RESAMPLE_FACTOR = 20000

# Largest relative error accepted when approximating the ratio. Samples are
# timestamped at the rate actually achieved rather than at the nominal target,
# so this never accumulates into clock drift. Its only consequence is that a
# 150-sample window spans 5 s to within this fraction -- at 1e-4 that is half a
# millisecond, far below anything the features can resolve. Keeping it loose is
# what lets 33.333 Hz use 9/10 instead of a 160,000-tap filter.
RATIO_TOLERANCE = 1e-4

OUTPUT_COLUMNS = ["X", "Y", "Z", "Datetime"]


def detect_sampling_rate(df, datetime_column="Datetime"):
    """Best-effort sampling rate (Hz) inferred from the timestamp spacing.

    Uses the median inter-sample interval, so it is robust to a handful of
    duplicated timestamps or gaps in the recording (e.g. data that has
    already been trimmed to wear-time windows). Returns ``None`` when there
    are too few rows, or when the timestamps carry no usable resolution.
    """
    if df is None or len(df) < 2 or datetime_column not in df.columns:
        return None

    times = pd.to_datetime(df[datetime_column]).to_numpy("datetime64[ns]")
    deltas = np.diff(times).astype("int64") / 1e9  # seconds
    deltas = deltas[deltas > 0]
    if deltas.size == 0:
        return None

    median_delta = float(np.median(deltas))
    if median_delta <= 0:
        return None

    return 1.0 / median_delta


def _resample_ratio(input_hz, target_hz):
    """Integer up/down factors for ``resample_poly``.

    Returns ``(up, down, relative_error)``.

    The exact ratio is used whenever its factors are small: 30 / 12.5 is 12/5,
    so the polyphase filter upsamples by 12 and decimates by 5. Parsing through
    ``Fraction(str(...))`` keeps 12.5 exact rather than inheriting the binary
    floating point value of 12.5.

    An arbitrary rate typed by the user need not have a small exact ratio.
    30 / 33.333 is exactly 10000/11111, which would upsample the signal ten
    thousandfold and build a filter of over a hundred thousand taps. In that
    case the smallest approximation within RATIO_TOLERANCE is used instead --
    9/10 here, wrong by one part in a hundred thousand, against a filter
    of a hundred taps instead of two hundred thousand.
    """
    exact = Fraction(str(target_hz)) / Fraction(str(input_hz))
    if max(exact.numerator, exact.denominator) <= MAX_EXACT_FACTOR:
        return exact.numerator, exact.denominator, 0.0

    # Search from small factors upward and take the first that is accurate
    # enough, so 33.333 Hz resolves to 9/10 rather than to its exact 10000/11111
    # while a rate whose exact ratio is simply large, such as 1/1470, is still
    # found exactly further up the search.
    wanted = float(target_hz) / float(input_hz)
    limit = 1
    while limit <= MAX_RESAMPLE_FACTOR:
        approx = Fraction(wanted).limit_denominator(limit)
        # limit_denominator bounds only the denominator, so the numerator is
        # checked too: 30 Hz from 1e-9 Hz is 30000000000/1, which would build a
        # filter of six hundred billion taps.
        if approx.numerator > 0 and max(approx.numerator, approx.denominator) <= MAX_RESAMPLE_FACTOR:
            error = abs(float(approx) - wanted) / wanted
            if error <= RATIO_TOLERANCE:
                return approx.numerator, approx.denominator, error
        limit *= 2

    raise ValueError(
        f"Cannot resample {input_hz} Hz to {target_hz} Hz: the ratio between "
        "them cannot be expressed accurately with small enough whole-number "
        "factors. Check that the sampling frequency was entered correctly."
    )


def _segment_bounds(times, input_hz):
    """Index bounds of runs of samples with no gap in the recording.

    Segments are resampled independently. Resampling across a gap would
    smear real data into the empty stretch and shift every timestamp after
    it, which matters for data that has already been trimmed for
    wear/nonwear windows, or any other stretch removed from an otherwise
    continuous recording.

    """
    if times.size == 0:
        return []

    deltas = np.diff(times).astype("int64") / 1e9
    breaks = np.flatnonzero(deltas > GAP_TOLERANCE / input_hz) + 1
    bounds = np.concatenate(([0], breaks, [times.size]))
    return [(int(a), int(b)) for a, b in zip(bounds[:-1], bounds[1:])]


def resample_to_target_hz(df, input_hz, target_hz=TARGET_HZ):
    """Return ``df`` resampled to ``target_hz``.

    Parameters
    ----------
    df : DataFrame with columns X, Y, Z, Datetime.
    input_hz : sampling rate of ``df``.
    target_hz : rate to resample to; defaults to 30 Hz.

    Returns
    -------
    (resampled_df, report) where ``report`` is a dict describing what happened,
    suitable for logging or showing in the GUI status line.
    """
    report = {
        "input_hz": input_hz,
        "target_hz": target_hz,
        "resampled": False,
        "segments": 1,
        "dropped_segments": 0,
        "dropped_samples": 0,
        "input_samples": 0 if df is None else len(df),
        "output_samples": 0 if df is None else len(df),
    }

    if df is None or len(df) == 0:
        return df, report

    missing = [c for c in ("X", "Y", "Z", "Datetime") if c not in df.columns]
    if missing:
        raise ValueError(
            "Cannot resample: input data is missing column(s) "
            + ", ".join(missing)
        )

    if input_hz is None or input_hz <= 0:
        raise ValueError(f"Invalid input sampling rate: {input_hz!r}")

    if float(input_hz) == float(target_hz):
        return df.copy(), report

    up, down, ratio_error = _resample_ratio(input_hz, target_hz)
    times = pd.to_datetime(df["Datetime"]).to_numpy("datetime64[ns]")
    axes = df[["X", "Y", "Z"]].to_numpy(dtype=float)

    # When the ratio is approximate the result is not quite target_hz. Time the
    # samples at the rate actually achieved, so the timestamps stay true to the
    # recording instead of drifting from it over a multi-day file.
    achieved_hz = float(input_hz) * up / down
    min_samples = int(np.ceil(EPOCH_SECONDS * input_hz))

    pieces = []
    segments = _segment_bounds(times, input_hz)
    for start, stop in segments:
        n_in = stop - start
        if n_in < min_samples:
            # Too short to yield even one 5 s epoch, and too short for the
            # polyphase filter to behave well. Drop rather than pad.
            report["dropped_segments"] += 1
            report["dropped_samples"] += n_in
            continue

        # padtype="line" extends each end along a linear fit instead of with
        # zeros, which avoids a step discontinuity at the segment edges.
        resampled = resample_poly(
            axes[start:stop], up, down, axis=0, padtype="line"
        )
        n_out = resampled.shape[0]

        # Offsets are computed from the sample index rather than accumulated,
        # so rounding cannot build up across a long recording.
        offsets_ns = np.round(np.arange(n_out) * 1e9 / achieved_hz).astype("int64")
        segment_times = times[start] + offsets_ns

        piece = pd.DataFrame(resampled, columns=["X", "Y", "Z"])
        piece["Datetime"] = pd.to_datetime(segment_times)
        pieces.append(piece)

    if not pieces:
        raise ValueError(
            "No usable data after resampling: every contiguous run of samples "
            f"was shorter than one {EPOCH_SECONDS} s epoch."
        )

    out = pd.concat(pieces, ignore_index=True)

    report["resampled"] = True
    report["segments"] = len(segments)
    report["output_samples"] = len(out)
    report["ratio"] = (up, down)
    report["achieved_hz"] = achieved_hz
    report["ratio_error"] = ratio_error
    return out[OUTPUT_COLUMNS], report


def describe_report(report):
    """One-line human-readable summary of a resampling report."""
    if not report.get("resampled"):
        return f"Data already at {report['target_hz']} Hz; no resampling needed."

    up, down = report.get("ratio", (1, 1))
    msg = (
        f"Resampled {report['input_samples']} samples at {report['input_hz']} Hz "
        f"to {report['output_samples']} samples at {report['target_hz']} Hz "
        f"(x{up}/{down})"
    )
    if report.get("ratio_error"):
        msg += (
            f", approximated to {report['achieved_hz']:.6g} Hz "
            f"({report['ratio_error']:.1e} relative)"
        )
    if report["segments"] > 1:
        msg += f" across {report['segments']} contiguous segments"
    if report["dropped_segments"]:
        msg += (
            f"; dropped {report['dropped_segments']} segment(s) "
            f"({report['dropped_samples']} samples) shorter than "
            f"{EPOCH_SECONDS} s"
        )
    return msg + "."
