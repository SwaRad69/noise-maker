# Camera Noise Experiment — YUYV Dark Capture (`yuyv-dark-001`)

## 0. Purpose and scope

This experiment asks a single question:

> Does the directly captured YUYV stream from the covered laptop webcam contain
> useful temporal variation worth pursuing as a physical entropy source?

It analyzes `test_yuyv.yuv`, a DirectShow/FFmpeg rawvideo (YUY2) capture from the
Lenovo Legion's `Integrated Camera`, without converting YUYV to BGR. The analysis
is diagnostic only. No entropy extraction, conditioning, hashing, or RNG is
performed or implied. Throughout this report the chain is respected:

```
physical variation != entropy != independent entropy != cryptographic security
```

YUYV is an uncompressed video representation exposed by the camera, **not** raw
sensor data, and uncompressed does not mean unprocessed.

---

## 1. Hardware

| Item | Value |
|---|---|
| Host | Lenovo Legion laptop, Windows 11 (build 10.0.26200-SP0, AMD64) |
| Camera device name | `Integrated Camera` |
| Camera driver | Realtek, `oem47.inf`, driver 10.0.22000.20384 |
| USB device ID | `USB\VID_04F2&PID_B828&MI_00\7&17eca250&0&0000` |
| Capture backend | DirectShow via FFmpeg (`dshow`) |
| Requested format | `rawvideo (YUY2 / 0x32595559), yuyv422, 1280x720, 30 fps` |
| Supported formats on device | YUYV 4:2:2, NV12 (DirectShow); RAW/Bayer not exposed |
| Dark-frame procedure | Lens physically covered during capture (declared; not software-verifiable) |

The DirectShow device exposes no RAW/Bayer/RGGB/BGGR or high-bit-depth media
types. This file therefore cannot contain raw sensor data.

---

## 2. Acquisition and verification

### 2.1 File verification (mismatch with stated spec)

| Check | Expected (spec) | Measured | Result |
|---|---|---|---|
| File exists | `test_yuyv.yuv` | yes | OK |
| Byte size | `1280×720×2×30 = 55,296,000` | **552,960,000** | **MISMATCH (exactly 10×)** |
| Frames at 1280×720 | 30 | **300** (`552,960,000 / 1,843,200`) | 300, not 30 |
| YUYV 4:2:2 packing | `Y0 U0 Y1 V0 Y2 U1 Y3 V1 ...` | confirmed (see 2.2) | OK |

The file is **inconsistent** with a 30-frame 1280×720 YUYV capture. It is exactly
consistent with **300 frames of 1280×720 YUYV 4:2:2** (300 × 1,843,200 bytes).
This was confirmed by direct byte-pattern inspection (below), and by the fact that
no frame equals its counterpart 30 frames earlier (it is not a 30-frame sequence
repeated 10 times). The 640×480 (900 frames) and 640×360 (1200 frames)
alternatives are numerically possible but are inconsistent with the stated
DirectShow 1280×720 capture format. The analysis below proceeds on the verified
**300-frame @ 1280×720** interpretation, reported explicitly rather than
silently. All downstream figures use `F=300`, `H=720`, `W=1280`.

### 2.2 Packing verification

Byte-value statistics by position `mod 4` over the first frames confirm the
YUYV macropixel layout `[Y0 U0 Y1 V0]`:

| Position mod 4 | Role | min | max | mean |
|---|---|---|---|---|
| 0 | Y | 16 | 21 | 16.21 |
| 1 | U | 126 | 139 | 127.99 |
| 2 | Y | 16 | 20 | 16.21 |
| 3 | V | 126 | 132 | 128.12 |

Y sits at the video-black floor (16, limited-range mapping) rather than digital
zero; U and V sit at neutral 128. This is the signature of an ISP-processed,
video-range YUYV stream — not a raw sensor readout.

### 2.3 Decoding (no BGR conversion)

| Array | Shape | dtype | Sampling |
|---|---|---|---|
| Packed | `(300, 720, 2560)` | `uint8` | `Y0 U0 Y1 V0 ...` |
| Y | `(300, 720, 1280)` | `uint8` | one Y per pixel |
| U | `(300, 720, 640)` | `uint8` | every even macropixel x |
| V | `(300, 720, 640)` | `uint8` | every odd macropixel x |

