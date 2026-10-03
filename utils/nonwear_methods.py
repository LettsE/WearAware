"""Nonwear detection methods.

Seven methods are offered, each detecting nonwear from a different signal
and rule:

    Method          Signal   Epoch   Rule
    --------------  -------  ------  ----------------------------------------
    5min_0count     counts   1 s     >=5 consecutive min of 0 counts
    10min_0count    counts   1 s     >=10 consecutive min of 0 counts
    20min_0count    counts   1 s     >=20 consecutive min of 0 counts
    30min_0count    counts   1 s     >=30 consecutive min of 0 counts
    60min_0count    counts   1 s     >=60 consecutive min of 0 counts
    Troiano60s      counts   60 s    >=60 consecutive min of 0 counts, allowing
                                      up to 2 consecutive non-zero minutes at or
                                      below 100 cpm; any minute >100 cpm is wear
    Ahmadi          raw g    1 s     30-min sliding interval (1 s sliding window); 
                                    considered nonwear when standard deviation of 
                                    vector magnitude (*g*) per second is < 0.013 *g*; 
                                    if wear period is < 30 min and < 30% of combined 
                                    bordering nonwear periods, marked nonwear



Each ``method_*`` function below takes the raw per-sample DataFrame produced
by ``utils.loader.load_raw_accel_file`` (columns X, Y, Z, Datetime,
tz-naive) and the sampling rate it was recorded/selected at,
and returns a DataFrame of WEAR bouts with columns
``["WearTimeStart", "WearTimeEnd"]`` (studyid is added by the caller in
main.py). ``mask_to_wear_bouts`` turns a per-epoch wear/nonwear boolean series
into that bout format and is shared by all seven methods.

Troiano60s has not been checked against an independent reference
implementation.
"""

import numpy as np
import pandas as pd

from utils.resampler import EPOCH_SECONDS, GAP_TOLERANCE, describe_report, resample_to_target_hz
from agcounts.extract import get_counts

# --- Method parameters -----------------------------------------------------

COUNT_EPOCH_SECONDS = 1  # epoch used for the *min_0count methods
COUNT_ZERO_THRESHOLDS_MIN = {
    "5min_0count": 5,
    "10min_0count": 10,
    "20min_0count": 20,
    "30min_0count": 30,
    "60min_0count": 60,
}

TROIANO_EPOCH_SECONDS = 60
TROIANO_MIN_NONWEAR_MINUTES = 60
TROIANO_SPIKE_ALLOWANCE_MINUTES = 2   # up to this many non-zero minutes tolerated...
TROIANO_WEAR_CPM_THRESHOLD = 100      # ...as long as each is <= this many counts/min; any minute above it is always wear

AHMADI_WINDOW_MINUTES = 30
AHMADI_STD_THRESHOLD_G = 0.013
AHMADI_MIN_WEAR_MINUTES = 30
AHMADI_MIN_WEAR_FRACTION_OF_BORDERING_NONWEAR = 0.30

# agcounts.extract.get_counts only accepts these sampling rates (its band-pass
# filter coefficients are defined for them specifically). A native rate
# outside this set is resampled to COUNTS_TARGET_HZ first -- see
# compute_counts().
AGCOUNTS_SUPPORTED_HZ = sorted(set(range(30, 101, 10)) | {32, 64, 128, 256})
COUNTS_TARGET_HZ = 30
# How close a typed/detected rate must be to a supported integer Hz to be
# treated as that rate outright, rather than resampled. Matches the
# preset-snap tolerance used in main.py, for the same reason: this is for
# clock jitter (29.998 -> 30), not for reclassifying a genuinely different rate.
_NATIVE_RATE_TOLERANCE = 0.01

METHOD_LABELS = {
    "5min_0count": "5min_0count",
    "10min_0count": "10min_0count",
    "20min_0count": "20min_0count",
    "30min_0count": "30min_0count",
    "60min_0count": "60min_0count",
    "troiano60s": "Troiano60s",
    "ahmadi": "Ahmadi",
}


