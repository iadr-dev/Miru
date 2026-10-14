// SFX pin table (sound-design.md §4; aesthetic rules S1–S4). SFX only — no BGM (see DESIGN-SPEC).
// Every `from` is relative to its shot (§4.5, never a bare frame); offsets reference the same
// in-shot moments the scenes animate on. Film vocabulary only: whoosh / impact / riser / sparkle /
// transition, plus real-object foley (camera shutters, light switches) where the picture has that
// action. All files are Mixkit, with traceable source URLs (AUDIO-CREDITS.md); the library's
// untraceable keyboard / riser-cine / sparkle / pop were swapped for traced equivalents.
import React from 'react';
import { Audio, Sequence, interpolate, staticFile } from 'remotion';
import { SHOTS, TOTAL_FRAMES } from './timeline';

type Pin = { from: number; src: string; volume: number; duration?: number; fadeTo?: number; fadeLen?: number; note: string };

const S = SHOTS;
// Master gain: the first mix measured peak −6.1 dBFS / RMS −30.3 dBFS with no clipping; +3.5 dB
// keeps the peak under −2.5 dBFS while lifting the SFX-only film to a confident level.
const MASTER = 1.5;
const ROW_CUES = [42, 53, 63]; // Answer rows 1–3 (the last two merge into one sweep, card guidance)
const PILL_BEAT = 21;
const PILL_INTRO = 12;

export const SFX: Pin[] = [
  // 1 · noise
  { from: S.noise.from, src: 'air-woosh-deep.mp3', volume: 0.25, note: 'the first word arrives (fills the near-silent first half-second)' },
  { from: S.noise.from, src: 'swoosh-slow.mp3', volume: 0.3, duration: 165, note: 'the tunnel of words rushing past' },

  // 2 · ask
  { from: S.ask.from, src: 'transition-soft.mp3', volume: 0.45, note: 'composer drops in as the noise recedes' },
  // Typing spans t 0.06→0.73 of the 150 f shot (frames 9–109). Kept low: this is the film's
  // quietest beat by design (review: the first mix peaked here at −4.4 dBFS).
  { from: S.ask.from + 9, src: 'typewriter-digital.mp3', volume: 0.18, duration: 40, note: 'typing the question (1/3)' },
  { from: S.ask.from + 49, src: 'typewriter-digital.mp3', volume: 0.16, duration: 40, note: 'typing the question (2/3)' },
  { from: S.ask.from + 89, src: 'typewriter-digital.mp3', volume: 0.14, duration: 20, note: 'typing the question (3/3), ends with the last character' },

  // 3 · sources converge
  { from: S.converge.from + 7, src: 'sweep-short.mp3', volume: 0.25, note: 'the four paths draw on' },
  { from: S.converge.from + 57, src: 'air-woosh-quick.mp3', volume: 0.35, note: 'sources start sliding down their curves' },
  { from: S.converge.from + 122, src: 'camera-lens-shutter.mp3', volume: 0.5, note: 'swallowed into the aperture mark: the logo’s own sound' },
  { from: S.converge.from + 134, src: 'swoosh-quick.mp3', volume: 0.18, note: 'frame glides the mark to centre' },

  // 4 · the answer
  { from: S.answer.from + 18, src: 'transition-soft.mp3', volume: 0.32, note: 'summary lands first' },
  ...ROW_CUES.map((cue, i) => ({
    from: S.answer.from + cue,
    src: i % 2 === 0 ? 'switch-tap.mp3' : 'switch-click-quick.mp3', // two samples alternating (S2)
    volume: [0.3, 0.26, 0.22][i],
    note: `evidence row ${i + 1} files in`,
  })),
  { from: S.answer.from + 72, src: 'sweep-fast.mp3', volume: 0.16, note: 'rows 4–5 tighten into one low sweep (no machine-gun pops)' },
  { from: S.answer.from + 97, src: 'click-camera.mp3', volume: 0.3, note: 'analysis complete' },

  // 5 · breath title
  { from: S.title.from, src: 'swoosh-quick.mp3', volume: 0.3, note: 'title card in' },

  // 6 · coverage
  { from: S.coverage.from, src: 'air-woosh-quick.mp3', volume: 0.22, note: 'stem rises in' },
  ...[0, 1, 2, 3, 4].map((k) => ({
    from: S.coverage.from + PILL_INTRO + k * PILL_BEAT,
    src: k % 2 === 0 ? 'switch-tap.mp3' : 'switch-click-quick.mp3',
    volume: 0.28 - k * 0.025,
    note: `pill slot turns (${k + 1}/5)`,
  })),
  { from: S.coverage.from + PILL_INTRO + 5 * PILL_BEAT, src: 'transition-snap.mp3', volume: 0.35, note: 'the closing phrase lands' },

  // 7 · brand: riser → impact → sparkle (§4.3)
  { from: S.brand.from, src: 'transition-soft.mp3', volume: 0.3, note: 'the composer returns (fills the gap after the coverage finale)' },
  { from: S.brand.from + 22, src: 'click-camera.mp3', volume: 0.55, note: 'send pressed' },
  { from: S.brand.from + 26, src: 'sweep-fast.mp3', volume: 0.3, note: 'the question flies off' },
  { from: S.brand.from + 34, src: 'air-woosh-deep.mp3', volume: 0.3, note: 'the composer morphs into the disc' },
  { from: S.brand.from + 56, src: 'camera-shutter-vintage.mp3', volume: 0.32, note: 'aperture blades, pair 1' },
  { from: S.brand.from + 70, src: 'camera-shutter-hard.mp3', volume: 0.28, note: 'aperture blades, pair 2' },
  // Riser runs right up to the impact's hit and fades over its last 8 f instead of being cut dry
  // while still loud (review: a −31 dB hole opened just before the hit).
  { from: S.brand.from + 62, src: 'light-aura.mp3', volume: 0.32, duration: 46, fadeTo: S.brand.from + 108, fadeLen: 8, note: 'riser into the settle' },
  { from: S.brand.from + 84, src: 'camera-shutter-vintage.mp3', volume: 0.25, note: 'aperture blades, pair 3' },
  // impact-deep-whoosh has a ~14 f lead-in before its hit; pinned 14 f early so the hit lands ON
  // the settle (f+104), not on the lift that follows (review: it peaked ~16 f late).
  { from: S.brand.from + 90, src: 'impact-deep-whoosh.mp3', volume: 0.55, fadeTo: TOTAL_FRAMES, fadeLen: 24, note: 'logo settles — the film’s loudest beat; its long tail fades out with the film instead of being cut dry' },
  { from: S.brand.from + 128, src: 'sparkle-touch.mp3', volume: 0.3, note: 'wordmark — the afterglow' },
];

export const SoundTrack: React.FC = () => (
  <>
    {SFX.map((s, i) => (
      <Sequence key={i} from={s.from} durationInFrames={s.duration ?? 150} name={s.note}>
        <Audio
          src={staticFile(`audio/${s.src}`)}
          volume={
            s.fadeTo === undefined
              ? s.volume * MASTER
              : (f) => interpolate(f, [s.fadeTo! - s.from - (s.fadeLen ?? 24), s.fadeTo! - s.from], [s.volume * MASTER, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' })
          }
        />
      </Sequence>
    ))}
  </>
);
