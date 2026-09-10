# PROJECT DOCUMENTATION — `noise maker`

Before you read the following stuff- tldr;
- The main purpose of building / trying this project was to achieve absolute randomness using real physical noise which i beleive was true randomness which can be used for simulators and shi for simulating real life hardware.
- I would be trying to build this using different methods.
- this was built using my laptop sensor, which i was not able to properly access the noisy part of the camera sensors.
- I will be trying more stuff for this in the coming future using different types sensors and the noise.
- ggz 


**Single-source documentation, architecture reference, experiment history,
scientific rationale, failure analysis, post-mortem, and repository guide.**

Project name: `noise maker`
Python package: `camera-noise-collector` (`camera_noise`)
Version: 0.2.0
Platform under test: Windows 11, Lenovo Legion laptop, `Integrated Camera`
Status: **Closed as a documented feasibility investigation.**

---

## 1. Why this project is being thrown off the cliff

This project is being **stopped on purpose**. It is not being abandoned because
the code failed to compile or the tests failed. The code worked, the experiments
ran, the storage was robust, and the reports are reproducible. The decision to
stop is a **scientific judgment**, and it is documented here so that the reason
does not need to be re-derived later.

The original goal was ambitious and reasonable on its face:

> Build a **generally usable camera-based hardware entropy source** by sampling
> the physical noise of a covered webcam sensor, extracting random bits, and
> exposing those bits as a Python package consumable by Qiskit.

That goal was **not achieved**, and the project is being closed because the
accessible data cannot honestly support it. Specifically:

1. **No RAW/Bayer access.** The Lenovo Legion integrated webcam exposes
   `YUYV 4:2:2` and `NV12` through Windows DirectShow, and no other format.
   DirectShow does not expose RAW, Bayer, or any high-bit-depth sensor format.
   Everything software received was already processed by the camera.
2. **The ISP was always upstream.** Both the BGR path (OpenCV) and the direct
   YUYV path (FFmpeg rawvideo) deliver values produced after the image-signal
   processor, firmware, black-level correction, denoising, and driver buffering
   stages. "Uncompressed" did not mean "unprocessed."
3. **Dark-frame output was heavily processed.** The default dark baseline was
   aggressively black-level clamped (95% exact zeros in one baseline session),
   and the remaining signal was dominated by fixed-pattern structure,
   common-mode drift, automatic gain/exposure behavior, and frame duplication.
4. **Direct YUYV access did not solve the entropy problem.** YUYV carried more
   visible temporal variation than the static default BGR baseline, but that
   variation was overwhelmingly a frame-wide common-mode drift delivered in
   duplicated frames, with near-perfect spatial correlation and heavily biased
   LSBs. It was a different numeric representation of the same processed signal.
5. **Some settings produced more variation, but only by making the system
   hardware-specific.** Elevating exposure on DirectShow produced changing
   frames with lower lag-1 autocorrelation, yet the behavior then depended on
   one laptop, one webcam, one backend, and a manually selected exposure index.
   That is the opposite of a general-purpose source.
6. **The observed data contained strong temporal dependence, spatial
   dependence, and common-mode structure.** Millions of pixels do not yield
   millions of independent samples when the ISP has correlated them, and
   frame-to-frame changes were largely predictable.
7. **The project therefore could not be promoted into a general-purpose
   hardware RNG.** Doing so would have required either stronger evidence of
   independence and entropy, or different hardware that exposes lower-level
   sensor data.
8. **Continuing to build a polished RNG package on top of this signal would
   have meant engineering around camera artifacts.** A well-packaged library
   would not have converted ISP-processed, duplicated, common-mode video into
   genuine physical entropy. Hashing cannot create entropy; extraction cannot
   recover entropy that is not in the accessible data.

The decision is therefore to **stop at the research/proof-of-concept stage**
and to preserve the repository as a documented, defensible negative result. The
experiments, the analysis infrastructure, and the Qiskit demonstration are
real; the physical entropy source that the original idea required was not
demonstrated on this hardware.

The rest of this document explains the reasoning in detail.

---

## 2. The original idea, reconstructed step by step

The conceptual pipeline that motivated the project was:

```
Camera sensor
  → dark frame
  → physical sensor noise
  → noise extraction
  → random bits
  → statistical validation
  → reusable Python RNG package
  → Qiskit integration
```

Each step in that chain was plausible, and it is worth recording why it seemed
reasonable before describing why it failed.

### 2.1 Why a dark camera sensor can physically contain noise

A CMOS image sensor is an array of photodiodes. During an exposure, charge
accumulates in each pixel well, is converted to a voltage, and is digitized.
Even with the lens covered, charge and voltage fluctuate. The physical noise
sources are real:

- **Dark current.** Heat generates electron-hole pairs in the semiconductor
  even with no light. The accumulated unwanted charge is called the dark
  signal; its statistical fluctuation is dark-current shot noise. Dark current
  grows with exposure time and temperature.
- **Read noise.** Measuring and digitizing the charge in a pixel is imperfect:
  resetting the pixel, charge-to-voltage conversion, column amplifiers, analog
  routing, and the analog-to-digital converter all add noise that is present
  even in total darkness.
- **Electronic noise.** Amplifiers and readout electronics contribute
  Johnson–Nyquist (thermal) noise and low-frequency flicker (1/f) noise.
- **ADC effects.** The analog-to-digital converter rounds a continuous voltage
  to an integer, adding quantization error, and can have its own noise and
  non-idealities.
- **Photon shot noise.** With a truly opaque cover, photon shot noise from the
  scene is nearly zero, but light leakage (including infrared that is invisible
  to the eye) can still contribute Poisson-distributed counts.

These mechanisms are collectively the reason a covered sensor is not a flat
constant: there is a physical, time-varying dark-noise mixture in the silicon.

### 2.2 Why random physical variation can be an entropy source

If a measurement channel carries genuinely unpredictable physical variation,
an observer who knows all past samples and all public context cannot predict the
next sample exactly. That unpredictability is what an entropy source provides.
Read noise and thermal fluctuations are classically random (not purely
quantum), but unpredictability does not require quantum mechanics: any
high-dimensional, poorly observable physical process can carry information-theoretic
entropy. This is why a camera sensor is, in principle, a legitimate candidate.

### 2.3 Why a camera is attractive as a cheap entropy source

A laptop webcam is already installed, costs nothing extra, is standardized, and
can produce enormous numbers of samples (millions of pixels at tens of frames
per second). The apparent sample count is seductively large. If even a small
fraction of those samples carried fresh entropy, the aggregate rate could be
very high. The original idea was: *we already own a sensor array; why not use
it?*

### 2.4 Why repeatedly capturing dark frames seemed plausible

Covering the lens removes the dominant intended input (scene light). The
remaining variation should then be dominated by internal electronic/sensor
effects rather than moving objects or illumination. Repeating the capture gives
a time series of nominally identical "black" frames whose differences should
reflect the stochastic component.

### 2.5 Why taking pixel LSBs was initially tempting

The least significant bit of an 8-bit value is the natural first guess for
"noise bits": in an ideal noise-limited ADC with many quantization levels, the
lowest bits toggle pseudo-randomly. LSB extraction is trivial, fast, and
produces one bit per sample with no further processing. It was therefore the
first candidate extraction rule.

### 2.6 Why that turned out to be problematic

In this camera, the LSB was not determined by sensor noise. It was determined
by ISP quantization, black-level correction, clipping, and buffering. The
baseline BGR dataset produced 95% zeros; the YUYV LSB stream was 73.5% ones.
The LSB carried the structure of the processing pipeline, not fresh sensor
noise. A "noisy-looking" number can have a LSB that is nearly constant.

### 2.7 Why an image can look noisy without containing much entropy

Spatial variation is not temporal entropy. A single frame that contains many
different pixel values can be almost completely predictable frame-to-frame if
those values are a stable spatial pattern (fixed-pattern noise). Pooling all
pixels across all frames inflates histogram-based entropy estimates even when
the underlying signal is a static image plus a tiny, highly correlated
fluctuation.

### 2.8 Why spatial correlation matters

If adjacent pixels are correlated (because of demosaicing, denoising, shared
column amplifiers, or compression), then N pixels carry fewer than N
independent observations. The project measured adjacent-pixel correlations near
1.0 in several configurations, which directly reduces the number of effective
samples and forbids treating every pixel as an independent entropy source.

### 2.9 Why temporal correlation matters

Entropy sources must supply *fresh* unpredictability. If the next frame is
mostly the previous frame (measured here as 66–99% exact duplicate transitions
and lag-1 autocorrelations above 0.97 in the worst cases), then repeated frames
add almost no new information. Predictability of future values from past values
is the direct opposite of what an entropy source should provide.

### 2.10 Why fixed-pattern noise matters

A stable map of pixel offsets or hot pixels varies *spatially* but is
*constant in time*. Reading a fixed pattern repeatedly yields the same
"noise-looking" values with almost no new entropy. The project explicitly
separated the persistent spatial component from the time-varying residual for
this reason.

### 2.11 Why ISP processing matters

The image signal processor sits between the sensor and the software. It can
destroy genuine noise (denoising, temporal filtering, black-level clamping,
clipping) or manufacture apparent randomness (sharpening, tone curves,
automatic gain). The project could only observe the ISP's output; the sensor's
own statistics were masked by it.

### 2.12 Why black-level clamping matters

Cameras estimate and subtract a black level so that small negative-going
electronic fluctuations are representable. After subtraction, values below zero
are clipped. When the dark baseline sits below the clip threshold, the output
collapses to a flat zero — which is exactly what the BGR baseline showed (95%
zeros, 100% spatial uniformity). Clamping destroys the low end of the noise
distribution where the interesting physics lives.

### 2.13 Why RAW/Bayer data would have been much more desirable

RAW/Bayer samples are (ideally) the digitized sensor values before demosaicing,
tone mapping, denoising, and most ISP stages. They would expose the per-color-filter
photodiode measurements with a direct, approximately linear relationship to the
physical signal, vastly improving the chance of observing genuine read/dark
noise. The tested DirectShow interface never exposed RAW/Bayer; the project
therefore never had access to the data its core idea depended on.

