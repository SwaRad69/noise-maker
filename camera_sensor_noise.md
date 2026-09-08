# Understanding Noise from a Covered Laptop Camera

A covered webcam does not directly expose "pure sensor noise." What software receives is a digital image produced by the sensor plus the webcam's internal electronics and image-processing pipeline.

The immediate goal is therefore:

> Measure how pixel values fluctuate across space and time when the camera receives as little light as possible, then determine which fluctuations come from the sensor, which come from processing, and whether their statistics are stable and useful.

A useful conceptual model for pixel \(i\) in frame \(t\) is:

\[
D_{i,t}
=
\text{offset}_i
+
\text{light signal}_{i,t}
+
\text{dark signal}_{i,t}
+
\text{read/electronic noise}_{i,t}
\]

The webcam then applies gain, black-level correction, denoising, color processing, compression, clipping, and other transformations before returning the final value \(D_{i,t}\).

## 1. Noise inside a camera sensor

A CMOS or CCD sensor is an array of light-sensitive pixels. During an exposure, incoming photons can release electrons in each pixel. The accumulated electrical charge is converted into a voltage and then into a digital pixel value.

Variation can enter at several stages:

1. The number of photons arriving naturally fluctuates.
2. Electrons can be generated thermally even without light.
3. Pixels differ slightly because of manufacturing tolerances.
4. Resetting and measuring a pixel introduces uncertainty.
5. Amplifiers and analog electronics add voltage noise.
6. Digitization rounds an analog voltage to an integer.
7. Later image processing modifies or obscures the original fluctuations.

Some of these effects vary randomly from frame to frame. Others create stable patterns that repeat in nearly the same locations.

That distinction is fundamental:

- **Temporal noise** changes over time at the same pixel.
- **Spatial variation** differs between pixels in the same frame.
- **Fixed-pattern noise** is spatial variation that remains relatively stable across frames.

A dark frame normally contains a combination of all three.

## 2. Major types of camera noise

### Photon noise or shot noise

Light arrives as discrete photons rather than as a perfectly smooth flow. Even under constant illumination, the exact number of photons detected during each exposure varies randomly.

If a pixel detects an average of \(N\) photons or photoelectrons, the standard deviation is approximately:

\[
\sigma \approx \sqrt{N}
\]

This is usually modeled by a Poisson distribution.

Its relative importance is:

\[
\frac{\sqrt{N}}{N} = \frac{1}{\sqrt{N}}
\]

So photon shot noise becomes proportionally less visible as the light level increases.

With a truly covered lens, photon shot noise from the scene should become very small because almost no photons should reach the sensor. Light leakage or infrared light can still produce it.

Shot noise is rooted in the discrete detection of photons, but the webcam output cannot automatically be treated as a clean quantum-random source. The signal has passed through many classical electronic and processing stages.

### Read noise

After exposure, the sensor must measure the charge stored in each pixel. That measurement is imperfect.

Read noise can arise from:

- resetting a pixel before exposure;
- converting charge into voltage;
- pixel-level or column-level amplifiers;
- analog signal routing;
- the analog-to-digital converter.

Read noise is present even when no light reaches the sensor. It usually appears as small frame-to-frame fluctuations around a dark baseline.

Read noise is one of the main effects we hope to observe in a covered-camera experiment.

### Dark current noise

Heat can generate electrons inside a pixel even when no photons arrive. This accumulated unwanted charge is called **dark current**.

Dark current generally:

- increases with exposure duration;
- rises strongly as sensor temperature increases;
- differs from pixel to pixel;
- can produce bright or "hot" pixels.

The average dark current creates a dark signal or bias. Because the number of thermally generated electrons fluctuates, it also creates **dark-current shot noise**.

These are related but different:

- **Dark signal:** the average unwanted charge accumulated in darkness.
- **Dark-current noise:** the random variation around that average.

Laptop sensors are small and often operate at warm temperatures, so dark current may be observable during long exposures or high-gain operation. However, webcam firmware may subtract or suppress much of it.

### Thermal noise

Thermal agitation of charge carriers produces random voltage fluctuations in electronic components. A common electrical model is Johnson-Nyquist noise.

Thermal noise can occur in:

- pixel electronics;
- amplifiers;
- resistive components;
- analog readout paths.

"Thermal noise" is sometimes used loosely to include dark current, but they are not exactly the same:

- **Dark current** is unwanted charge generated in the light-sensitive region.
- **Thermal electronic noise** is voltage or current fluctuation in the readout electronics.

Both depend on temperature, but through different mechanisms.

### Fixed-pattern noise

No two pixels or readout circuits are perfectly identical. Manufacturing differences produce repeatable spatial patterns.