def _run_change_mask(values, times, epoch_seconds, gap_tolerance=GAP_TOLERANCE):
    """Boolean array, True wherever a new run starts in ``values``.

    A new run starts at the first position, at any position whose value
    differs from the one before it, OR at any position that does not follow
    the previous one after (approximately) exactly ``epoch_seconds`` of real
    elapsed time. That last condition matters: an epoch grid built by
    ``compute_counts`` (or a per-second groupby) does not contain rows for a
    real gap in the recording -- it just jumps in time. Without this check, a
    same-valued run on either side of that jump (e.g. two short "all quiet"
    stretches either side of a 20-minute stretch removed by an earlier
    trimming step) would be treated as one long contiguous run and could
    cross a minimum-duration threshold that neither side reaches alone. This
    mirrors why
    ``utils.resampler`` resamples each contiguous segment independently
    rather than across a gap.

    Uses ``GAP_TOLERANCE`` from ``utils.resampler`` for consistency with how
    gaps are defined there.
    """
    values = np.asarray(values)
    times = pd.to_datetime(pd.Series(times)).to_numpy("datetime64[ns]")
    n = len(values)
    if len(times) != n:
        raise ValueError(f"values ({n}) and times ({len(times)}) must be the same length.")

    change = np.empty(n, dtype=bool)
    change[0] = True
    if n > 1:
        value_changed = values[1:] != values[:-1]
        deltas = np.diff(times).astype("int64") / 1e9
        gapped = deltas > (epoch_seconds * gap_tolerance)
        change[1:] = value_changed | gapped
    return change


def mask_to_wear_bouts(epoch_start_times, wear_mask, epoch_seconds):
    """Turn a per-epoch wear/nonwear boolean series into contiguous bouts.

    Parameters
    ----------
    epoch_start_times : array-like of datetime64, sorted, one per epoch. A
        real time gap between epochs (e.g. either side of a stretch removed
        by an earlier trimming step) always starts a new bout, even where
        the mask value doesn't change either side of it -- otherwise a wear
        bout would be reported as spanning time that was never actually
        observed.
    wear_mask : array-like of bool, same length as ``epoch_start_times``.
        True marks a worn epoch.
    epoch_seconds : length of one epoch, in seconds, used to compute each
        bout's end time from its last epoch's start, and to size the gap
        check above.

    Returns
    -------
    DataFrame with columns ``WearTimeStart``, ``WearTimeEnd``, one row per
    contiguous run of worn epochs.
    """
    times = pd.to_datetime(pd.Series(epoch_start_times)).to_numpy("datetime64[ns]")
    mask = np.asarray(wear_mask, dtype=bool)

    if len(times) != len(mask):
        raise ValueError(
            f"epoch_start_times ({len(times)}) and wear_mask ({len(mask)}) "
            "must be the same length."
        )
    if len(times) == 0:
        return pd.DataFrame(columns=["WearTimeStart", "WearTimeEnd"])

    epoch_delta = np.timedelta64(int(round(epoch_seconds * 1e9)), "ns")

    change = _run_change_mask(mask, times, epoch_seconds)
    run_starts = np.flatnonzero(change)
    run_ends = np.append(run_starts[1:], len(mask))

    bouts = []
    for start_idx, end_idx in zip(run_starts, run_ends):
        if not mask[start_idx]:
            continue
        bouts.append((times[start_idx], times[end_idx - 1] + epoch_delta))

    return pd.DataFrame(bouts, columns=["WearTimeStart", "WearTimeEnd"])


def _segment_bounds(times, input_hz):
    """Index bounds of gap-free runs of samples, e.g. either side of a
    stretch removed by an earlier trimming step. Same gap rule as
    ``utils.resampler._segment_bounds`` (shares its ``GAP_TOLERANCE``
    constant) -- duplicated here rather than imported, to keep this module's
    dependency on the resampler limited to its public, tested entry point
    (``resample_to_target_hz``).
    """
    if len(times) == 0:
        return []
    deltas = np.diff(times).astype("int64") / 1e9
    breaks = np.flatnonzero(deltas > GAP_TOLERANCE / input_hz) + 1
    bounds = np.concatenate(([0], breaks, [len(times)]))
    return [(int(a), int(b)) for a, b in zip(bounds[:-1], bounds[1:])]


def _nearest_supported_hz(sampling_rate):
    for hz in AGCOUNTS_SUPPORTED_HZ:
        if abs(sampling_rate - hz) <= hz * _NATIVE_RATE_TOLERANCE:
            return hz
    return None