### 2.14 Why direct YUYV was investigated

Because OpenCV's BGR path added an unknown conversion layer, a natural next
step was to request the camera's native uncompressed video. FFmpeg/DirectShow
successfully opened the device as rawvideo YUY2 (YUYV 4:2:2) at 1280x720 and
30 fps, bypassing OpenCV's decode. This was investigated to see whether the
uncompressed representation carried a cleaner, less processed signal.

### 2.15 Why YUYV still does not mean RAW sensor data

YUYV is an uncompressed *video representation*. It is produced after the sensor
is read out, demosaiced (implicitly), color-processed, and mapped to a video
range (the observed luma sat on the video-black floor of 16 with chroma locked
near 128). "Uncompressed" describes the transport, not the processing history.
The capability report states this explicitly: a valid path is still
`sensor -> ADC -> ISP/firmware -> YUV -> USB -> software`.

---

## 3. The experimental progression (chronological story)

```
Original idea
    ↓
Build camera acquisition layer (OpenCV BGR, DirectShow/MSMF backends)
    ↓
Collect dark frames  (dark-001, MSMF, default settings, 300 frames)
    ↓
Notice black-level clamping  (95% zeros, flat field, 99.3% duplicate frames)
    ↓
Investigate exposure/backend behavior  (exposure/gain sweep, dshow vs msmf)
    ↓
Discover DirectShow performs better than MSMF in the tested setup
    (0 host timing gaps vs 79; 0% duplicate frames at elevated exposure)
    ↓
Analyze spatial and temporal structure  (lag-1 ACF, spatial correlation, FPN)
    ↓
Try multiple bit extraction methods  (LSB, median threshold, temporal sign/parity, Von Neumann)
    ↓
Observe severe bias / correlation  (biased LSBs, strong dependence, no security)
    ↓
Investigate direct YUYV  (FFmpeg rawvideo YUY2 capture, 300 frames)
    ↓
Successfully capture YUYV  (test_yuyv.yuv, 552,960,000 bytes)
    ↓
Analyze Y/U/V directly  (no BGR conversion; Y/U/V separated)
    ↓
Observe strong common-mode + temporal/spatial dependence
    (frame-wide drift, 66% duplicate frames, lag-1 ~0.97, spatial r ~0.999)
    ↓
Compare with BGR  (no material entropy improvement)
    ↓
Conclude consumer webcam path is not suitable as a general RNG
    ↓
Stop rather than over-engineer around configuration-specific behavior
```

Each stage motivated the next:

- **Acquisition → dark frames.** You cannot study camera noise without first
  being able to record frames, so a reliable capture layer was built first.
- **Dark frames → clamping discovery.** The first dark session was so flat
  that the diagnosis (ISP black clamping) was unambiguous.
- **Clamping → exposure/backend sweep.** If the default operating point is
  clamped, the obvious experiment is to move the operating point (exposure)
  and to change the acquisition backend (MSMF vs DirectShow).
- **Sweep → bit extraction.** Once changing frames existed, the next question
  was whether bits could be extracted from them, motivating several candidate
  extraction methods and their statistical evaluation.
- **Extraction failure → direct YUYV.** Since the BGR path is clearly
  post-processed, the uncompressed native representation was the natural next
  candidate for a "cleaner" signal.
- **YUYV → conclusion.** The YUYV analysis showed the same class of artifacts
  (common-mode drift, duplication, spatial smoothness), which closed the loop:
  the problem is in the accessible camera output itself, not in one particular
  decoding path.

---

## 4. Architecture and implementation documentation

The package is structured as a pipeline:

```
capture (collector + backends + storage)
    → session on disk (frames.npy, timestamps.npy, metadata.json)
    → capability discovery (capabilities.py)  [side investigation]
    → analysis (analysis/engine.py)           [signal characterization]
    → entropy (analysis/entropy.py)           [bit extraction + diagnostics]
    → quantum (quantum.py)                    [Qiskit demonstration]
```

Each module is documented below: why it exists, what responsibility it carries,
how it fits, the important classes/functions, key implementation decisions,
their assumptions, what worked, the limitations, and whether it is reusable.

---

### 4.1 `pyproject.toml`

**Why it exists.** Declares the package, build backend, dependencies, optional
groups, console entry point, and pytest configuration.

**Key decisions.**

- `requires-python = ">=3.10"`, with `numpy>=1.24,<2.3` (the code was exercised
  on NumPy 2.2.6 / Python 3.12.7).
- Dependencies: `numpy`, `opencv-python`, `matplotlib`, `scipy`. Qiskit is
  deliberately an *optional* group (`quantum`) so the heavy quantum stack does
  not leak into ordinary capture/analysis installs.
- `[project.scripts] camera-noise = "camera_noise.cli:main"` exposes the CLI.
- `[tool.pytest.ini_options] testpaths = ["tests"]`, `addopts = "-q"`.

**Assumptions.** OpenCV is available on the target platform and exposes the
backends used (`DSHOW`, `MSMF`, `V4L2`).

**What worked.** Clean optional dependency separation; `pip install -e ".[all]"`
installs everything including Qiskit.

**Limitations.** Pins nothing for Qiskit itself beyond major versions; relies on
the venv.

**Reusable.** Yes — as the packaging skeleton for any future camera or noise
collection tool.

---

### 4.2 `src/camera_noise/__init__.py`

**Why it exists.** Public surface of the package.

**Contents.** Exports `CaptureConfig`, `CaptureResult`, `collect`, and defines
`__version__ = "0.2.0"`.

**Decision.** Only the capture path is exposed at top level; analysis, entropy,
capabilities, and quantum live in submodules and are imported lazily inside the
CLI to keep startup cheap.

**Reusable.** Yes.

---

### 4.3 `src/camera_noise/models.py`

**Why it exists.** Central data definitions shared by collector, backends,
analysis, and CLI.

**Important classes.**

- `CameraDevice` — numeric index, name, backend, negotiated width/height/fps/fourcc.
- `SettingReport` — name, requested, before/after readback, setter return value,
  status, detail. This is the provenance unit for *"we asked the driver for X and
  it reported Y."*
- `CapturedFrame` — one image plus four timestamps (monotonic start/end,
  UTC end, source timestamp) and source frame position.
- `CaptureConfig` — validated capture parameters (camera id, output dir, frame
  count, resolution, fps, exposure, gain, white balance, auto-control switches,
  warmup, dark-frame flag, backend, notes).
- `SessionInfo` — device + setting reports + warnings + property snapshot.

**Key decisions.**

- Setting *requests* and driver *readbacks* are kept separate (`before`/`after`
  on every `SettingReport`). This encodes the reality that a webcam driver often
  ignores a requested value; the report records both sides.
- `CaptureConfig.validate()` enforces coherent input before touching hardware.

**Assumptions.** The driver readback is a meaningful statement about what is
actually applied. (Caveat: readback can be a lie on some drivers; the code
labels ambiguous cases as `unverifiable`.)

**Reusable.** Yes — models are hardware-agnostic.

---

### 4.4 `src/camera_noise/backends/base.py`

**Why it exists.** Abstraction so future acquisition backends (raw sensor
libraries, industrial cameras) can implement the same API.

**Important classes.**

- `CameraSession` (ABC) — `read() -> CapturedFrame | None`, `close()`,
  `snapshot_properties()`, and context-manager support (`__enter__`/`__exit__`).
- `CameraBackend` (ABC) — `discover(max_devices)` and `open(config) -> CameraSession`.

**Decision.** `read()` returns `None` on a failed read rather than raising; the
collector counts consecutive failures and decides whether to abort. This models
real webcam behavior where a transient read failure is recoverable.

**Reusable.** Yes — this is the intended extension point for better hardware.

---

### 4.5 `src/camera_noise/backends/opencv.py`

**Why it exists.** The concrete OpenCV implementation of the backend contract.

**Important functions/classes.**

- `backend_code(name)` — maps `"any" / "dshow" / "msmf" / "v4l2"` to OpenCV
  constants with `getattr` fallbacks so the code survives builds lacking a backend.
- `_finite` / `_close` / `_fourcc` — safe numeric/property helpers; `_fourcc`
  decodes the integer FOURCC property into printable text.
- `OpenCVSession` — wraps a `cv2.VideoCapture`, configures it, and reports what
  happened.
- `OpenCVSession._configure()` — the settings negotiation logic:
  - `_set_numeric(name, prop, requested, tolerance)` records `before`, attempts
    the set, records `after`, and classifies the result as `accepted`,
    `unverifiable`, `rejected`, or `unsupported` by comparing readback to the
    requested value within a tolerance.
  - `_disable_auto_exposure()` — attempts **backend-specific** values
    (`0.25` for DirectShow, `1.0` for V4L2, `0.0/0.25/1.0` for MSMF) because
    auto-exposure semantics differ per backend. A matching readback is evidence
    of acceptance, not proof that all internal control stopped.
  - Requests to disable auto-white-balance set `CAP_PROP_AUTO_WB = 0.0`.
  - When gain is not requested, a warning is recorded that OpenCV has no
    portable auto-gain switch — the code refuses to silently assume gain is fixed.
- `OpenCVSession.read()` — measures `capture_start_monotonic_ns` and
  `capture_end_monotonic_ns` around `capture.read()` with `time.perf_counter_ns()`,
  records `time.time_ns()` as UTC end, and reads `CAP_PROP_POS_MSEC` and
  `CAP_PROP_POS_FRAMES` as source-provided timing.
- `OpenCVSession._properties()` — a snapshot of width/height/fps/exposure/gain/
  auto-exposure/AWB/WB-temperature/fourcc/backend-name.
- `OpenCVBackend.discover()` — scans indices 0..N-1, opening and reading one
  frame to confirm the camera actually delivers data before listing it.
- `OpenCVBackend.open()` — opens the requested index and raises if not opened.

**Key decisions and reasoning.**

- **Monotonic nanosecond timestamps** (`time.perf_counter_ns`) are used for
  frame intervals because `perf_counter` is guaranteed monotonic and immune to
  wall-clock adjustments; the median interval between frames is the basis for
  dropped-frame estimation. `time.time_ns()` is captured separately for wall-clock
  provenance. Mixing them would corrupt interval statistics, so they are kept in
  distinct fields.