Important forms include:

- **Dark-signal non-uniformity:** pixels have different dark offsets or dark-current rates.
- **Photo-response non-uniformity:** pixels respond differently to the same amount of light.
- **Column or row noise:** shared readout circuitry gives entire rows or columns a similar offset.
- **Hot pixels:** particular pixels produce unusually large dark signals.

If the same bright pixel, band, or texture appears in many dark frames, that is probably fixed-pattern behavior rather than fresh randomness.

Fixed-pattern noise may look noisy in one image, but it contains relatively little new information from frame to frame. For a randomness-oriented project, repeatedly reading a stable hot-pixel map is not useful entropy.

### Electronic noise

"Electronic noise" is a broad umbrella term rather than one single mechanism. It can include:

- amplifier noise;
- reset noise;
- transistor flicker or \(1/f\) noise;
- power-supply variation;
- analog-to-digital converter noise;
- clock interference;
- USB or motherboard electromagnetic interference;
- quantization effects.

Read noise is therefore partly electronic noise. The terms overlap:

- **Read noise** describes noise associated with measuring and digitizing the pixel.
- **Electronic noise** describes the wider family of electrical mechanisms that can affect the signal.

Electronic interference may also produce structured artifacts such as periodic bands, repeated values, or correlations between pixels.

## 3. What a laptop webcam can realistically reveal

A normal webcam will probably let us observe the combined effect of:

- read noise;
- electronic noise;
- some dark-current variation;
- fixed-pattern noise;
- hot pixels;
- temperature-dependent drift;
- possible light leakage;
- artifacts from gain, compression, and image processing.

It will be difficult to cleanly separate these mechanisms using ordinary processed video alone.

Photon shot noise is best studied under controlled, very dim but nonzero illumination. With the lens covered, the intended photon signal is near zero, so read noise and dark effects should become relatively more important.

There are also important limitations:

- Many webcams expose only 8-bit processed video.
- The sensor may internally operate at a higher bit depth, but the lower bits may be discarded.
- The driver may supply YUV or compressed video instead of raw sensor measurements.
- Automatic gain may amplify tiny fluctuations.
- Denoising may remove real fluctuations.
- Black-level correction may force dark pixels toward zero.
- Clipping may make many pixels exactly zero.
- Hot-pixel correction may replace unusual pixels with neighboring values.

Consequently, we will initially be studying the webcam system's output noise, not just the underlying semiconductor sensor.

## 4. Processing before software receives the image

A typical webcam contains an image signal processor, often called an ISP. The ISP or driver may perform several operations.

### Automatic exposure

The webcam may increase the exposure time in darkness. Longer exposure collects more dark current and gives more opportunity for light leaks to contribute.

Exposure may also change from frame to frame while the automatic controller searches for an acceptable brightness.

### Automatic gain

In a dark scene, the webcam may raise analog or digital gain. This makes sensor noise more visible, but it also changes its distribution.

If gain is continually adjusted, frames collected at different times are not directly comparable.

### Black-level correction

Real sensors have a baseline offset so that small negative-going electronic fluctuations can be represented. The camera may estimate and subtract this black level.

After subtraction, values below zero are clipped. A large group of zero-valued pixels can therefore mean that the processing pipeline has destroyed part of the original noise distribution.

### Denoising and temporal filtering

The webcam may smooth neighboring pixels or combine information from consecutive frames.

This can create:

- correlations between nearby pixels;
- correlations between consecutive frames;
- reduced variance;
- delayed or "ghosted" changes.

Temporal denoising is especially problematic when we want to treat consecutive samples as independent.

### Defective-pixel correction

Hot or dead pixels may be detected internally and replaced with interpolated values. That hides real sensor defects and can introduce correlation with neighboring pixels.

### Demosaicing

Most color sensors use a color filter array, commonly a Bayer pattern. Each physical pixel initially measures only one color band. The ISP estimates full red, green, and blue values by combining neighboring sensor measurements.

Therefore, RGB values returned by the webcam may not correspond to independent physical sensor sites.

### White balance and color correction

The camera can multiply color channels by different gains and mix channels through a color-correction matrix. This changes variances and creates dependencies between channels.

### Gamma and tone curves

The relationship between sensor charge and returned pixel value may be nonlinear. Gamma and tone processing can stretch some ranges and compress others.

### Sharpening and contrast enhancement

These operations combine neighboring pixels and can make random noise appear stronger or more structured.

### Compression

Webcams may transmit MJPEG, H.264, or another compressed format. Compression can create:

- block patterns;
- value quantization;
- artificial spatial correlations;
- temporal correlations;
- loss of weak noise components.