def compute_counts(data, sampling_rate, epoch_seconds):
    """ActiGraph-comparable activity counts per epoch, from raw acceleration.

    Computed with ``agcounts.extract.get_counts``, called with
    ``freq=<counting rate>, epoch=epoch_seconds, fast=True``. agcounts only
    accepts a fixed set of sampling rates (its filter coefficients are only
    defined for them); when ``sampling_rate`` isn't one of them, the data is
    resampled to ``COUNTS_TARGET_HZ`` (30 Hz) first.

    Each contiguous segment of the recording (split on gaps -- e.g. an
    already wear-trimmed file, or any other stretch removed from an
    otherwise continuous recording) is counted independently and
    epoch-timestamped from its own start, for the same reason
    ``resample_to_target_hz`` does this: counting across a gap would treat
    the empty stretch as more (silent) recording and shift every epoch
    timestamp after it.

    Returns
    -------
    DataFrame with columns ``Datetime`` (epoch start), ``Xcounts``,
    ``Ycounts``, ``Zcounts``, ``VMcounts``. Empty (but correctly columned) if
    ``data`` has no segment long enough to produce a single epoch.
    """
    

    columns = ["Datetime", "Xcounts", "Ycounts", "Zcounts", "VMcounts"]
    if data is None or len(data) == 0:
        return pd.DataFrame(columns=columns)

    native_hz = _nearest_supported_hz(sampling_rate)
    counting_hz = native_hz if native_hz is not None else COUNTS_TARGET_HZ

    times = pd.to_datetime(data["Datetime"]).to_numpy("datetime64[ns]")
    pieces = []
    resample_reports = []
    skipped_segments = 0
    skipped_samples = 0
    # The resampler drops any stretch shorter than one of its 5 s epochs, and
    # raises if that leaves nothing. Such a stretch is skipped here instead, so
    # it cannot take the rest of the recording down with it.
    min_resample_samples = int(np.ceil(EPOCH_SECONDS * sampling_rate))

    for start, stop in _segment_bounds(times, sampling_rate):
        segment = data.iloc[start:stop]

        if native_hz is None:
            if stop - start < min_resample_samples:
                skipped_segments += 1
                skipped_samples += stop - start
                continue

            # _segment_bounds already isolated one gap-free run, so this call
            # sees a single segment and its own internal gap-splitting is a
            # no-op pass-through.
            segment, report = resample_to_target_hz(
                segment, sampling_rate, target_hz=COUNTS_TARGET_HZ
            )
            resample_reports.append(report)

        raw = segment[["X", "Y", "Z"]].to_numpy(dtype=float)
        counts = get_counts(raw, freq=counting_hz, epoch=epoch_seconds, fast=True)
        if counts.shape[0] == 0:
            continue

        seg_start = pd.Timestamp(segment["Datetime"].iloc[0])
        epoch_starts = seg_start + pd.to_timedelta(
            np.arange(counts.shape[0]) * epoch_seconds, unit="s"
        )
        vm_counts = np.sqrt((counts.astype(float) ** 2).sum(axis=1))
        pieces.append(
            pd.DataFrame(
                {
                    "Datetime": epoch_starts,
                    "Xcounts": counts[:, 0],
                    "Ycounts": counts[:, 1],
                    "Zcounts": counts[:, 2],
                    "VMcounts": vm_counts,
                }
            )
        )

    if resample_reports:
        # One summary line for the whole recording. Each report covers a single
        # gap-free segment, so the per-segment figures are summed.
        summary = dict(resample_reports[0])
        for key in ("input_samples", "output_samples", "dropped_segments", "dropped_samples"):
            summary[key] = sum(r[key] for r in resample_reports)
        summary["input_samples"] += skipped_samples
        summary["dropped_segments"] += skipped_segments
        summary["dropped_samples"] += skipped_samples
        summary["segments"] = len(resample_reports) + skipped_segments
        print(describe_report(summary))

    if not pieces:
        return pd.DataFrame(columns=columns)
    return pd.concat(pieces, ignore_index=True)[columns]


def _zero_count_wear_mask(vm_counts, epoch_times, epoch_seconds, min_nonwear_epochs):
    """True (wear) except where an epoch belongs to a run of
    >= min_nonwear_epochs consecutive zero-VMcounts epochs.
    """
    is_zero = np.asarray(vm_counts) <= 0
    n = len(is_zero)
    if n == 0:
        return np.array([], dtype=bool)

    change = _run_change_mask(is_zero, epoch_times, epoch_seconds)
    run_id = np.cumsum(change) - 1
    run_len = np.bincount(run_id)
    is_nonwear = is_zero & (run_len[run_id] >= min_nonwear_epochs)
    return ~is_nonwear


def _consecutive_zero_count_bouts(data, sampling_rate, min_nonwear_minutes):
    """Shared body for the five *_0count methods -- differ only by threshold."""
    counts_df = compute_counts(data, sampling_rate, COUNT_EPOCH_SECONDS)
    if counts_df.empty:
        return pd.DataFrame(columns=["WearTimeStart", "WearTimeEnd"])

    min_nonwear_epochs = min_nonwear_minutes * 60 / COUNT_EPOCH_SECONDS
    wear_mask = _zero_count_wear_mask(
        counts_df["VMcounts"].to_numpy(), counts_df["Datetime"], COUNT_EPOCH_SECONDS, min_nonwear_epochs
    )
    return mask_to_wear_bouts(counts_df["Datetime"], wear_mask, COUNT_EPOCH_SECONDS)