U and V share the vertical grid and are horizontally sub-sampled 2×. They are
per-macropixel chroma values, not per-pixel sensor samples, so U/V samples cannot
be treated as independent of the neighboring Y samples.

---

## 3. Basic signal characterization

### 3.1 Overall statistics

| Metric | Y | U | V |
|---|---|---|---|
| Sample count | 276,480,000 | 138,240,000 | 138,240,000 |
| min | 16 | 114 | 124 |
| max | 65 | 161 | 151 |
| mean | 22.471 | 127.989 | 128.128 |
| std | 3.145 | 0.177 | 0.467 |
| median | 21 | 128 | 128 |
| p25 / p75 | 21 / 22 | 128 / 128 | 128 / 128 |
| unique values | 48 | 44 | 26 |
| zero fraction | 0.0000 | 0.0000 | 0.0000 |
| 255 fraction | 0.0000 | 0.0000 | 0.0000 |

Histogram top levels (fraction): Y → 21 (57.9%), 22 (11.4%), 26 (6.7%), 29
(6.1%), 28 (3.2%), 25 (3.0%), 31 (2.0%), 16 (2.0%). U → 128 (98.7%). V → 128
(79.4%), 129 (16.3%).

Unlike the BGR default path (dark-001), there is **no zero clamping**: Y sits at
the video-black floor and covers 48 levels in 16–65. U/V are essentially neutral
constant chroma.

### 3.2 Per-frame quantities and their frame-to-frame changes

| Quantity (Y) | Value |
|---|---|
| Frame-mean range | 16.19 → 31.14 LSB (overall mean 22.47, std 3.12) |
| Frame-mean changes | 101 of 299 transitions change the frame mean |
| Per-frame spatial std | mean 0.40 (range 0.09–0.84) |
| Per-frame distinct Y values | mean 8.0 (range 4–34) |

U frame-mean range 127.98–128.01; V frame-mean range 128.12–128.18. Both are
essentially constant frame-to-frame.

The Y frame-mean trajectory is a slow, **non-monotonic drift** (16.2 up to 31.1,
then back to ~21.2; 56 decreasing steps of 299; Pearson r vs frame index −0.43).
This is the dominant temporal structure in the stream.

---

## 4. Temporal variation

### 4.1 Frame-to-frame differences (consecutive frames)

| Metric | Y | U | V |
|---|---|---|---|
| Mean absolute difference (LSB) | 0.140 | 0.0035 | 0.0061 |
| RMS difference | 0.546 | 0.105 | 0.126 |
| Std of differences | 0.546 | 0.105 | 0.126 |
| Fraction of samples changed | 9.47% | 0.23% | 0.41% |
| ±1 of all samples | 6.97% | 0.18% | 0.33% |
| >±1 of all samples | 2.51% | 0.04% | 0.08% |
| ±1 of changed samples | 73.5% | 80.4% | 79.5% |
| >±1 of changed samples | 26.5% | 19.6% | 20.5% |
| Max absolute change | 30 | 33 | 22 |
| Duplicate consecutive frames | **198 / 299 (66%)** | 198/299 | 198/299 |

### 4.2 Duplicate frames

* 198 of 299 transitions are exact duplicates (identical packed frames).
* Only **102 unique frames** exist out of 300.
* Duplicate runs: length 2 (9 runs), 3 (87 runs), 4 (5 runs) — each "new" frame
  is typically delivered 3 times before updating.

This is a driver/ISP buffering signature of the same class seen on the MSMF BGR
path, and it directly caps the number of genuinely new temporal observations.

### 4.3 Fixed-pixel temporal dependence (25-point deterministic grid)

Temporal autocorrelation of Y at fixed, well-separated grid coordinates
(mean over 25 points; 300 samples per point — still a small sample):

| Lag | Y mean | Y min–max |
|---|---|---|
| 1 | 0.974 | 0.889–0.987 |
| 2 | 0.947 | 0.777–0.974 |
| 3 | 0.920 | 0.720–0.963 |
| 4 | 0.901 | 0.696–0.957 |
| 8 | 0.816 | 0.565–0.860 |