Even an apparently uncompressed software format may have been processed internally before transmission.

## 5. Why covering the lens helps

Covering the lens removes, or greatly reduces, the largest intended input: scene light.

Without a cover, changes in pixel values could come from:

- moving objects;
- flickering room lights;
- display refresh;
- shadows;
- exposure changes;
- small camera movements;
- actual sensor noise.

A good opaque cover makes the experiment easier to interpret. With external light suppressed, remaining variation is more likely to come from:

- dark current;
- read noise;
- internal electronics;
- fixed-pattern differences;
- camera processing.

The cover also creates a spatially uniform condition. Ideally, every pixel receives approximately zero light. Persistent differences between pixels can then reveal offsets, hot pixels, or readout patterns.

However, darkness alone does not separate all internal mechanisms. It gives us a dark-noise mixture.

## 6. Sources of contamination with the lens covered

Several effects can remain.

### Light leakage

A cover that looks opaque to the eye may transmit infrared light. Light can also enter around the lens assembly or through gaps in the laptop bezel.

Nearby status LEDs and the laptop screen can matter.

### Temperature changes

The webcam warms after activation. The laptop's processor, battery charging, display, and cooling system can change the sensor's temperature.

This may cause:

- rising dark current;
- changes in hot pixels;
- slow baseline drift;
- changing read-noise characteristics.

A recording made immediately after enabling the camera may differ from one made after ten minutes.

### Automatic camera controls

Auto-exposure, auto-gain, auto-white-balance, and auto-black-level algorithms may continue adjusting the output despite the covered lens. This can create large changes that are generated by a control loop, not by microscopic noise.

### Power and electromagnetic interference

USB traffic, switching power supplies, battery chargers, displays, Wi-Fi radios, and processor activity can create structured electrical interference.

Mains electricity can also lead to periodic behavior near 50 Hz in India, or related harmonics, although the exact pattern depends on frame rate, exposure, and electronics.

### Compression and driver behavior

Compression can create fake texture. Drivers may duplicate or drop frames, convert color formats, or cache frames.

Two identical consecutive frames would be strong evidence that the output is not delivering a fresh independent measurement each time.

### Mechanical and environmental effects

An imperfect cover can move slightly. Airflow and temperature changes can affect the camera module. Pressure on the bezel could alter the cover or, in unusual cases, the module itself.

### Quantization and clipping

If nearly all values are rounded or clipped to the same integer, genuine analog noise may exist below the digital resolution but remain invisible to software.

### Persistent sensor patterns

Hot pixels and column offsets are real sensor effects, but stable patterns should not be mistaken for new random noise.

## 7. What "good noise" means here

There are two different possible meanings, and they should remain separate.

For studying a physical noise source, useful noise is:

- measurable above quantization;
- not dominated by light leakage;
- not dominated by compression artifacts;
- statistically characterizable;
- reasonably stable under controlled conditions;
- accompanied by known exposure, gain, frame rate, and temperature conditions.

For producing changing values that might drive a simulation, useful noise should also have:

- **Variation:** the output does not remain constant.
- **Entropy:** samples are not concentrated into only a few predictable values.
- **Freshness:** new frames contain new information.
- **Low temporal correlation:** the next sample is not mostly determined by previous samples.
- **Controlled spatial correlation:** neighboring pixels are not simply copies or filtered versions of each other.
- **Limited periodic structure:** there are no strong repeating bands or cycles.
- **Robustness:** behavior does not completely change when the webcam warms up.
- **Traceability:** we know how the samples were obtained and transformed.

"Good" does not necessarily mean perfectly uniform or Gaussian. Qiskit noise simulations often need particular physical distributions, correlations, drift, or error rates. A nonuniform source can still be useful if it is measured honestly and transformed appropriately.

It is also important not to overstate the result:

> Noise from a webcam is not automatically quantum randomness.

Photon detection and some microscopic semiconductor events have quantum origins, but the delivered video has been mixed with classical thermal noise, deterministic patterns, feedback algorithms, and digital processing. Without a careful raw-sensor and entropy analysis, it should not be used for cryptography or claimed as a certified quantum random-number generator.

For this project, the most defensible goal is initially to use webcam noise as an experimentally measured stochastic signal or as data from which simulation parameters can be derived.

## 8. What to measure before judging the camera

We should characterize the camera in stages.

### Dark-level distribution

Measure which pixel values appear and how often they occur.

Questions include:

- Are most pixels clipped to zero?
- Is there a usable spread of values?
- Is the distribution smooth, quantized, skewed, or multimodal?
- Do RGB channels behave differently?

### Mean and variance

For every pixel, estimate:

- its average value over many frames;
- its frame-to-frame variance.