def method_5min_0count(data, sampling_rate):
    return _consecutive_zero_count_bouts(data, sampling_rate, COUNT_ZERO_THRESHOLDS_MIN["5min_0count"])


def method_10min_0count(data, sampling_rate):
    return _consecutive_zero_count_bouts(data, sampling_rate, COUNT_ZERO_THRESHOLDS_MIN["10min_0count"])


def method_20min_0count(data, sampling_rate):
    return _consecutive_zero_count_bouts(data, sampling_rate, COUNT_ZERO_THRESHOLDS_MIN["20min_0count"])


def method_30min_0count(data, sampling_rate):
    return _consecutive_zero_count_bouts(data, sampling_rate, COUNT_ZERO_THRESHOLDS_MIN["30min_0count"])


def method_60min_0count(data, sampling_rate):
    return _consecutive_zero_count_bouts(data, sampling_rate, COUNT_ZERO_THRESHOLDS_MIN["60min_0count"])


def _troiano_wear_mask(vm_counts_per_minute, epoch_times):
    """Not checked against an independent reference implementation; treat
    with more caution than the other methods.

    Scans minute epochs for maximal runs that are either exactly 0 counts, or
    (up to 2 consecutive minutes at a time) non-zero at or below 100 cpm; any
    minute above 100 cpm always ends the run and is always wear. A run only
    counts as nonwear if its total length is >= 60 minutes; a spike run that
    would need a 3rd consecutive non-zero minute to continue is cut there
    instead (the 3rd minute is re-examined as its own potential run start). A
    real time gap between consecutive minute-epochs (e.g. either side of a
    dropped segment) always ends a run too, for the same reason
    ``_run_change_mask`` exists -- two below-threshold stretches either side
    of a gap must not combine into one nonwear run.
    """
    counts = np.asarray(vm_counts_per_minute, dtype=float)
    times = pd.to_datetime(pd.Series(epoch_times)).to_numpy("datetime64[ns]")
    n = len(counts)
    is_wear = np.ones(n, dtype=bool)

    def follows_previous(pos):
        gap_seconds = (times[pos] - times[pos - 1]).astype("int64") / 1e9
        return gap_seconds <= TROIANO_EPOCH_SECONDS * GAP_TOLERANCE

    i = 0
    while i < n:
        if counts[i] > TROIANO_WEAR_CPM_THRESHOLD:
            i += 1
            continue

        j = i
        run_len = 0
        consec_spike = 0
        while j < n and counts[j] <= TROIANO_WEAR_CPM_THRESHOLD and (j == i or follows_previous(j)):
            if counts[j] == 0:
                consec_spike = 0
                run_len += 1
                j += 1
            elif consec_spike < TROIANO_SPIKE_ALLOWANCE_MINUTES:
                consec_spike += 1
                run_len += 1
                j += 1
            else:
                break

        if run_len * TROIANO_EPOCH_SECONDS / 60 >= TROIANO_MIN_NONWEAR_MINUTES:
            is_wear[i:j] = False

        i = j if j > i else i + 1

    return is_wear


def method_troiano60s(data, sampling_rate):
    """60-second-epoch method: minimum 60 consecutive minutes of 0 counts,
    allowing up to 2 consecutive non-zero minutes as long as each is <=100
    counts/min; any minute >100 counts/min is always wear.

    Not checked against an independent reference implementation; treat its
    results with more caution than the other methods.
    """
    counts_df = compute_counts(data, sampling_rate, TROIANO_EPOCH_SECONDS)
    if counts_df.empty:
        return pd.DataFrame(columns=["WearTimeStart", "WearTimeEnd"])

    wear_mask = _troiano_wear_mask(counts_df["VMcounts"].to_numpy(), counts_df["Datetime"])
    return mask_to_wear_bouts(counts_df["Datetime"], wear_mask, TROIANO_EPOCH_SECONDS)