U lag-1 0.497–0.946 (11/25 points constant over all 300 frames); V lag-1
0.497–0.992 (10/25 points constant). Y varies at every sampled point; U/V mostly
do not.

### 4.4 Held-out prediction (train 150 frames, test 150 frames)

| Channel | Constant-predictor MAE | Lag-1 predictor MAE | Lag-1 exact match |
|---|---|---|---|
| Y | 2.818 | 0.142 | 88.3% |
| U | 0.375 | 0.070 | 97.0% |
| V | 0.430 | 0.143 | 95.4% |

A trivial "copy the previous frame" predictor reconstructs most of the Y signal
exactly. Temporal predictability is high.

---

## 5. Spatial dependence

### 5.1 Adjacent-pixel correlation (pooled over 300 frames)

| Channel | Horizontal | Vertical | Diagonal |
|---|---|---|---|
| Y | 0.999 | 0.998 | 0.997 |
| U | 0.893 | 0.919 | 0.831 |
| V | 0.851 | 0.929 | 0.798 |

### 5.2 Correlation vs spatial separation

Y (pooled across frames): horizontal r 0.9998 (d=1) → 0.979 (d=1024); vertical r
0.999 (d=1) → 0.984 (d=512). Y stays near 0.98 even across the whole frame.

U decays: h 0.956 (d=1) → 0.056 (d=256). V decays: h 0.989 (d=1) → −0.024
(d=256); vertical persists ~0.25 out to d=512.

### 5.3 Interpretation of the Y spatial structure

Within a frame the Y image is nearly flat: mean per-frame spatial std ≈ 0.40,
first and mid frames each contain only ~6 distinct Y levels, and the temporal
mean image has spatial std 0.21 (fixed pattern is small). The very high Y spatial
correlations therefore reflect a near-constant, heavily smoothed/spatially
filtered Y plane, not many independent pixel values. Candidate causes consistent
with the data: upstream ISP/spatial smoothing, gain/illumination uniformity
under the cover, and common-mode electronics; the data cannot separate these.
Large spatial separation does **not** guarantee independence here.

---

## 6. Common-mode variation

The most decisive result concerns frame-wide behaviour:

| Metric | Y | U | V |
|---|---|---|---|
| Frame-mean std over time | 3.118 | 0.006 | 0.010 |
| Mean pixel temporal std | 3.137 | — | — |
| Distant-point off-diagonal mean \|r\| | 0.941 | 0.098 | 0.114 |
| Distant-point off-diagonal max \|r\| | 0.997 | 0.703 | 0.623 |
| Mean pixel-to-frame-mean correlation | 0.969 | 0.127 | 0.259 |
| Directional agreement among changed pixels | 0.590 | — | — |

For Y, the mean pixel temporal std (3.14) is essentially equal to the frame-mean
std (3.12). If pixels varied independently, the per-pixel temporal std would be
much larger than the frame-mean std (independent fluctuations average out across
the frame). Their near-equality means **almost all of the Y temporal variation is
frame-wide/common-mode**. Distant pixel time series correlate |r| ≈ 0.94 on
average (up to 0.997). Directional agreement on change transitions is only 0.59
(barely above chance), consistent with small, step-like, shared movements rather
than clean per-pixel noise.

This is direct evidence **against** treating distant Y pixels as independent
temporal entropy sources: the apparent temporal signal is largely a single
frame-wide drift (likely automatic gain/exposure settling plus buffered
duplication), not thousands of independent fluctuations.

---

## 7. Diagnostic bit analysis (LSBs)

Characterization only — no extraction, debiasing, conditioning, or security
claim is made.

| Metric | Y | U | V |
|---|---|---|---|
| Fraction of ones | 0.735 | 0.010 | 0.204 |
| Absolute bias from 0.5 | 0.235 | 0.490 | 0.296 |
| Runs count (observed / expected) | 6.0M / 107.7M | 0.57M / 2.86M | 7.0M / 44.9M |
| Runs z-score | −15,697 | −9,425 | −9,913 |
| Lag-1 bit autocorrelation | 0.803 | 0.905 | 0.989 |
| Adjacent LSB-pair equal fraction | 97.9% | 99.7% | 95.0% |