- **Requested-versus-actual verification** is the core scientific habit: the
  collector never claims a camera is running at a requested exposure/resolution/
  fps; it records what the driver read back and how close that is to the request.

**What worked.** DirectShow vs MSMF behavior was cleanly distinguishable; the
setting reports made the exposure sweep auditable.

**Limitations.** OpenCV exposes no native min/max/step/default for controls and
no native media-type list; some drivers report values the hardware does not
apply. Also, OpenCV typically converts YUV to BGR in the default path, which is
precisely why the YUYV investigation used FFmpeg instead.

**Reusable.** Yes — the backend abstraction is the natural place to plug in
RAW-capable acquisition later.

---

### 4.6 `src/camera_noise/storage.py`

**Why it exists.** Robust, lossless, crash-safe persistence of capture sessions.

**Important functions/classes.**

- `TIMESTAMP_DTYPE` — a structured numpy dtype:
  `frame_index (i8), capture_start_monotonic_ns (i8), capture_end_monotonic_ns (i8),
  capture_end_utc_ns (i8), source_timestamp_ms (f8), source_frame_position (f8)`.
- `utc_now_iso()` — timezone-aware UTC ISO timestamp for metadata.
- `write_json_atomic(path, data)` — writes to a `.tmp` sibling, `flush`s,
  `os.fsync`s, then `os.replace`s onto the final path. This guarantees readers
  never observe a half-written metadata file.
- `SessionStore` — owns two memory-mapped arrays:
  - `frames.npy` allocated up front as `open_memmap(..., mode="w+",
    shape=(frame_count, *image.shape))`.
  - `timestamps.npy` allocated the same way with `TIMESTAMP_DTYPE`.
  - `append(frame)` validates that every incoming frame matches the shape and
    dtype of the first frame (raises if the camera changes layout mid-session),
    writes both arrays, and advances `valid_count`.
  - `flush()` flushes both memmaps so the data is durable before the collector
    finishes.

**Key decisions and reasoning.**

- **Why memory mapping.** Sessions are large (300 frames of 1280x720x3 uint8 is
  829 MB). Writing incrementally into a pre-allocated memmap avoids holding the
  entire capture in RAM and avoids re-allocating as frames arrive. It also makes
  the on-disk file identical to the numpy format that analysis later reads with
  `np.load(..., mmap_mode="r")`.
- **Why atomic metadata writes.** Metadata is the provenance record; a torn
  write would silently corrupt the scientific trace. `fsync` + atomic rename
  make the write durable and atomic.
- **Why fixed allocation.** The collector knows the requested frame count up
  front; a full-allocation memmap means `valid_count` alone tells readers how
  many entries are real, and partial captures (interruptions) are still valid
  sessions.

**What worked.** Partial captures were retained correctly; analysis opened
sessions read-only via memmap and never modified the source (tests assert this
with SHA-256 digests before/after analysis).

**Limitations.** Allocation size is fixed at start; a session that grows beyond
its pre-allocation is not supported. `create()` uses `exist_ok=False`, so
re-running into the same directory fails rather than overwriting.

**Reusable.** Yes — this is the most reusable engineering component of the
project.

---

### 4.7 `src/camera_noise/collector.py`

**Why it exists.** Orchestrates one capture session end to end.

**Important functions.**

- `_drop_report(timestamps, fps, read_failures)` — computes two classes of
  dropped-frame evidence:
  - **Source frame-position gaps:** if the backend exposes an increasing
    `source_frame_position`, count deltas > 1.
  - **Host timing gaps:** with the readback fps, a frame interval beyond
    `1.5 × period` is a candidate missed interval.
  It explicitly notes that host timing gaps can also be scheduler delays, so
  they are evidence, not proof.
- `collect(config, backend=None)` — the main loop:
  1. Validate config, create store, write initializing metadata.
  2. Open the backend session, discard `warmup_frames`.
  3. Loop: `session.read()`; on `None`, record a read-failure event and abort
     if consecutive failures exceed `max_consecutive_read_failures`; otherwise
     `store.append(sample)`.
  4. On `KeyboardInterrupt` or exception, record `error`, but still `flush()`
     the store so the partial session is usable.
  5. Snapshot camera properties after warmup and at end; build the full metadata
     JSON and write it atomically.

**Key decisions and reasoning.**

- **Warmup frames** are discarded because Windows webcams are known to produce
  transient, non-representative frames immediately after opening.
- **Partial captures are first-class.** Interrupting a capture must not destroy
  the frames already stored; the session is marked `status: partial` with a
  warning and a valid frame count.
- **Provenance over trust.** The metadata records the requested configuration,
  each setting report, warnings, read-failure events, dropped-frame estimates,
  and the dark-frame declaration with the explicit caveat that software cannot
  verify that the lens was actually covered.
- **Dark-frame declaration.** `--dark` records that the operator declared the
  camera covered; the software never claims it verified opacity.

**What worked.** All seven sweep sessions captured cleanly; DirectShow sessions
reported 0 host timing gaps versus 79 for the MSMF baseline.

**Limitations.** Cannot verify the physical cover, cannot observe hardware
frame counts that the driver does not expose, and cannot distinguish scheduler
delays from true frame loss in host timing.

**Reusable.** Yes — a solid capture orchestration core.

---

### 4.8 `src/camera_noise/capabilities.py`

**Why it exists.** Answer the question *"what is the least-processed thing this
camera will actually give us, and which controls actually work?"* before
running noise experiments. This is a capability/format audit, deliberately
separated from entropy analysis.

**Important functions and logic.**

- Default probe grids: `DEFAULT_RESOLUTIONS`, `DEFAULT_FRAME_RATES = (15, 30)`,
  `DEFAULT_PIXEL_FORMATS = ("MJPG", "YUY2", "NV12")`.
- `classify_pixel_format(fourcc)` — a conservative table maps FOURCC names to
  `compressed` / `uncompressed` / `unknown`, plus a `_RAW_NAMES` set of
  Bayer-like names (BA81, RGGB, BG10/12/16, etc.). The interpretation field
  stresses that a RAW-like name is only evidence if the format is honored and
  the delivered representation is consistent with it; uncompressed transport
  does **not** establish ISP bypass.
- `_open_capture` / `_read_frame` — retry loops with recorded attempts and
  timing; OpenCV camera opens are known to fail transiently, and the retry
  records the attempts as evidence.
- `_request_mode` — requests width/height/fps/fourcc, reads back the actuals,
  and classifies each dimension as `honored` / `mismatch` / `unverifiable`
  (fps ≤ 0 is unverifiable rather than a mismatch). This catches *silent
  fallback* where the driver ignores the requested mode.
- `_probe_controls` — for each control in `_CONTROL_SPECS`, reads the current
  value, attempts small changes, checks the readback moved, and restores the
  original. Because OpenCV exposes no native min/max/step/default, those are
  recorded as `None` and explicitly not inferred.
- `_validate_mode` — re-opens a discovered mode, disables RGB conversion
  (`CAP_PROP_CONVERT_RGB=0`), captures a tiny validation stack, checks
  consistent shape/dtype, counts consecutive duplicate pairs, and saves a
  `.npy` sample with an exact round-trip check. This yields direct evidence of
  frame duplication in a negotiated mode.
- `_windows_hardware_info` — reads the Windows registry (`winreg`) to enumerate
  USB camera devices, driver INF/version/date/provider, and the OS camera
  privacy consent. This maps the opaque OpenCV numeric index to a named PnP
  device (e.g., `Integrated Camera`, `USB\VID_04F2&PID_B828&MI_00`, Realtek
  driver `10.0.22000.20384`, `oem47.inf`).
- `_rank_modes` / `_mode_score` — a transparent heuristic scoring (RAW/Bayer
  name +100, uncompressed +40, compressed −20, bit depth, verified exposure/gain
  changes, no-duplicate validation) to recommend the least-processed mode.
- `_conclusion` — classification categories:
  - **A:** RAW/Bayer appears accessible.
  - **B:** uncompressed but still processed video is accessible.
  - **C:** only heavily processed/compressed video is accessible.
  - **D:** insufficient information.
- `discover_capabilities` — orchestrates the audit, writes
  `camera_capabilities.json` and `camera_capabilities.md`, and appends
  historical evidence from past sessions.
- `render_markdown` — produces the human-readable report with the key caution
  rendered in every relevant section.

**Key decisions and reasoning.**

- **The audit is not an entropy analysis.** Its report explicitly says so.
  The purpose is to establish, with evidence, what representation the camera
  exposes *before* deciding whether a noise experiment is even meaningful.
- **"Uncompressed does NOT automatically mean raw sensor data"** is stated
  verbatim in the rendered report (and asserted in a test), because this is the
  single most important interpretive boundary in the project.

**What worked.** The audit produced the definitive negative result for this
camera: the highest accessible component bit depth was null, and no RAW/Bayer
mode was ever negotiated; category B (uncompressed-but-processed) was the
best-case classification.

**Limitations.** The probe grid is finite; OpenCV hides native media-type
advertisement; vendor-specific or undocumented RAW paths were not testable.
On the final audit run the camera could not be opened at all through either
backend (Windows had detected the device but OpenCV opens failed), illustrating
how environment-dependent the interface is.

**Reusable.** Yes — this module is the right tool to vet any future camera
before committing to a noise experiment.

---

### 4.9 `src/camera_noise/analysis/models.py` and `analysis/session.py`

**Why they exist.** Typed analysis configuration (ROI, channel, lag, windows,
entropy parameters) and read-only loading of stored sessions.

**Important logic.**

- `ROI` — validated rectangle; `ROI.validate(frame_width, frame_height)`.
- `AnalysisConfig` / `EntropyConfig` — validated settings; `EntropyConfig`
  converts to `AnalysisConfig` for shared loading.
