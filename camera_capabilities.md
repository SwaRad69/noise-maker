# Camera Capability Report

This report covers capability discovery only. No entropy, randomness, bit extraction, or Qiskit analysis was performed.

## 1. Device

- OpenCV camera index: `0`
- Device name: `Integrated Camera`
- Device identifier: `USB\VID_04F2&PID_B828&MI_00\7&17eca250&0&0000`

## 2. Hardware/Driver Information

| Field | Measured value | Source |
| --- | --- | --- |
| Manufacturer | Realtek | Windows registry camera enumeration (read-only) |
| Device presence | present | Windows registry camera enumeration (read-only) |
| Driver INF | oem47.inf | Windows registry camera enumeration (read-only) |
| Driver original name | unknown | Windows registry camera enumeration (read-only) |
| Driver provider | Realtek | Windows registry camera enumeration (read-only) |
| Driver version | 10.0.22000.20384 | Windows registry camera enumeration (read-only) |
| Current-user camera consent | unknown | Windows CapabilityAccessManager registry |
| Machine camera consent | Allow | Windows CapabilityAccessManager registry |

## 3. Backends Tested

| Backend | Status | Opened Camera 0 | Open attempts | Actual backend | Default mode | Default read | Failure |
| --- | --- | --- | --- | --- | --- | --- | --- |
| dshow | open_failed | no | 1:failed (0.14379 s); 2:failed (0.026711 s); 3:failed (0.024899 s); 4:failed (0.027277 s); 5:failed (0.023146 s); 6:failed (0.023436 s); 7:failed (0.027395 s); 8:failed (0.027007 s) | unknown | unknown | unknown | VideoCapture.isOpened() remained false after 8 isolated attempts |
| msmf | open_failed | no | 1:failed (0.113857 s); 2:failed (0.084456 s); 3:failed (0.090937 s); 4:failed (0.084816 s); 5:failed (0.094435 s); 6:failed (0.088839 s); 7:failed (0.085225 s); 8:failed (0.081955 s) | unknown | unknown | unknown | VideoCapture.isOpened() remained false after 8 isolated attempts |

## 4. Supported Video Modes

These are modes actually honored from the configured probe grid, not an exhaustive native driver advertisement dump.

| Backend | Width | Height | FPS | FOURCC | Opened | Usable | Fully verified | Verification | Delivered array |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

### Historical project evidence (not a fresh capability result)

The current run's open failures are kept separate from earlier completed captures. These records demonstrate prior access, but do not prove that the same modes are available at this moment.

| Session | Backend | Mode | FOURCC | Exposure | Gain | Frames | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| experiments\dark-exposure-001 | DSHOW | 1280x720 @ 0.0 fps | YUY2 | -8 | -1 | 300 | complete |
| experiments\dark-exposure-002 | DSHOW | 1280x720 @ 0.0 fps | YUY2 | -6 | -1 | 300 | complete |
| experiments\dark-exposure-003 | DSHOW | 1280x720 @ 0.0 fps | YUY2 | -5 | -1 | 300 | complete |
| experiments\dark-exposure-004 | DSHOW | 1280x720 @ 0.0 fps | YUY2 | -4 | -1 | 300 | complete |
| experiments\dark-exposure-005 | DSHOW | 1280x720 @ 0.0 fps | YUY2 | -3 | -1 | 300 | complete |

## 5. Pixel Formats

| Backend | FOURCC | Description | RAW/Bayer-like name | Observed meaning |
| --- | --- | --- | --- | --- |

## 6. Compression

Uncompressed video does NOT automatically mean raw sensor data. A valid path may still be `sensor -> ISP -> YUV -> USB`.

| Backend | Mode | Classification | Evidence |
| --- | --- | --- | --- |

## 7. Bit Depth

Highest accessible reported/format-derived component bit depth: `unknown` bits.

This is the exposed stream/component depth, not an inference about the internal ADC or sensor precision.

| Backend | Mode | Component bits | Application dtype bits |
| --- | --- | --- | --- |

## 8. Camera Controls

Native min/max/step/default values are `unknown` because OpenCV does not expose them. Change verification used setter calls, readback, and restoration.

| Backend | Control | Current | Min | Max | Step | Default | Setter | Change verified | Readback | Restored |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

A successful setter/readback is evidence of interface acceptance, not proof that the hardware applied the value exactly or that all automatic processing stopped.

## 9. RAW/Bayer Availability

**RAW/Bayer access was not exposed through the tested interface.**

This does not establish whether the camera hardware, proprietary software, firmware, or an undocumented interface supports RAW.

## 10. Mode Negotiation

| Backend | Requested | Actual | Read | Usable | Fully verified | Verification | Silent fallback |
| --- | --- | --- | --- | --- | --- | --- | --- |

## 11. Backend Comparison

| Backend | Works | Honored modes | Validated modes | Verified exposure | Verified gain |
| --- | --- | --- | --- | --- | --- |
| dshow | no | 0 | 0 | no | no |
| msmf | no | 0 | 0 | no | no |

## 12. Processing/ISP Considerations

Directly observed: backend open/read results, FOURCC readbacks, dimensions/FPS, OpenCV arrays, control readbacks, and tiny-frame duplication checks.

Likely for ordinary YUV/RGB webcam modes: sensor and analog stages feed an ADC, followed by some camera ISP/firmware processing before USB and Windows delivery.

Unknown: demosaicing location, denoising, temporal filtering, black-level correction, tone mapping, sharpening, compression details, internal precision, and whether proprietary paths bypass any stage.

Observed `YUY2` would mean software can receive uncompressed YUV through that interface. It would not prove raw sensor measurements.

## 13. Candidate Least-Processed Modes

| Rank | Backend | Mode | Score | Evidence-based reasons |
| --- | --- | --- | --- | --- |

## 14. Recommended Configuration

No camera mode can be recommended from this run because no tested mode was successfully honored.

Lowest-level classification: **D - The available interfaces do not provide enough information to determine the processing level**.

No tested backend produced an honored mode, so this run cannot characterize the accessible stream.

Primary next experiment: Restore Camera 0 access, then verify the previously successful DirectShow configuration 1280x720 YUY2 at exposure -3.0. Rerun capability discovery before starting a new noise experiment.

Secondary experiments:

- Compare the best uncompressed mode with the best compressed mode at matched exposure, gain, resolution, and FPS.
- Investigate manufacturer-specific tools or documented extension controls only if they explicitly expose RAW/Bayer or higher-bit-depth streams.

## 15. Unknowns and Limitations

- OpenCV does not expose native DirectShow/Media Foundation control min/max/step/default metadata.
- The active mode probe covers the configured resolution/FPS/FOURCC grid, not every vendor-specific media type.
- An observed YUV/RGB stream does not reveal which ISP, denoising, temporal filtering, tone mapping, or firmware stages ran.
- Internal ADC/sensor bit depth cannot be inferred from the application stream bit depth.
- Proprietary manufacturer software, undocumented extension controls, firmware interfaces, and hardware-specific methods were not tested.
- A tiny validation capture can reveal obvious repetition but cannot establish long-term buffering or temporal-filter behavior.
- When Windows detects a started camera but OpenCV cannot open it, this layer cannot distinguish a physical/e-shutter, exclusive use, or a driver/runtime failure.

### Confirmed

Only values in the tables that came from successful opens, readbacks, honored requests, or delivered validation frames.

### Unknown

Anything available only through proprietary software, undocumented controls, firmware, or hardware-specific interfaces, plus all unobserved internal sensor/ISP details.

No claims are made here about entropy, true randomness, quantum randomness, or cryptographic security.