def method_ahmadi(data, sampling_rate):
    """Raw-data method: 30-min sliding interval (1 s step); nonwear when the
    per-second standard deviation of raw vector magnitude (g) is < 0.013 g.
    Bordering-period rule: a wear period < 30 min AND < 30% of its combined
    bordering nonwear duration is re-marked as nonwear.
    """
    if data is None or len(data) == 0:
        return pd.DataFrame(columns=["WearTimeStart", "WearTimeEnd"])

    epoch_seconds = 1
    vm = np.sqrt(data["X"] ** 2 + data["Y"] ** 2 + data["Z"] ** 2)
    per_second = pd.DataFrame(
        {"Datetime": pd.to_datetime(data["Datetime"]).dt.floor("s"), "VM": vm}
    )
    sdvm = per_second.groupby("Datetime")["VM"].std()
    # A second with a single sample (e.g. the last, partial second of a
    # segment) has no variance to compute -- std() gives NaN. Only
    # fully-sampled seconds are meaningful for this threshold, so such
    # seconds are excluded rather than guessed at.
    sdvm = sdvm.dropna()
    if sdvm.empty:
        return pd.DataFrame(columns=["WearTimeStart", "WearTimeEnd"])

    wear_mask = _ahmadi_wear_mask(sdvm.to_numpy(), sdvm.index.to_numpy(), epoch_seconds)
    return mask_to_wear_bouts(sdvm.index.to_numpy(), wear_mask, epoch_seconds)


def _ahmadi_wear_mask(std_vm, epoch_times, epoch_seconds):
    """Two passes: the first marks a low-SD epoch as nonwear only if it
    belongs to a run of at least AHMADI_WINDOW_MINUTES consecutive low-SD
    epochs; the second folds a wear run shorter than AHMADI_MIN_WEAR_MINUTES
    back into nonwear if it also makes up less than
    AHMADI_MIN_WEAR_FRACTION_OF_BORDERING_NONWEAR of its combined bordering
    nonwear duration.
    """
    std_vm = np.asarray(std_vm, dtype=float)
    n = len(std_vm)
    if n == 0:
        return np.array([], dtype=bool)

    min_nonwear_epochs = AHMADI_WINDOW_MINUTES * 60 / epoch_seconds
    min_wear_epochs = AHMADI_MIN_WEAR_MINUTES * 60 / epoch_seconds

    low_sd = std_vm < AHMADI_STD_THRESHOLD_G

    # Pass 1: a low-SD epoch is nonwear only if it belongs to a run of
    # >= min_nonwear_epochs consecutive low-SD epochs.
    change0 = _run_change_mask(low_sd, epoch_times, epoch_seconds)
    run_id = np.cumsum(change0) - 1
    run_len = np.bincount(run_id)
    nonwear = low_sd & (run_len[run_id] >= min_nonwear_epochs)

    # Pass 2: bordering-period merge -- a WEAR run shorter than
    # AHMADI_MIN_WEAR_MINUTES and making up less than 30% of its combined
    # bordering nonwear duration is folded into nonwear.
    change = _run_change_mask(nonwear, epoch_times, epoch_seconds)
    run_starts = np.flatnonzero(change)
    run_ends = np.append(run_starts[1:], n)
    run_values = nonwear[run_starts]  # True = nonwear run, False = wear run
    run_lengths = run_ends - run_starts

    final_nonwear = nonwear.copy()
    for i in range(len(run_values)):
        if run_values[i]:
            continue
        prev_len = run_lengths[i - 1] if i > 0 else 0
        next_len = run_lengths[i + 1] if i < len(run_values) - 1 else 0
        bordering = prev_len + next_len
        if bordering == 0:
            continue  # no bordering nonwear at all -- never reclassified
        if (
            run_lengths[i] < min_wear_epochs
            and (run_lengths[i] / bordering) < AHMADI_MIN_WEAR_FRACTION_OF_BORDERING_NONWEAR
        ):
            final_nonwear[run_starts[i]:run_ends[i]] = True

    return ~final_nonwear  # wear mask


# Dispatch table used by main.py -- keys match the GUI's method radio group.
METHOD_FUNCTIONS = {
    "5min_0count": method_5min_0count,
    "10min_0count": method_10min_0count,
    "20min_0count": method_20min_0count,
    "30min_0count": method_30min_0count,
    "60min_0count": method_60min_0count,
    "troiano60s": method_troiano60s,
    "ahmadi": method_ahmadi,
}


def run_method(method_key, data, sampling_rate):
    """Look up and run one of the methods above by its GUI key."""
    try:
        func = METHOD_FUNCTIONS[method_key]
    except KeyError:
        raise ValueError(
            f"Unknown nonwear method '{method_key}'. Expected one of: "
            + ", ".join(METHOD_FUNCTIONS)
        )
    return func(data, sampling_rate)
