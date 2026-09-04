# Beat math for frame-accurate cutting

`detect_beats` returns beat and downbeat times in **source seconds** plus an estimated `bpm`.
The timeline works in **frames**. Every sync error in a montage comes from mishandling that
boundary, so do the arithmetic once, up front, and keep it.

## Converting a beat time to a timeline frame

For a cut placed at beat time `B` (source seconds) on a clip:

```
timelineFrame = startFrame + (B * fps - trimStartFrame) / speed
```

For the **music track laid at timeline frame 0 with speed 1 and no trim** — which is how you
should always lay it — this collapses to:

```
timelineFrame = round(B * fps)
```

Lay the music at frame 0 first, and every downbeat becomes a plain `round(B * fps)`. If you
offset or trim the music later, every cached beat frame is wrong; re-derive them.

Take `startFrame`, `trimStartFrame` and `speed` for the bed from the `add_clips` delta rather than
your own arithmetic.

## Palmier truncates; you round

Internally Palmier converts with `Int(seconds * fps)` — **truncation, not rounding**. That matters
whenever you hand it seconds, which is every `source: [in, out]` on `add_clips`.

`detect_beats` returns 2-decimal seconds, so a beat at `4.99` s on a 30 fps timeline truncates to
frame 149, not 150. Quantise your own in and out points to exact frame boundaries before passing
them:

```
srcIn  = frameIn  / fps      // e.g. 150 / 30 = 5.0
srcOut = frameOut / fps
```

Round when computing a *target* frame from a beat (`round(B * fps)`), then convert back to seconds
by division. Never hand Palmier a raw 2-dp beat time and assume it lands on the frame you meant.

## Frames per beat / per bar

Beat intervals are almost never whole frames. The table gives `frames per beat / frames per
bar` (4/4) at common tempos.

| BPM | 24 fps | 30 fps | 60 fps | 1 bar |
|---|---|---|---|---|
| 80 | 18.00 / 72.0 | 22.50 / 90.0 | 45.00 / 180.0 | 3.00 s |
| 85 | 16.94 / 67.8 | 21.18 / 84.7 | 42.35 / 169.4 | 2.82 s |
| 88 | 16.36 / 65.5 | 20.45 / 81.8 | 40.91 / 163.6 | 2.73 s |
| 90 | 16.00 / 64.0 | 20.00 / 80.0 | 40.00 / 160.0 | 2.67 s |
| 95 | 15.16 / 60.6 | 18.95 / 75.8 | 37.89 / 151.6 | 2.53 s |
| 100 | 14.40 / 57.6 | 18.00 / 72.0 | 36.00 / 144.0 | 2.40 s |
| 110 | 13.09 / 52.4 | 16.36 / 65.5 | 32.73 / 130.9 | 2.18 s |
| 120 | 12.00 / 48.0 | 15.00 / 60.0 | 30.00 / 120.0 | 2.00 s |
| 128 | 11.25 / 45.0 | 14.06 / 56.2 | 28.12 / 112.5 | 1.88 s |
| 140 | 10.29 / 41.1 | 12.86 / 51.4 | 25.71 / 102.9 | 1.71 s |
| 150 | 9.60 / 38.4 | 12.00 / 48.0 | 24.00 / 96.0 | 1.60 s |
| 160 | 9.00 / 36.0 | 11.25 / 45.0 | 22.50 / 90.0 | 1.50 s |
| 170 | 8.47 / 33.9 | 10.59 / 42.4 | 21.18 / 84.7 | 1.41 s |
| 174 | 8.28 / 33.1 | 10.34 / 41.4 | 20.69 / 82.8 | 1.38 s |

## Drift: the rule that matters

**Always compute each cut from the absolute beat time. Never accumulate durations.**

Wrong — rounding error compounds, and by bar 16 the cut is visibly late:

```
frame += round(framesPerBeat)     // drifts
```

Right — every cut is derived independently from the beat list:

```
frames = downbeats.map(b => Math.round(b * fps))   // never drifts
```

At 128 BPM / 30 fps a beat is 14.0625 frames. Accumulating over 64 beats loses 4 frames — an
eighth of a second, plainly audible as a cut that misses the hit.

## Phrase structure

Hip-hop and most electronic music is built in powers of two. `detect_beats` gives you beats and
downbeats; group the downbeats yourself:

- **1 bar** = 4 beats — the shortest hold that reads as deliberate
- **4 bars** = one phrase — the natural unit for a montage section
- **8 bars** = a section (verse, hook, breakdown)
- **16 or 32 bars** = a full movement

Section boundaries land on downbeats whose index is a multiple of 4 or 8. Cut your *structure*
on those; cut individual shots on any downbeat, and only cut on off-beats deliberately.

## Shot lengths in bars

Express every planned shot length in bars, not seconds, then convert once:

| Hold | Beats | Frames @128 BPM/30fps | Reads as |
|---|---|---|---|
| ½ bar | 2 | 28 | fast montage, drop territory |
| 1 bar | 4 | 56 | the default montage cut |
| 2 bars | 8 | 113 | a shot that breathes |
| 4 bars | 16 | 225 | a hero shot or an establisher |

At 128 BPM a ½-bar cut is ~0.94 s and a 1-bar cut is ~1.88 s. Below about 8 frames (~0.27 s) a
shot stops reading as an image and becomes a flash — that is a legitimate effect at a drop, but
it is not a shot.

## Sanity checks before you cut

1. `detect_beats` on speech or ambience returns few or no beats — that means you pointed it at
   the wrong asset, not that the music has no beat.
2. Compare the reported `bpm` against the gap between consecutive downbeats: `4 * 60 / bpm`
   should match. A halved or doubled value means the detector picked the wrong metrical level;
   use the downbeat spacing you actually observe.
3. Check the last downbeat against the music's duration. If they diverge, the track has a tempo
   change or a non-4/4 section — re-derive per section rather than extrapolating.
