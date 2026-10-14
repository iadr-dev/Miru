// Shot 1 — flying-words (demos/typography/flying-words/FlyingWords.tsx), reskinned.
// Kept from the demo: golden-angle layout, z −1750→800 with perspective 1100, drift 0.5+u·1.35,
// life curve [0,1,0.5,0.2,0]@[0,0.25,0.6,0.85,1], near blur after u>0.86, integer CYCLES=2,
// 22 words always mounted (only transform/opacity change), the centre glow breathing twice.
// Changed for Miru: warm cream/etoupe words instead of blue-violet, signed moves in --up/--down
// with ▲/▼ (governance: never colour alone), the orange accent glow, and CJK font sizes reduced as
// the card's known-pitfall note requires. Runs under shot 2 as a dimmed, blurred backdrop.
import React from 'react';
import { useCurrentFrame } from 'remotion';
import { DesignStage } from '../DesignStage';
import type { Lang } from '../copy';
import { lerp, rand, seg, E } from '../motion';
import { ACCENT_RGB, C, F } from '../theme';

const OP = [0, 1, 0.5, 0.2, 0];
const OT = [0, 0.25, 0.6, 0.85, 1];
const curve = (u: number) => {
  for (let k = 0; k < 4; k++) {
    if (u <= OT[k + 1]) {
      const p = (u - OT[k]) / (OT[k + 1] - OT[k]);
      return OP[k] + (OP[k + 1] - OP[k]) * p;
    }
  }
  return 0;
};

const CYCLES = 2;
// The demo's loop length: one t-unit = 180 frames. Kept so words travel at the tuned speed.
const LOOP = 180;

type Signed = 'up' | 'down' | null;
const signOf = (w: string): Signed => (w.startsWith('+') ? 'up' : w.startsWith('–') ? 'down' : null);

export const NoiseTunnel: React.FC<{
  words: string[];
  lang: Lang;
  dimFrom: number; // local frame where the tunnel starts receding behind the composer
  dimDur: number;
}> = ({ words, lang, dimFrom, dimDur }) => {
  const frame = useCurrentFrame();
  const t = frame / (LOOP - 1);
  const N = words.length;
  const cjk = lang === 'zh-TW';
  const recede = seg(frame, dimFrom, dimFrom + dimDur, E.inOutCubic);

  return (
    <DesignStage bg={`radial-gradient(60% 60% at 50% 50%, ${C.surface}, ${C.bg} 75%)`}>
      <div
        style={{
          position: 'absolute',
          inset: 0,
          perspective: 1100,
          overflow: 'hidden',
          opacity: lerp(recede, 1, 0.22),
          filter: recede > 0 ? `blur(${recede * 3}px)` : 'none',
        }}
      >
        <div
          style={{
            position: 'absolute',
            left: '50%',
            top: '50%',
            width: 0,
            height: 0,
            transformStyle: 'preserve-3d',
          }}
        >
          {words.map((w, i) => {
            const a = i * 2.39996 + rand(i * 7 + 1) * 0.8;
            const r = 82 + rand(i * 13 + 2) * 165;
            const base = cjk ? 15 + rand(i + 3) * 11 : 18 + rand(i + 3) * 14;
            const sign = signOf(w);
            const light = 74 + rand(i + 9) * 16;
            const color =
              sign === 'up' ? C.up : sign === 'down' ? C.down : `hsl(32, ${18 + rand(i + 5) * 22}%, ${light}%)`;
            const glow =
              sign === 'up'
                ? 'rgba(240,72,62,.40)'
                : sign === 'down'
                  ? 'rgba(31,181,116,.40)'
                  : `rgba(${ACCENT_RGB},.28)`;
            const label = sign === 'up' ? `▲ ${w}` : sign === 'down' ? `▼ ${w}` : w;
            const u = (t * CYCLES + i / N) % 1;
            const z = lerp(u, -1750, 800);
            const drift = 0.5 + u * 1.35;
            const x = Math.cos(a) * r;
            const y = Math.sin(a) * r * 0.6;
            const rz = (rand(i + 21) - 0.5) * 14;
            return (
              <div
                key={i}
                style={{
                  position: 'absolute',
                  left: 0,
                  top: 0,
                  transformOrigin: '50% 50%',
                  whiteSpace: 'nowrap',
                  fontWeight: 700,
                  fontSize: base,
                  lineHeight: 1,
                  fontFamily: sign ? F.mono : F.sans,
                  fontVariantNumeric: 'tabular-nums',
                  letterSpacing: '0.5px',
                  color,
                  textShadow: `0 0 16px ${glow}`,
                  margin: '-14px 0 0 -60px',
                  transform: `translate3d(${x * drift}px,${y * drift}px,${z}px) rotateZ(${rz}deg)`,
                  opacity: curve(u),
                  filter: u > 0.86 ? `blur(${(u - 0.86) * 26}px)` : 'none',
                }}
              >
                {label}
              </div>
            );
          })}
        </div>
        <div
          style={{
            position: 'absolute',
            left: '50%',
            top: '50%',
            width: 220,
            height: 220,
            margin: -110,
            borderRadius: '50%',
            background: `radial-gradient(circle, rgba(${ACCENT_RGB},.20), transparent 68%)`,
            filter: 'blur(6px)',
            opacity: 0.75 + Math.sin(t * Math.PI * 4) * 0.12,
          }}
        />
      </div>
    </DesignStage>
  );
};