- `LoadedSession.project(frame_index)` — crops the ROI and applies the channel
  projection:
  - `b/g/r` selects an integer channel directly (enables LSB/parity methods).
  - `mean` averages the three channels.
  - `luma` computes BT.601 luma `0.114·B + 0.587·G + 0.299·R` in float64 with no
    rounding.
  - `intensity` is used for stored single-channel frames.
- `LoadedSession.elapsed_seconds()` — prefers genuine monotonic intervals
  (host monotonic ns converted to seconds) when all intervals are positive and
  finite; falls back to fps readback; falls back to frame index. This keeps the
  frequency axis of spectral analysis honest.
- `load_session()` — memory-maps `frames.npy` (`mmap_mode="r"`), validates
  `valid_frame_count`, ROI bounds, and pixel coordinates, and derives numeric
  min/max from the dtype for clipping-fraction reporting.

**Key decisions.** Analysis is strictly read-only; the source files are never
written (tests assert byte-for-byte equality of `frames.npy` after analysis).
Channel projections are explicit so that "what exactly was analyzed" is always
recorded.

**Reusable.** Yes.

---

### 4.10 `src/camera_noise/analysis/math_utils.py`

**Why it exists.** Shared numeric primitives used by engine and entropy.

**Important functions.**

- `pearson(x, y)` — Pearson correlation with NaN handling for zero-variance
  inputs (returns `None`).
- `autocorrelation(series, max_lag)` — lag-based Pearson of a time series;
  `rho[0] = 1`.
- `histogram_percentiles(...)` — percentile estimation from histograms, exact
  for integer levels.
- `json_safe(value)` — recursively converts numpy arrays/generics and
  non-finite floats into plain JSON-safe Python values.
- `PairAccumulator` — incremental sum-based accumulator (n, Σx, Σy, Σx², Σy²,
  Σxy) enabling **streaming/pooled Pearson correlations** across arbitrarily
  large arrays without holding all pairs in memory. This is how the engine pools
  adjacent-pixel correlations over hundreds of millions of pairs.

**Reusable.** Yes — a small, dependency-free statistics toolkit.

---

### 4.11 `src/camera_noise/analysis/engine.py`

**Why it exists.** Full signal characterization of a captured session: the
"analysis" stage that produces `analysis_report.json`, `derived_metrics.npz`,
and 8 diagnostic plots.

**Important logic (key regions).**

- `_grid_points` — deterministic approximately-uniform grid of sample points
  (used for pixel time series and correlation sampling) so results are
  reproducible across runs.
- Main loop over frames computing: ROI mean/median/min/max, frame spatial std,
  clipping fractions, per-pixel mean/variance maps (Welford's online algorithm
  via `delta = frame - mean_map; mean_map += delta/(i+1); m2_map += delta*(frame-mean_map)`
  — numerically stable without storing all frames), mean-absolute-difference
  map, pixel traces, frame-difference statistics (mean abs, RMS, max abs, zero
  fraction), and adjacent-frame Pearson correlation.
- `_detect_steps` — heuristic detection of frame-median step changes (exposure
  / gain transients) using scaled median-absolute-deviation thresholds plus a
  windowed before/after median contrast. Heuristic only, explicitly labelled.
- `_block_boundary_ratio` — checks whether 8-pixel-boundary mean differences are
  elevated, a compression/block artifact probe.
- `_reported_property_changes` — compares property snapshots across the session
  to detect automatic-control drift.
- `_spectrum` — linear detrend, Hann window, one-sided real FFT of the ROI mean,
  reporting dominant peaks, sample rate, and timing-jitter fraction.
- `_interpret` — produces structured findings that carefully attribute evidence
  to overlapping categories (sensor/electronic noise, fixed pattern, drift,
  environmental contamination, processing artifacts, temporal correlation) with
  explicit cautions that attribution categories overlap and that no separation
  is claimed.

**Outputs.** JSON report with descriptive statistics, histogram, frame
differences, spatial structure (raw / fixed-pattern-removed / local-residual
adjacent correlations), temporal structure (adjacent-frame correlation, ACFs),
spectral analysis, artifact detection, stability windows; `derived_metrics.npz`
with mean/std/diff maps, traces, ACFs, spectra; 8 PNG plots.

**Key decisions.** The report never assumes a Gaussian distribution or
statistical randomness. Values are projected, not rescaled; no denoising or
outlier rejection is applied. Percentiles are exact for integer data.

**Reusable.** Yes — this is the project's main analytical asset.

---

### 4.12 `src/camera_noise/analysis/entropy.py`

**Why it exists.** To ask the central question *"does the measured camera noise
contain useful unpredictability?"* and to compare extraction methods honestly.

#### 4.12.1 What "entropy" means in this project

Entropy is treated as a *quantified property of observed data under an explicit
model and quantizer*, not as a physical guarantee. The module reports Shannon
entropy, min-entropy, and confidence-bounded estimates, and keeps five claim
levels distinct (see section 7 of `camera_sensor_noise.md`):

1. Looks random — no obvious defect found by limited diagnostics.
2. Passes selected tests — named tests did not reject reference models.
3. Contains measurable entropy — outcomes have a quantified spread.
4. Is unpredictable — not established by these tests.
5. Is cryptographically secure — requires a validated source, health tests,
   conditioning, and a threat model; never established here.

#### 4.12.2 Data loading

- `_load_traces` — selects a deterministic grid of ROI pixels and reads their
  per-frame values (capped at `max_samples`) to bound memory. The selection is
  explicit: *distribution estimates describe that subset*, and a different ROI
  or interval can differ.

#### 4.12.3 Continuous/amplitude metrics

- `_symbolize` — quantizes float values. For integer channels with a small
  range, uses exact integer levels; otherwise equal-width bins. Quantization is
  always recorded because entropy changes with quantizer resolution.
