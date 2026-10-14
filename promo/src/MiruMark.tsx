// Vector rebuild of web/public/logo.png (the orange camera-aperture mark), so it can be assembled
// piece by piece in the outro. Geometry measured off the 1024px PNG (disc radius R ≈ 462px):
// six straight blade gaps, each tangent-offset d ≈ 0.33R from the centre and ≈ 0.08R wide,
// rotated 60° apart into a pinwheel; a centre circle ≈ 0.2R. Gaps and centre use the
// colour of the background, so it knocks out exactly like LogoImage (the real PNG) it crossfades into.
import React from 'react';
import { C } from './theme';

const D = 0.33; // gap offset from centre, in R
const W = 0.08; // gap width, in R
const START = -0.06; // where a gap starts along its line (just before the tangent point), in R
const CORE = 0.22; // centre circle radius, in R

// blades: per-gap reveal 0..1 (the gap grows outward from its inner end); core: centre 0..1.
export const MiruMark: React.FC<{
  size: number;
  blades?: number[];
  core?: number;
  spin?: number; // degrees
  disc?: number; // disc scale 0..1
  glow?: number;
}> = ({ size, blades = [1, 1, 1, 1, 1, 1], core = 1, spin = 0, disc = 1, glow = 0 }) => {
  const R = 100;
  const clipId = `miru-disc-${React.useId().replace(/[^a-zA-Z0-9]/g, '')}`; // safe inside url(#…)
  const gapLen = Math.sqrt(1 - D * D) - START + 0.05; // runs past the rim, clipped by the disc
  return (
    <svg
      width={size}
      height={size}
      viewBox="-100 -100 200 200"
      style={{ overflow: 'visible', filter: glow > 0 ? `drop-shadow(0 0 ${30 * glow}px rgba(243,112,33,${0.5 * glow}))` : undefined }}
    >
      <defs>
        <clipPath id={clipId}>
          <circle r={R * disc} />
        </clipPath>
      </defs>
      <g transform={`rotate(${spin})`}>
        <circle r={R * disc} fill={C.logo} />
        <g clipPath={`url(#${clipId})`}>
          {blades.map((b, k) => {
            if (b <= 0) return null;
            const len = gapLen * b;
            return (
              <rect
                key={k}
                x={START * R}
                y={-D * R - (W * R) / 2}
                width={len * R}
                height={W * R}
                fill={C.bg}
                transform={`rotate(${k * 60})`}
              />
            );
          })}
        </g>
        {core > 0 && <circle r={CORE * R * core} fill={C.bg} />}
      </g>
    </svg>
  );
};