Every LSB stream is severely biased and massively over-correlated (runs z-scores
are thousands of standard deviations below expectation). The runs statistic's IID
assumption is grossly violated by the spatial/temporal structure, so the z-scores
are descriptive only; they still show the raw LSBs are nowhere near balanced or
independent.

---

## 8. Comparison with the existing BGR experiments

Reference points from `experiment_report_dark_001.md` and
`experiment_report_exposure_sweep.md`:

| Session | Format | Duplicates | Lag-1 ACF | Spatial r | Notes |
|---|---|---|---|---|---|
| dark-001 (MSMF, default) | BGR | 297/299 (99.3%) | 0.972 | 1.000 | 95% zeros, static after 15 frames |
| dark-exposure-004 (dshow, exp −4) | BGR | 0/299 | transient ramp | ~0.998 | mean 15.6 LSB |
| dark-exposure-005 (dshow, exp −3) | BGR | 0/299 | **0.015** | ~0.75 | 0.82 LSB/frame diff |
| **yuyv-dark-001 (this file)** | **YUYV** | **198/299 (66%)** | **0.974** | **Y 0.999** | common-mode drift |

Direct answers:

1. **More temporal variation?** Yes versus dark-001 (which is fully static after
   its startup transient), but the variation is a frame-wide drift delivered in
   duplicated frames. Versus the best BGR operating point (dark-exposure-005,
   lag-1 0.015), YUYV here is far worse on temporal independence.
2. **Reduced frame duplication?** No — 66% exact duplicates, similar to the
   buffering artifacts seen on the MSMF BGR path.
3. **Reduced spatial correlation?** No — Y adjacent correlation is 0.999,
   comparable to dark-001's 1.000 and much higher than dark-exposure-005's ~0.75.
4. **Cleaner temporal signal?** No — lag-1 ACF 0.974, a 16→31→21 drift, and an
   88%-exact lag-1 predictor.
5. **Reduced bias?** No — Y LSB is 73.5% ones (bias +0.235).
6. **Improved predictability?** No — the previous-frame copy predictor is highly
   effective.
7. **Genuinely meaningful or the same processed signal in another form?** The
   YUYV path is a genuinely different numeric representation (video-range black
   Y at floor 16 with 48 levels, no zero-clamping), which is why it is not
   trivially "the same" as dark-001. But its temporal behaviour — buffered
   duplication plus a common-mode gain/exposure drift — is the same class of
   ISP/driver artifact. The improvement is representational, not a better signal.
8. **Evidence YUYV is closer to sensor noise?** No. The near-constant U/V at 128,
   the video-range black mapping, the frame-wide drift, and the heavy duplication
   all indicate the ISP and driver remain fully in the loop.

Important confound: the FFmpeg capture's exposure/gain/white-balance settings are
not recorded in the file and are unknown (likely default/auto). The BGR sweep
showed that the exposure operating point dominates the observable behaviour, so a
direct "YUYV vs BGR" comparison at matched settings is not possible from this
file.

---

## 9. Interpretation

**Measured facts:** 300 frames @ 1280×720 YUYV 4:2:2; Y in 16–65 with 48 levels,
U/V ≈ 128; 198/299 duplicate transitions; 102 unique frames; Y frame-mean drift
16.2→31.1→21.2; Y adjacent spatial r 0.999; distant-pixel |r| 0.94; pixel
temporal std ≈ frame-mean std; LSB fractions of ones 0.735/0.010/0.204.

**Statistical observations:** lag-1 temporal ACF 0.974; lag-1 predictor 88% exact
match; runs z-scores of order −10⁴; per-frame Y spatial std ~0.4.

**Plausible interpretations (consistent with, not proven by, the data):** an
automatic gain/exposure control loop settling over the first seconds produced the
frame-wide drift; the DirectShow/FFmpeg or driver pipeline buffered each frame
2–4×; the ISP applied video-range mapping (black at 16) and spatial smoothing that
flattens the Y plane; chroma is essentially neutral because the scene is dark.