The average image exposes fixed-pattern offsets. The variance image shows where temporal fluctuations are strongest.

A pixel with a high mean but almost no temporal variation may be a fixed hot pixel, not a valuable changing source.

### Temporal behavior

For selected pixels and aggregated measurements, determine:

- whether values drift as the camera warms;
- whether consecutive frames are correlated;
- whether frames repeat;
- whether there are periodic patterns;
- whether variance changes over time.

This separates fresh fluctuation from slow drift and webcam control-loop behavior.

### Spatial behavior

Measure whether neighboring pixels, rows, or columns move together.

Strong correlation could come from:

- shared column amplifiers;
- demosaicing;
- denoising;
- compression blocks;
- electrical interference.

A million output pixels do not provide a million independent samples if the ISP has strongly correlated them.

### Fixed-pattern component

Compare the average of many dark frames with individual frames.

This lets us conceptually separate:

\[
\text{dark frame}
=
\text{persistent spatial pattern}
+
\text{time-varying residual}
\]

The persistent component may be interesting for sensor characterization, but the residual is more relevant when looking for fresh noise.

### Dependence on settings

If settings can be controlled, compare behavior across:

- exposure times;
- gain levels;
- frame rates;
- resolutions;
- color or grayscale formats;
- compressed and uncompressed modes.

Useful clues include:

- Variance increasing with exposure may indicate dark-current effects.
- Variance increasing mainly with gain may indicate amplified read noise.
- Large changes between formats may indicate processing or compression effects.

### Warm-up and temperature sensitivity

Record how the mean, variance, and hot-pixel population change from camera startup through thermal stabilization.

The laptop's charging state and workload should also be considered because they influence temperature and electrical conditions.

### Entropy and predictability

Before treating samples as a useful input source, examine:

- value frequencies;
- autocorrelation;
- repeated frames;
- repeated patterns;
- bias;
- runs of similar values;
- approximate entropy per sample;
- how results vary between recording sessions.

Passing a few statistical randomness tests would not prove that a source is unpredictable. Such tests can detect obvious problems, but they cannot establish the physical origin of the noise.

### Reproducibility

Repeat the experiment on different days and under different laptop conditions.

We want to know whether an observed pattern is:

- a persistent property of the sensor;
- an effect of a particular camera configuration;
- temperature-dependent;
- caused by environmental interference;
- merely a one-session accident.

The first experimental milestone should therefore be a clear characterization of the webcam's dark video output: its baseline, time-varying fluctuations, fixed patterns, correlations, processing artifacts, temperature drift, and dependence on camera settings. Only after that can we decide whether it offers a meaningful signal for parameterizing or driving a Qiskit noise simulation.

## 9. Entropy and randomness: five different claims

Entropy analysis must keep several ideas separate.

1. **Looks random** means a plot or a limited set of diagnostics shows no obvious pattern.
2. **Passes statistical tests** means named tests did not reject their reference models at a selected threshold and sample size.
3. **Contains measurable entropy** means observed outcomes have a quantified spread under a stated model, quantizer, and confidence method.
4. **Is unpredictable** means even an informed predictor cannot guess future outputs substantially better than the claimed bound.
5. **Is cryptographically secure** additionally requires a threat model, a validated entropy-source design, continuous health tests, and a sound conditioner or extractor.

The first three can provide useful experimental evidence. They do not by
themselves establish the last two. A deterministic generator can pass many
statistical tests, and a physical source can have entropy while still leaking
enough state to be predictable to an observer.

Shannon entropy measures average surprise. Min-entropy uses the probability of
the most likely outcome and therefore describes the easiest single guess. For
randomness extraction, min-entropy is generally the more conservative quantity.
Both estimates depend on how continuous or high-resolution measurements are
quantized into symbols.

Pooled camera amplitudes require special care. A stable pattern of different
pixel offsets can have a rich histogram but provide no fresh information in the
next frame. Per-pixel temporal estimates and frame-to-frame differences are
more relevant to fresh entropy, although differencing does not create entropy
and may retain camera-control or filtering dependencies.

Least significant bits are not automatically random. An ISP, codec, quantizer,
black-level correction, or clipping rule can determine them. They should be
compared with other extraction rules and tested for bias, runs, collisions,
serial dependence, autocorrelation, and held-out predictability. Von Neumann
pair extraction can remove simple independent bias, but it discards data and
does not repair general dependence.

NIST SP 800-90B estimators and restart tests are the appropriate reference for
entropy-source validation. NIST SP 800-22, PractRand, and Dieharder are useful
for detecting statistical defects in bitstreams, but passing them does not
prove true randomness, physical origin, unpredictability, or cryptographic
security.