- `_continuous_metrics` computes:
  - plugin Shannon entropy `H = −Σ pᵢ log₂ pᵢ`;
  - plugin min-entropy `H∞ = −log₂ max pᵢ`;
  - **Most Common Value (MCV)** estimate with a one-sided **Wilson score upper
    bound** at the configured confidence, yielding a conservative
    min-entropy lower bound (`H∞ ≥ −log₂ p_upper`). This is *inspired by*
    SP 800-90B's MCV estimator, and the report explicitly says it is not a
    conforming EntropyAssessment run;
  - per-pixel temporal MCV lower bounds (each pixel's time series separately),
    which avoids crediting pooled spatial structure as fresh entropy;
  - collisions (IID collision probability from observed frequencies vs observed
    adjacent-frame-same-pixel match fraction) to expose memory/quantization;
  - temporal autocorrelation by lag;
  - `_conditional_symbol_prediction` — a first-order Markov table trained on the
    first 60% of the time axis and evaluated on the remainder, reporting held-out
    accuracy vs the marginal baseline, the accuracy excess, and an empirical
    first-order conditional Shannon entropy;
  - distribution tests: uniform χ², first-half vs second-half stationarity χ²
    (contingency), and D'Agostino–Pearson normality (reference model only).

#### 4.12.4 Bit-level metrics

- `_runs` — Wald–Wolfowitz runs statistic (normal approximation, two-sided via
  `erfc`) conditional on observed zero/one counts.
- `_lag_predictor` — for each lag, selects on the training prefix whether to
  *copy* or *invert* the bit lag away, then evaluates on the held-out suffix;
  reports best accuracy, Wilson upper bound, and a min-entropy lower bound.
  Multiple-comparison optimism is disclosed.
- `_bit_metrics` combines:
  - exact binomial monobit p-value;
  - block-frequency test (block size 128);
  - runs test;
  - overlapping pair serial test (2×2 χ²; labelled diagnostic because pairs
    overlap);
  - lag autocorrelations;
  - MCV and held-out-lag-prediction min-entropy bounds, with the conservative
    minimum taken;
  - `claim_levels`: `looks_random` requires all selected tests to pass, |ACF| at
    every tested lag within `max(0.02, 3/√n)`, prediction excess ≤ tolerance,
    and conservative entropy ≥ 0.8 bits/bit.

#### 4.12.5 Extraction methods

- `per_pixel_median_threshold` — trains per-pixel median on the first half,
  emits second-half comparisons `x > median`. Time-separated training avoids
  leakage but drift can bias it and ties emit zero.
- `temporal_difference_sign` — emits 1 if a pixel increased from the previous
  frame, else 0. Ties emit zero.
- `temporal_difference_parity` (integer channels) — parity of |Δ|.
- `direct_value_lsb` (integer channels) — the LSB of each observed integer.
- `_von_neumann` — pairs `01 → 0`, `10 → 1`, discard `00/11`.

**Why Von Neumann was attempted and why it is not enough.** Von Neumann
removes a *constant independent* bias between the two symbols by pairing: for
independent bits with P(01)=P(10)=p(1−p), both unequal pairs are equally likely,
so emitting the first bit of an unequal pair is unbiased. But it requires the
input pairs to be independent and identically biased. Camera bits fail this:
they are temporally and spatially dependent (e.g., 00/11 dominance from
duplicated frames), so the pairs are not independent. Von Neumann can lower
bias but cannot create entropy or remove arbitrary dependence, and it discards
most of the input (typically >85% here).

**Why held-out prediction.** In-sample statistics are optimistic. Training a
predictor on the first 60% and evaluating on the remaining 40% measures how much
of the stream is actually predictable to a simple, honest adversary; high
accuracy or a strong predictor lower bound directly contradicts "unpredictable."

**Why statistical tests are not proof of security.** Tests check specific null
models at finite sample sizes; a deterministic generator can pass them, and a
physical source can have entropy while leaking predictable state. The module's
own `claim_levels` encode this hierarchy in its JSON output.

#### 4.12.6 Ranking, bitstream export, external tools

- `_rank_methods` — ranks by conservative entropy per output bit, bias, and
  yield; recommends a passing method if one exists, otherwise the top yield
  method. The reason string states it is a *comparison heuristic*, not a
  certified entropy rate.
- `_write_bitstreams` — writes packed `.bin` (MSB-first) and one-bit-per-byte
  `.symbols.bin` for external suites.
- `_run_external_tools` — optional integration with NIST `ea_non_iid`,
  PractRand `RNG_test`, and Dieharder, with per-tool minimum-size guidance and
  the caveat that a pass is retained verbatim and is not treated as proof of
  unpredictability.
- `analyze_entropy` — orchestrates, writes `entropy_report.json` + `.md`.

**Reusable.** Yes — this module is the project's methodological core and is
independent of the specific camera.

---

### 4.13 `src/camera_noise/analysis/plots.py`

**Why it exists.** Matplotlib (Agg backend) rendering of 8 diagnostic figures:
ROI selection overlay, time series, frame differences, distribution, spatial
maps (mean/std/mean-abs-diff), correlations (adjacent-frame + ACFs), spectrum,
and windowed stability. Purely diagnostic; no claims attached.

**Reusable.** Yes, with minor adaptation.

---

### 4.14 `src/camera_noise/analysis/yuyv.py` (added during the YUYV experiment)

**Why it exists.** To analyze the direct YUYV capture without converting to BGR,
keeping the investigation isolated from the RNG-oriented pipeline.

**Important functions.**

- `verify_yuyv_file(path, width, height, frame_count)` — verifies existence,
  even width, and exact byte size `width·height·2·frame_count`; raises on
  mismatch rather than silently reinterpreting the file.
- `load_packed_yuyv` — `np.fromfile` reshaped to `(frames, height, width·2)`.
- `reshape_packed` — to `(frames, height, width, 2)`.
- `extract_channels` — splits into Y `(F,H,W)`, U `(F,H,W/2)`, V `(F,H,W/2)`
  using the packing `[Y0 U0 Y1 V0]`: even chroma slots are U, odd are V.
- `channel_summary` — min/max/mean/std, unique levels, zero/255 fractions,
  histogram top levels.
- `frame_statistics` — per-frame mean/variance/min/max and their frame-to-frame
  changes.
- `temporal_differences` — mean-abs/RMS/std of consecutive-frame differences,
  changed fraction, ±1 fractions, >±1 fractions, duplicate-frame indices.
- `grid_coordinates` / `pixel_traces` — deterministic grid of pixel time series.
- `pixel_autocorrelation` — per-point and mean ACF (reuses `math_utils.autocorrelation`).
- `held_out_predictor` — constant-vs-lag1 copy predictor on the held-out second
  half.
- `adjacent_correlations` / `pooled_spatial_correlations` — adjacent (h/v/diag)
  and correlation-vs-distance tables using `PairAccumulator`.
- `common_mode_analysis` — frame-mean series, distant-pixel correlation matrix,
  mean pixel-to-frame-mean correlation, and directional sign agreement.
- `bit_diagnostics` — LSB fraction/bias, runs z-score, lag-1 bit ACF.
- `analyze_yuyv_file` — orchestrates everything into one JSON result with
  explicit scientific cautions; `write_yuyv_results` serializes it.

**Key decisions.** Decodes straight from the packed bytes (no BGR). Reports
sampling relationships explicitly (U/V are per-macropixel, 2× sub-sampled, not
per-pixel samples). Refuses to call YUYV raw. Built as a standalone module so
the experiment stayed isolated from any production RNG functionality.

**What worked.** Produced the full quantitative YUYV post-mortem; the 13
accompanying tests all pass.

**Reusable.** Yes — directly reusable for any future YUYV stream analysis.

---

### 4.15 `src/camera_noise/quantum.py`

**Why it exists.** To demonstrate the *downstream* idea: feed camera-derived
bits into Qiskit to generate parameterized quantum circuits. This is the
"Qiskit integration" proof-of-concept.

**Important classes/functions.**

- `NoiseSource` — a stateful bit reader over an extracted bitstream:
  - `from_symbol_file`, `from_packed_file`, `from_entropy_report` (reads the
    entropy report's recommended candidate automatically).
  - `take_bits(n)`, `take_int(n_bits)` (MSB-first integer), `take_float(n_bits,
    low, high)` (linear map of the integer to a range), `take_angle(n_bits)`
    (map to `[0, 2π)`).
  - Tracks `consumed` / `remaining` and raises on exhaustion so the pipeline
    never silently reuses bits.
- Circuit strategies:
  - `_build_random_angles` — fixed `Ry` layers + alternating CNOT entangling;
    camera noise sets only the rotation angles (provenance records every angle).
  - `_build_random_structure` — noise selects both the single-qubit gate type
    (3 bits over 8 gate types) and the angles, plus which adjacent pair is
    entangled.
  - `_build_random_walk` — noise selects gates, angles, and arbitrary
    control/target pairs.
- `_apply_gate` — maps a gate index to `rx/ry/rz/h/x/z/s/t`.
- `_bits_per_circuit` — an exact bit budget per strategy used to refuse
  generation when the source is too short.
- `generate_circuits` — validates parameters, consumes bits, produces
  `GeneratedCircuit` objects carrying QASM3 text and full provenance.
- `simulate_circuits` — runs circuits on Qiskit Aer `AerSimulator` and returns
  measurement counts.
- `run_quantum_pipeline` — end-to-end: load entropy → generate → optionally
  simulate → write QASM files and a `quantum_report.json` with explicit
  caveats.

**What is and is not demonstrated.**

- *Demonstrated:* camera-derived bits can be consumed programmatically, mapped
  to integers/floats/angles, used to parameterize circuits, exported as QASM3,
  and executed on Aer. Different noise inputs produce different circuits; the
  same input reproduces the same circuits deterministically (tested).
- *Not demonstrated:* this is **not** implementing a Qiskit physical
  `NoiseModel`. A `NoiseModel` describes quantum error rates/correlations of a
  device; here the camera bits parameterize *application-level circuit
  structure and rotation angles*. And because the source bits are not a proven
  entropy source, the circuits are simply *driven by processed webcam output*,
  not by quantum randomness. The report file states this verbatim.

**Reusable.** The `NoiseSource` abstraction and circuit generators are reusable
for any bit source; only the bit-file import path is coupled to the entropy
pipeline's output format.

---

### 4.16 `src/camera_noise/cli.py` and `__main__.py`

**Why they exist.** A single `camera-noise` console command exposing:
`list`, `capabilities`, `capture`, `analyze`, `entropy`, `quantum`.

**Important details.**

- `capture` interactive camera selection (`_select_camera`), the dark-cover
  confirmation prompt (skippable with `--yes`), default output naming
  (`experiments/{timestamp}_{dark|camera}{id}`), and JSON result output.
- `analyze`/`entropy`/`quantum` forward CLI arguments into the corresponding
  `AnalysisConfig`/`EntropyConfig`/pipeline and print machine-readable JSON.
- `capabilities` prints a short JSON summary plus paths to the full
  markdown/JSON reports.
- `_parse_pixels`, `_parse_resolutions` — strict CLI parsing with clear errors.

**Decisions.** Everything prints JSON so scripts can consume results; humans
get the markdown reports. Errors exit non-zero with `SystemExit(1/2)`.

**Reusable.** Yes.

---

### 4.17 `src/camera_noise/analysis/__init__.py`

Exports the analysis API: `analyze_session`, `analyze_entropy`,
`AnalysisConfig`, `EntropyConfig`, `ROI`. The YUYV module is intentionally kept
out of this public surface because it is an experiment-specific tool, not part
of the session pipeline.

---

## 5. Mathematics of the analysis

### 5.1 Sensor observation model

The project used (documented in `camera_sensor_noise.md`):

```
D_{i,t} = offset_i + light_{i,t} + dark_signal_{i,t} + read/electronic_noise_{i,t}
```

followed by gain, black-level correction, denoising, color processing,
compression, and clipping before the observed value. The term `offset_i`
absorbs per-pixel fixed-pattern structure; `dark_signal_{i,t}` includes the
temperature/exposure-dependent dark component; the last term is the
time-varying stochastic component the project hoped to exploit.

### 5.2 Temporal difference

```
Δ_{i,t} = D_{i,t} − D_{i,t−1}
```

Differencing removes static offsets and fixed patterns. But it does not create
entropy: if the original samples are correlated, the differences have their own
dependence structure (e.g., a step sequence becomes mostly zero). The project
therefore always reports differences *and* their autocorrelation rather than
treating differences as fresh bits.

### 5.3 Variance and standard deviation

Population variance over N samples:
```
σ² = (1/N) Σ (x_j − μ)²
```
Per-frame variance quantifies within-frame spatial spread; per-pixel temporal
variance quantifies change. The YUYV analysis found the mean pixel temporal std
(≈3.14) nearly equal to the frame-mean std (≈3.12), which is the quantitative
signature of common-mode dominance (independent pixel noise would make pixel
temporal std much larger, because independent fluctuations average out across
the frame).

### 5.4 Autocorrelation

```
ρ(k) = corr(x_t, x_{t+k})
```
Lag-1 autocorrelation was the project's primary temporal-independence metric
(BGR default 0.972; BGR dshow exp −3: 0.015; YUYV: ≈0.974). Values near 1 mean
consecutive samples are nearly copies of each other.

### 5.5 Bias

Bit bias is `p₁ − 0.5`. Raw LSBs were severely biased (BGR baseline −0.477; YUYV
Y +0.235). Bias alone already contradicts a uniform bit source.

### 5.6 Shannon entropy

```
H = −Σ pᵢ log₂ pᵢ
```
Average surprise under a stationary marginal model. Pooled amplitudes overstate
fresh entropy because stable spatial offsets contribute a rich histogram with no
temporal freshness.

### 5.7 Min-entropy

```
H∞ = −log₂(max pᵢ)
```
The negative log of the most-likely-outcome probability — the entropy relevant
to the best single guess. For extraction, min-entropy is the conservative
quantity.

### 5.8 MCV with a Wilson bound

For a most common value observed c times in n trials, the one-sided upper
confidence bound is the Wilson score upper limit `p_upper`; the conservative
estimate is `H∞ ≥ −log₂(p_upper)`. This guards against finite-sample optimism
under representative sampling (but not against nonstationarity or dependence).

### 5.9 Predictor accuracy

A predictor trained on the first part and evaluated on the remainder measures
empirical predictability. `direct_value_lsb` on the BGR baseline was predicted at
99.33% accuracy; the YUYV lag-1 copy predictor matched 88.3% of Y samples
exactly. High accuracy directly contradicts unpredictability.

### 5.10 Spatial correlation

Pearson correlation between pixel values at a separation d, pooled across
frames with `PairAccumulator`. BGR baseline: 1.000 at all separations (flat
field). YUYV Y: 0.999 adjacent, still ≈0.98 at 1024 px. High spatial correlation
means pixels are not independent samples.

### 5.11 Common-mode decomposition

```
D_{i,t} = frame_mean_t + residual_{i,t}
```
When `residual` is small compared to `frame_mean_t` variation and distant-pixel
residuals covary strongly, the temporal signal is a frame-wide shift. The YUYV
analysis found distant-pixel |r| ≈ 0.94 and pixel-to-frame-mean correlation
0.969 — most temporal variation is common mode.

### 5.12 Von Neumann extraction

Pair `(b₁,b₂)`: emit `b₁` if `01` or `10`, else discard. For IID bits with
P(1)=p, P(01)=P(10)=p(1−p), so emitted bits are unbiased. Requires IID pairs;
fails on correlated camera bits.

### 5.13 Why differencing can introduce dependence

If `X_t` has a persistent level, `Δ_t = X_t − X_{t−1}` is approximately white
only when `X` is white; for a step-like or drifting `X`, `Δ` is mostly zero
with spikes. `Δ_t` and `Δ_{t+1}` are dependent by construction (they share `X_t`).
The project measured this rather than assuming differencing "cleans" the signal.

### 5.14 Why statistical tests are not security proofs

A p-value is computed under a specific null model at a finite sample size.
Passing NIST SP 800-22/PractRand/Dieharder, or the built-in monobit/block/runs/
serial tests, demonstrates that no *tested* defect was found; it does not
establish a physical entropy source, does not bound an informed predictor, and
does not satisfy cryptographic validation requirements.

---

## 6. Experimental results

All numbers below are taken directly from the repository artifacts
(`experiment_report_dark_001.md`, `experiment_report_exposure_sweep.md`,
`experiment_report_yuyv_dark_001.md`, `experiments/*/analysis/*.json`,
`experiments/*/entropy/*.json`, `experiments/yuyv-dark-001/yuyv_analysis.json`).

### 6.1 BGR baseline: dark-001 (MSMF, default settings)

Capture: OpenCV/MSMF, 1280x720 BGR, 300 frames, camera covered.

| Metric | Value |
|---|---|
| Values present | {0, 2, 3} — three levels |
| Zero fraction | 95.000% |
| Mean / std | 0.1233 / 0.5490 |
| Dynamic range | 3 levels |
| Consecutive duplicate frames | 297 / 299 (99.3%) |
| Spatial correlation (h/v/diag) | 1.000000 (flat field, zero spatial variance) |
| Lag-1 autocorrelation | 0.9723 |
| Shannon entropy (plugin) | 0.1598 bits/symbol |
| Min-entropy (plugin MCV) | 0.0341 bits/symbol |
| Conditional Shannon (lag-1) | 0.0011 bits/symbol |
| Best extraction (`direct_value_lsb`) | H∞ 0.0092 bits/bit, all tests failed |

Interpretation: after a 15-frame startup transient (3 → 2 → 0), the camera
produced a perfectly flat black field for the remaining 285 frames. Zero usable
entropy.

### 6.2 Exposure/backend sweep (BGR, DirectShow vs MSMF)

Seven sessions, 300 frames each, covered lens. Gain was unsupported on both
backends (readback −1.0). DirectShow accepted exposure in [−12, −3]; MSMF locked
exposure at −6.

| Session | Backend | Exposure | Mean | Std | Zero % | Dup frames | Lag-1 ACF | Spatial r (h/v) |
|---|---|---|---|---|---|---|---|---|
| dark-exposure-001 | dshow | −8.0 | 0.0000 | 0.0000 | 100% | 299/299 | — | 1.00 / 1.00 |
| dark-exposure-002 | dshow | −6.0 | 0.0000 | 0.0000 | 100% | 299/299 | — | 1.00 / 1.00 |
| dark-exposure-003 | dshow | −5.0 | 0.0000 | 0.0008 | 99.999% | 287/299 | 0.700 | 0.70 / 0.70 |
| dark-exposure-004 | dshow | −4.0 | 15.6207 | 16.8979 | 52.07% | 0/299 | 0.984 (transient) | 0.998 / 0.997 |
| dark-exposure-005 | dshow | −3.0 | 0.1584 | 0.5621 | 89.97% | 0/299 | **0.0151** | 0.747 / 0.693 |
| dark-exposure-006 | msmf | req −3 / act −6 | 0.1552 | 0.4811 | 88.59% | 220/299 (73.6%) | 0.735 | 0.751 / 0.699 |

Findings:

- Below exposure −5, the ISP zero-gates everything.
- At −4.0 the signal broke through the clamp (mean 15.6, values to 105) but was a
  **transient ramp** (lag-1 0.984) and heavily spatially correlated.
- At −3.0 the signal was nearly temporally independent (lag-1 0.0151) with **0**
  duplicate frames, but was 89.97% zero, had FPN spatial std 0.3257 vs temporal
  FPN/temporal ratio 2.54, and adjacent spatial r ≈ 0.75.
- DirectShow delivered 0 host timing gaps vs 79 for MSMF, and 0% duplicate
  frames at elevated exposure vs 73.6% for MSMF at the same request — DirectShow
  was clearly the better backend in this setup.
- Entropy results: `dark-exposure-004` VN-LSB reached H∞ 0.9685 bits/bit and
  passed the built-in tests, but on a nonstationary transient. `dark-exposure-005`
  VN-LSB H∞ 0.8276 (bias +0.0007, lag-1 corr 0.0921). Raw LSBs remained strongly
  biased everywhere (e.g., dark-001 bias −0.4767).

Interpretation: the exposure sweep proved the camera *could* produce changing
frames, but only at a manually selected operating point on DirectShow, and with
a mixture of fixed-pattern and temporal structure that still was not independent
per-pixel entropy.

### 6.3 Direct YUYV capture (test_yuyv.yuv)

FFmpeg/DirectShow opened `rawvideo (YUY2 / 0x32595559), yuyv422, 1280x720,
30 fps`. The file is:

- **Actual size: 552,960,000 bytes.**
- Byte-count analysis: `552,960,000 / (1280 × 720 × 2) = 300 frames`, not the 30
  frames stated in the experiment brief (which would be 55,296,000 bytes). The
  analysis reported this mismatch explicitly rather than silently reinterpreting
  the file. The byte-position mod-4 statistics confirmed the YUYV 4:2:2 packing
  (`[Y0 U0 Y1 V0]`), and the file is not a 30-frame sequence repeated 10 times.

YUYV byte structure (positions mod 4, first frames): Y bytes ≈16–21
(video-black floor), U ≈127.99, V ≈128.12 — the signature of an ISP-processed,
video-range YUYV stream.

| Metric | Y (300×720×1280) | U (300×720×640) | V (300×720×640) |
|---|---|---|---|
| min / max | 16 / 65 | 114 / 161 | 124 / 151 |
| mean | 22.4708 | 127.9893 | 128.1284 |
| std | 3.1454 | 0.1769 | 0.4667 |
| unique levels | 48 | 44 | 26 |
| zero / 255 fraction | 0 / 0 | 0 / 0 | 0 / 0 |
| mean-abs frame diff | 0.140 | 0.0035 | 0.0061 |
| RMS diff | 0.546 | 0.105 | 0.126 |
| fraction of samples changed | 9.47% | 0.23% | 0.41% |
| ±1 of changed | 73.5% | 80.4% | 79.5% |
| duplicate transitions | **198 / 299 (66%)** | 198/299 | 198/299 |

Temporal structure:

- Only **102 unique frames** in 300; duplicate runs of length 2 (9), 3 (87), 4
  (5) — each new frame is typically delivered ~3 times.
- Y frame-mean trajectory: 16.19 → up to 31.14 → back to ~21.2 (non-monotonic;
  56 decreasing steps; Pearson r vs frame index −0.43) — a slow
  gain/exposure/thermal drift.
- Per-pixel lag-1 ACF (25-point grid): Y mean 0.974 (0.889–0.987); lag-2 0.947,
  lag-8 0.816. U/V: lag-1 0.50–0.95 with 10–11 of 25 points constant.
- Held-out predictor (train 150, test 150): Y constant-MAE 2.818 vs lag-1 MAE
  0.142, **88.3% exact match**; U 97.0%, V 95.4%.

Spatial structure:

- Y adjacent: h 0.999, v 0.998, diag 0.997; still ≈0.98 at 1024 px separation.
- U/V adjacent 0.85–0.93, decaying at larger distances (U h 0.056 at 256 px;
  V h −0.024 at 256 px).
- Within-frame Y is nearly flat: mean per-frame spatial std ≈0.40, ~6 distinct
  levels per frame, fixed-pattern spatial std 0.21.

Common mode:

- Distant-point off-diagonal mean |r| **0.941** (max 0.997); mean
  pixel-to-frame-mean correlation **0.969**.
- Mean pixel temporal std 3.137 ≈ frame-mean std 3.118 ⇒ **essentially all
  temporal variation is frame-wide (common-mode)**.
- Directional agreement among changed pixels on frame-mean-change transitions:
  0.59 (barely above chance).

Bit diagnostics (LSBs, characterization only):

| Metric | Y | U | V |
|---|---|---|---|
| fraction of ones | 0.735 | 0.010 | 0.204 |
| bias from 0.5 | +0.235 | −0.490 | −0.296 |
| runs z-score | −15,697 | −9,425 | −9,913 |
| lag-1 bit ACF | 0.803 | 0.905 | 0.989 |
| adjacent LSB pair equal | 97.9% | 99.7% | 95.0% |

### 6.4 YUYV vs BGR comparison (direct answers)

1. **More temporal variation?** Yes vs the static default BGR baseline (dark-001),
   but the YUYV variation is a frame-wide drift delivered in duplicated frames.
   Vs the best BGR operating point (dark-exposure-005, lag-1 0.015), YUYV is far
   worse on temporal independence.
2. **Reduced frame duplication?** No — 66% duplicates (vs 99.3% MSMF baseline,
   vs 0% on dshow elevated exposure).
3. **Reduced spatial correlation?** No — Y adjacent 0.999 (vs 0.75 at the best
   BGR point).
4. **Cleaner temporal signal?** No — lag-1 0.974, a drift, and an 88%-exact
   predictor.
5. **Reduced bias?** No — Y LSB 73.5% ones.
6. **Improved predictability?** No — the previous-frame copy predictor is highly
   effective.
7. **Meaningful improvement or same processed signal?** YUYV is a different
   numeric representation (video-range black, no zero-clamping) but the same
   class of ISP/driver artifacts. The improvement is representational, not
   signal-quality.
8. **Closer to sensor noise?** No evidence. Near-constant U/V, video-range
   black mapping, frame-wide drift, and duplication all indicate the ISP and
   driver remain in the loop.

---

## 7. Failure analysis

The project failed at the level of the *physical entropy source*, not the
software. The failure decomposes into several distinct layers.

### 7.1 Hardware/interface failure

The camera exposed only YUYV 4:2:2 and NV12 through DirectShow. No RAW, Bayer,
or high-bit-depth format existed on the interface. RAW/Bayer would have given
approximately linear, pre-demosaic sensor readouts where read noise and dark
current are observable. Without it, the project could never see the quantity its
core idea required. This is a hardware/interface property of consumer webcams:
they are designed to deliver processed pictures, not measurement data.

### 7.2 ISP failure

The ISP can both destroy and create apparent randomness. It destroyed genuine
noise through denoising, temporal filtering, black-level correction, and
clipping (the 95%-zero baseline). It created apparent randomness through
sharpening, tone curves, automatic gain, and quantization — fluctuations that
look "noisy" but follow processing rules rather than physical stochasticity.
Because the project observed only ISP output, it could not separate these.

### 7.3 Quantization failure

Genuine analog sensor noise may have existed below the accessible digital
resolution. The baseline sat at the clamp floor (0 in BGR, 16 in YUYV video
range). Even when values varied, they moved in ±1 steps (73–80% of changes),
indicating the observable amplitude was at the quantization limit. Analog noise
that would justify an entropy claim was effectively invisible at 8-bit,
ISP-processed resolution.

### 7.4 Spatial dependence

Millions of pixels did not imply millions of independent samples. Adjacent-pixel
correlation was 0.999 (YUYV Y) to 1.000 (flat BGR fields), and correlation
persisted near 0.98 across the whole frame. The spatial DOF were far smaller
than the pixel count; pooling spatial samples inflated naive entropy estimates
without adding information.

### 7.5 Temporal dependence

Frame-to-frame variation was largely predictable. Duplicate-frame rates ranged
from 66% (YUYV) to 99.3% (BGR baseline), lag-1 autocorrelations reached 0.97+,
and simple copy predictors matched 88–99% of samples exactly. A source whose
future output is mostly its past output is not an entropy source.

### 7.6 Common-mode behavior

The YUYV analysis showed that essentially all temporal variation was a
frame-wide shift (pixel temporal std ≈ frame-mean std; distant-pixel correlation
0.94). This is the signature of a shared gain/exposure/drift process, not
thousands of independent pixel-level fluctuations. It is positive evidence that
the spatial dimension carries almost no independent temporal entropy in this
capture.

### 7.7 Fixed-pattern noise

Spatial structure that repeats across frames (offsets, hot pixels, column
patterns) looks like noise in a still but is constant in time. The exposure
sweep's FPN/temporal ratio of 2.54 showed fixed structure dominating temporal
variation. Re-reading a fixed pattern yields no fresh entropy.

### 7.8 Extraction failure

- **LSB extraction** failed because the ISP/clamp/quantizer determines the LSB
  (95% zeros in BGR; 73.5% ones in YUYV Y).
- **Temporal differencing** removes static offsets but does not create
  independence; differences inherit the step/duplicate structure of the source,
  and successive differences share an underlying sample (built-in dependence).
- **Von Neumann** debiasing assumes independent, identically biased pairs. It
  reduced bias on some streams (e.g., H∞ 0.9685 on the transient exposure-004)
  but cannot create entropy, cannot remove arbitrary dependence, and discarded
  most input. Passing a subset of built-in tests on one nonstationary session
  was never evidence of a general-purpose source.

### 7.9 Generalization failure

Even the "best" configurations required: one specific laptop, one specific
webcam (Realtek `Integrated Camera`, USB VID_04F2 PID_B828), one specific
backend (DirectShow), a manually selected exposure index (−4 or −3), and
favorable driver behavior that was not even reproducible later (the final
capability audit could not open the camera at all through either backend). A
general-purpose RNG package cannot rest on that. Any further progress would
have meant engineering around camera artifacts rather than around physics.

---

## 8. What was successfully accomplished

The final objective was not achieved, but the project built a complete, honest,
reusable research pipeline. Concretely:

- **Camera acquisition was implemented** and made robust: multiple backends
  (DirectShow, MSMF, V4L2 mapping), retries, warmup, partial-capture retention.
- **Multiple backends were investigated** and their behavioral differences
  measured (DirectShow vs MSMF frame delivery, timing gaps, duplication).
- **Camera settings were probed and recorded** with requested-versus-actual
  readbacks and a full control probe (exposure, gain, white balance, etc.).
- **Capability/format discovery was implemented**, producing the definitive
  statement that no RAW/Bayer mode was accessible (category B at best).
- **Dark-frame experiments were executed** across an exposure sweep and a
  backend comparison, with auditable metadata for every session.
- **Storage was made robust**: memory-mapped lossless arrays, atomic fsynced
  metadata, structured timestamps, read-only analysis.
- **Analysis infrastructure was built**: descriptive statistics, spatial/temporal
  structure, fixed-pattern separation, common-mode checks, spectral analysis,
  artifact detection, and 8 diagnostic plots per session.
- **Several entropy extraction methods were implemented** (LSB, per-pixel
  median threshold, temporal sign, temporal parity, Von Neumann variants) with
  transparent bias/ACF/prediction reporting.
- **Statistical diagnostics were implemented**: Shannon/min-entropy, MCV with
  Wilson bounds, monobit/block/runs/serial tests, held-out prediction, and
  integration points for NIST SP 800-90B, PractRand, and Dieharder.
- **Direct YUYV capture was successfully demonstrated** (FFmpeg/DirectShow
  rawvideo YUY2) and its byte layout verified.
- **YUYV decoding and analysis were implemented** (separate Y/U/V extraction,
  temporal/spatial/common-mode/bit diagnostics) with focused tests.
- **The data was connected to Qiskit**: camera-derived bitstreams parameterize
  QASM3 circuits, which run on Aer.
- **Random circuits could be generated** from camera-derived bits with three
  strategies and full provenance.
- **Automated tests existed and passed**: 58 tests, all green.

These are meaningful scientific accomplishments: the project produced a
*defensible negative result* for this hardware, a methodology that can be
re-applied, and a demonstration that the software side of the original pipeline
could be built. What was missing was on the hardware side, and no amount of
software could supply it.

---

## 9. Testing and code quality

### 9.1 Test inventory (58 tests, all passing at the time of the audit)

| File | Tests | Covers |
|---|---|---|
| `tests/test_collector.py` | 4 | capture end-to-end with a fake backend; metadata provenance; partial capture retention; drop-report gap logic; frame/dtype layout change rejection |
| `tests/test_capabilities.py` | 5 | conservative format classification; negotiation/validation/reporting with a fake capture; CLI parsing; transient-open retry; hardware query ordering after camera probing |
| `tests/test_analysis.py` | 2 | analysis end-to-end on synthetic sessions (finds injected duplicate frames, exposure steps, spectral peaks, drift); ROI-bounds rejection; source immutability |
| `tests/test_entropy.py` | 4 | Von Neumann pair rule; constant bits get zero entropy and `looks_random=False`; extraction-method comparison on synthetic data; constant-session handling |
| `tests/test_quantum.py` | 30 | `NoiseSource` (empty rejection, consumption tracking, int/float/angle conversion, exhaustion, file loaders, entropy-report loading); three circuit strategies; QASM determinism; bit-budget accounting; parameter validation; Aer simulation; end-to-end pipeline; CLI parsing |
| `tests/test_yuyv.py` | 13 | YUYV byte-count validation; reshape; Y/U/V extraction; duplicate-frame detection; temporal-difference math; channel statistics; grid sampling; autocorrelation; held-out predictor; LSB diagnostics |

Tests are deterministic (seeded RNGs, synthetic arrays) and use fake capture
objects, so the suite runs without a camera.

### 9.2 What tests verify vs what remains scientifically unverified

The tests verify **software correctness**: byte counts, reshaping, extraction
rules, statistical math, provenance persistence, determinism of circuit
generation, and that constant inputs are not credited with entropy.

They do **not** verify the physical source. Passing tests prove the pipeline
faithfully processes whatever data it is given; they say nothing about whether
the camera data contains physical entropy. The distinction is fundamental:

```
software correctness  ≠  physical entropy-source validity
```

The entropy module's own outputs enforce this vocabulary (`looks_random`,
`passes selected tests`, `is_unpredictable: not established`, `is_cryptographically_secure:
not established`), and the reports never conflate them.

---

## 10. Why the project should stop here

Stopping is the correct engineering and scientific decision because every
avenue for *software* improvement is orthogonal to the actual deficiency:

- **Additional extraction algorithms cannot recover entropy that is absent from
  the accessible data.** Extraction is a deterministic function of the input; it
  can only preserve or discard the input's entropy. The accessible data's
  entropy problem is structural (dependence, duplication, common mode), not
  representational.
- **Cryptographic hashing cannot create entropy.** A cryptographic conditioner
  needs a source with at least the required min-entropy; it amplifies/compresses,
  it does not manufacture. Hashing duplicated, common-mode, ISP-shaped data
  yields hashed artifacts.
- **Package architecture cannot solve a hardware acquisition problem.** A
  polished API around this camera would make it easier to *use*, not more true.
- **More Qiskit integration does not validate the physical source.** Circuits
  can be generated from any bitstream; that was demonstrated. It demonstrates
  plumbing, not physics.
- **Making the camera work only under special conditions weakens the original
  goal.** A source requiring one laptop, one webcam, one backend, and a hand-set
  exposure is not the general-purpose camera RNG the project set out to build.
- **A more complicated pipeline could obscure the limitations.** Adding
  conditioners, health tests, and streaming would create the appearance of a
  product while the scientific basis remained absent.
- **The repository already contains enough evidence for a defensible negative
  result.** The reports quantify the clamping, duplication, spatial/temporal/
  common-mode dependence, and bias across BGR and YUYV paths. That is a complete
  and citable finding.

Stopping here preserves the result honestly instead of burying it under more
code.

---

## 11. What would be required to continue the idea (future work, not open tasks)

These are **new experimental directions**, explicitly not reasons to keep
extending the existing Legion webcam implementation:

- A camera that exposes **true RAW/Bayer samples** (e.g., an industrial
  machine-vision camera with documented raw output).
- A **direct sensor interface** or a **dedicated CMOS sensor board** (e.g., a
  bare sensor with a USB3/PCIe frame grabber and vendor SDK that bypasses
  webcam ISP firmware).
- A sensor with **higher ADC resolution** (12/14/16-bit) so analog noise is not
  below the quantization floor.
- A **controlled optical/dark enclosure** (verified opaque, IR-blocked) and
  **independent temperature monitoring** (read noise/dark current are strongly
  temperature-dependent; a laptop webcam cannot be thermally characterized).
- **Long-duration stability testing** (minutes to hours) and **large-scale
  entropy datasets** for meaningful statistics.
- **Formal entropy-source evaluation** (NIST SP 800-90B / EntropyAssessment
  conforming runs, restart testing) and **appropriate cryptographic
  conditioning** once a real min-entropy rate is established.
- **Online health testing** and a **streaming RNG API**.
- Only then, **Qiskit integration** as an actual validated randomness
  back-end.

If such hardware were acquired, the reusable pieces here — storage, analysis,
entropy methodology, YUYV tools, `NoiseSource`, and the Qiskit generator — could
be adapted. But that is a different project on different hardware.

---

## 12. Repository guide

### 12.1 Layout

```
noise maker/
├── pyproject.toml                    # package metadata, deps, CLI entry point, pytest config
├── README.md                         # user-facing command documentation
├── camera_sensor_noise.md            # scientific background: sensor noise, ISP, entropy claims
├── camera_capabilities.md/.json      # capability audit output (device, modes, controls)
├── experiment_report_dark_001.md     # BGR baseline (MSMF) experiment report
├── experiment_report_exposure_sweep.md  # exposure/backend sweep report (BGR)
├── experiment_report_yuyv_dark_001.md   # direct YUYV experiment report
├── PROJECT_DOCUMENTATION.md          # this document
├── test_yuyv.yuv                     # 300-frame 1280x720 YUYV capture (552,960,000 bytes)
├── src/camera_noise/
│   ├── __init__.py                   # public exports, version
│   ├── __main__.py                   # python -m camera_noise
│   ├── cli.py                        # camera-noise command (list/capabilities/capture/analyze/entropy/quantum)
│   ├── models.py                     # config/devices/setting/capture data types
│   ├── collector.py                  # capture orchestration
│   ├── storage.py                    # memmap + atomic JSON persistence
│   ├── capabilities.py               # capability/format audit
│   ├── quantum.py                    # NoiseSource + Qiskit circuit generation
│   ├── backends/
│   │   ├── base.py                   # CameraBackend / CameraSession abstractions
│   │   └── opencv.py                 # OpenCV implementation
│   └── analysis/
│       ├── __init__.py               # public analysis API
│       ├── models.py                 # ROI / AnalysisConfig / EntropyConfig
│       ├── session.py                # read-only session loading + channel projection
│       ├── math_utils.py             # pearson / autocorrelation / accumulators / json_safe
│       ├── engine.py                 # full signal analysis + report + plots
│       ├── entropy.py                # entropy estimates, extraction methods, tests
│       ├── plots.py                  # matplotlib diagnostics
│       └── yuyv.py                   # YUYV decode/analyze (experiment-specific)
├── tests/                            # 58 tests across 6 files
└── experiments/                      # captured sessions + analysis/entropy outputs
```

### 12.2 Install

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"        # capture + analysis
python -m pip install -e ".[quantum]"     # adds Qiskit/Aer
python -m pip install -e ".[all]"         # everything
```

### 12.3 Commands

```powershell
camera-noise list                                            # detect readable cameras
camera-noise capabilities --camera 0 --backend dshow --backend msmf
camera-noise capture --camera 0 --frames 300 --width 1280 --height 720
camera-noise capture --camera 0 --frames 1000 --dark --output experiments\dark-001
camera-noise analyze experiments\dark-001
camera-noise analyze experiments\dark-001 --roi 100 80 320 240 --channel g --pixel 150,120 --pixel 300,200 --max-lag 60
camera-noise entropy experiments\dark-001 --channel b
camera-noise entropy experiments\dark-001 --channel b --external-tool nist90b
camera-noise quantum experiments\dark-001\entropy --qubits 4 --depth 8 --circuits 5 --strategy random_angles --angle-bits 10 --simulate --shots 1024
```

Run the test suite:

```powershell
.venv\Scripts\python.exe -m pytest
```

### 12.4 Where things live

- **Experiments:** `experiments/<timestamp>_<camera|dark><id>/` for each capture,
  containing `frames.npy`, `timestamps.npy`, `metadata.json`, and nested
  `analysis/` and `entropy/` output directories. `experiments/yuyv-dark-001/`
  holds the YUYV analysis JSON.
- **Reports:** the root-level `experiment_report_*.md` files and
  `camera_capabilities.md`.
- **YUYV test data:** `test_yuyv.yuv` (300 frames @ 1280x720). Re-analyze with
  the module directly:

```python
from pathlib import Path
from camera_noise.analysis.yuyv import analyze_yuyv_file, write_yuyv_results
results = analyze_yuyv_file(Path("test_yuyv.yuv"), width=1280, height=720, frame_count=300)
write_yuyv_results(results, Path("experiments") / "yuyv-dark-001")
```

- **Qiskit demonstration:** run the `quantum` subcommand against any entropy
  directory; outputs QASM3 files and `quantum_report.json`.

### 12.5 File classification

- **Reusable infrastructure (production-grade engineering):** `storage.py`,
  `collector.py`, `backends/`, `models.py`, `analysis/session.py`,
  `analysis/math_utils.py`, `analysis/engine.py`, `analysis/entropy.py`,
  `analysis/plots.py`, `capabilities.py`, `cli.py`.
- **Experiment-specific:** `analysis/yuyv.py`, `tests/test_yuyv.py`,
  `experiment_report_yuyv_dark_001.md`.
- **Proof-of-concept / demonstration:** `quantum.py` (valid plumbing, not a
  validated randomness back-end).
- **Historical/experimental artifacts:** `experiments/` (large capture and
  derived data), `test_yuyv.yuv`, the `.test-tmp-*` directories (scratch), and
  `camera_capabilities.json/.md` (a point-in-time audit).

---

## 13. Final conclusion

The project **successfully established a complete experimental pipeline** for
investigating consumer-camera sensor noise as a potential entropy source: it
acquired frames through multiple backends, probed capabilities and settings,
characterized BGR and direct-YUYV dark output, implemented several extraction
methods and a layered entropy/statistics methodology, preserved everything with
lossless provenance, and demonstrated camera-derived bits driving Qiskit
circuits on Aer.

**However**, the specific Lenovo Legion integrated webcam did not expose
sufficiently low-level sensor data (no RAW/Bayer, 8-bit processed video only),
and its accessible YUYV/BGR outputs exhibited strong ISP-related structure,
spatial correlation, temporal dependence, common-mode drift, frame duplication,
and configuration sensitivity. No configuration demonstrated a defensible,
independent, general-purpose physical entropy source, and the YUYV path did not
materially improve on the BGR path.

The project is therefore **concluded as a well-characterized proof-of-concept /
negative feasibility study** rather than promoted into a production RNG package.

This is not a claim that "camera-based RNG is impossible." Sensors with raw
access and controlled physics could plausibly supply entropy. The conclusion is
narrow and evidence-based:

> this particular hardware/interface/approach was not sufficiently defensible
> for the original general-purpose objective.

---

## 14. Project status

| Area | Status | Explanation |
|---|---|---|
| Camera acquisition | Complete | OpenCV DirectShow/MSMF capture with retries, warmup, partial-capture retention; verified sessions |
| RAW sensor access | Not achieved | DirectShow exposed only YUYV 4:2:2 / NV12; no RAW/Bayer/high-bit-depth ever negotiated |
| BGR experiments | Complete | dark-001 baseline + 7-session exposure/backend sweep, fully analyzed and reported |
| YUYV experiments | Complete | 300-frame direct YUYV capture verified, decoded, analyzed (Y/U/V, temporal/spatial/common-mode/bits) |
| Noise extraction | Implemented, not viable | 5 methods + Von Neumann variants; LSBs biased, differences/pairs dependent |
| Entropy validation | Diagnostic only | Shannon/min-entropy/MCV-with-Wilson, tests, prediction; never certified; no source met validation bar |
| Streaming RNG | Not built | Deliberately out of scope; not justified by the source evidence |
| Cryptographic conditioning | Not built | Correctly omitted: hashing cannot create absent entropy |
| Python packaging | Complete | `camera-noise-collector` 0.2.0 installable, `camera-noise` CLI, optional quantum group |
| Qiskit integration | Demonstrated | Camera bits parameterize QASM3 circuits run on Aer; not a physical `NoiseModel` and not validated randomness |
| Scientific validity | Negative feasibility result | Reproducible evidence that this camera's accessible output is not a general-purpose entropy source |
| Final project status | **Closed as documented feasibility investigation** | Stopped intentionally at the proof-of-concept stage; repository preserved as the complete record |