**Unsupported hypotheses:** any claim that these samples are random, independent,
entropy-bearing, cryptographically secure, or close to raw sensor measurements.
None of those are supported. In particular, the high common-mode correlation is
positive evidence *against* treating the 921,600 spatial Y sites as independent
sources, and the duplication caps the number of genuinely new temporal
observations at 102 frames.

---

## 10. Limitations

* Only **300 frames (~10 s)**; 300 temporal samples per pixel, of which only 102
  are unique frames. Insufficient for strong statistical conclusions about
  entropy.
* **No RAW/Bayer access**; DirectShow exposes only YUYV/NV12. The ISP remains
  upstream of the observed stream.
* **Unknown capture settings** (exposure/gain/white balance) for the FFmpeg run;
  this confounds comparison with the BGR exposure sweep.
* No proof of entropy, no cryptographic validation, and no extractor applied
  (out of scope for this experiment).

---

## 11. Conclusion

The YUYV acquisition path is verified and the covered camera **does** produce
measurable temporal variation at the YUYV level — unlike the fully static default
BGR baseline. However, that variation is dominated by a frame-wide common-mode
drift, delivered through heavy frame duplication, with near-perfect spatial
smoothness, negligible chroma variation, and strongly biased, serially dependent
LSBs. There is no evidence that the YUYV representation is closer to physical
sensor noise than the existing BGR path, and it is materially worse on temporal
independence than the best previously tested BGR operating point
(dark-exposure-005). The observation remains a single, settings-unknown capture.

---

## 12. Final verdict

### A. Acquisition
Yes. Direct YUYV 4:2:2 acquisition is verified: 552,960,000 bytes = 300 frames @
1280×720, packing confirmed by byte-position statistics. **Note the file does not
match the stated 30-frame / 55,296,000-byte spec (it is exactly 10× larger).**

### B. Signal
Yes — measurable temporal variation in Y (frame-mean drift 16.2→31.1→21.2 LSB;
9.5% of Y samples change per transition; 101/299 transitions change the frame
mean). U and V are effectively constant.

### C. Dependence
Strong temporal and spatial dependence. Temporal: lag-1 ACF 0.974, 66% exact
duplicate frames (102 unique frames), lag-1 predictor 88% exact. Spatial: Y
adjacent r 0.999, ≈0.98 even at 1024-pixel separation. Common-mode: pixel
temporal std ≈ frame-mean std and distant-pixel |r| ≈ 0.94, i.e., the temporal
variation is essentially frame-wide.

### D. BGR comparison
No material improvement. YUYV is a different numeric representation (video-range
black, no zero-clamping) but exhibits the same class of ISP/driver artifacts and
is worse than the best BGR operating point on every independence and bias metric
measured. No evidence it is closer to sensor noise.

### E. Entropy-source suitability
`PROCEED WITH CAUTION`

The acquisition layer is functional, verified, and the covered camera is not a
static zero source at the YUYV level, so the path is worth one more controlled
look. But the current evidence is dominated by processing artifacts (common-mode
drift, 66% duplication, spatial flatness, biased LSBs), and the decisive
settings variable (exposure) is unknown for this capture. Proceeding is justified
only for a controlled follow-up capture; there is no basis yet for treating this
stream as an entropy source, and certainly none for RNG construction.

### F. Single next experiment
A controlled DirectShow YUYV capture (FFmpeg rawvideo YUY2, 1280×720 @ 30 fps,
covered) at the previously promising elevated manual exposure (~index −3 to −4,
matching dark-exposure-005), with the exposure/gain/white-balance settings
recorded, run for ≥ 3,000 frames (~100 s), then re-run this same YUYV analysis
and compare duplicate rate, lag-1 ACF, common-mode correlation, and LSB bias
against this file. (Not implemented in this task.)

### G. Files changed
* `src/camera_noise/analysis/yuyv.py` — new reusable YUYV decoder/analysis module.
* `tests/test_yuyv.py` — new focused tests (13 tests).
* `experiments/yuyv-dark-001/yuyv_analysis.json` — full machine-readable results.
* `experiment_report_yuyv_dark_001.md` — this report.
