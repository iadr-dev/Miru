// Shot 3 — bezier-source-converge-merge (demos/ui-entrance/bezier-source-converge-merge/
// BezierSourceConvergeMerge.tsx), reskinned to ebony. Kept exactly: the four paths
// M −22,y L 74,y C 186,y 214,120 332,120 (y = 36/92/148/204), arc-length tables (no DOM
// measuring), staggered draw-on 0.04+i·0.045 over 0.17 outQuad, nodes travelling the real curve
// with conv = seg(0.34→0.74, inOutCubic), the three-stage shrink 44→15→0 with the 0.75 knee,
// two integer packet cycles with i·0.13 phase, erase 0.78→0.9 backwards, the badge pulse.
// Changed: cream hairlines on ebony, warm surface nodes with drawn source icons (the card:
// "replace the S1–S4 placeholders with real sources; keep accent for packets and the badge"),
// source labels beside each node (they fade as the node leaves), the badge is the Miru mark,
// and the closing caption is centred at the Q11 narration size, keeping the word-by-word deepen.
import React from 'react';
import { useCurrentFrame } from 'remotion';
import { DesignStage } from '../DesignStage';
import { LogoImage } from '../LogoImage';
import { E, lerp, seg, tOf } from '../motion';
import { C, F } from '../theme';

const XC = 332;
const YC = 120;
const YS = [36, 92, 148, 204];
const clamp01 = (v: number) => (v < 0 ? 0 : v > 1 ? 1 : v);

type Pt = { x: number; y: number };
const cubic = (y0: number, u: number): Pt => {
  const v = 1 - u;
  return {
    x: v * v * v * 74 + 3 * v * v * u * 186 + 3 * v * u * u * 214 + u * u * u * XC,
    y: v * v * v * y0 + 3 * v * v * u * y0 + 3 * v * u * u * YC + u * u * u * YC,
  };
};
const SAMPLES = 1600;
const LINE_LEN = 96;
const mkPathGeom = (y0: number) => {
  const cum: number[] = [0];
  let px = 74;
  let py = y0;
  let acc = 0;
  for (let k = 1; k <= SAMPLES; k++) {
    const p = cubic(y0, k / SAMPLES);
    acc += Math.hypot(p.x - px, p.y - py);
    cum.push(acc);
    px = p.x;
    py = p.y;
  }
  const len = LINE_LEN + acc;
  const pointAt = (s: number): Pt => {
    const sc = Math.max(0, Math.min(len, s));
    if (sc <= LINE_LEN) return { x: -22 + sc, y: y0 };
    const target = sc - LINE_LEN;
    let lo = 0;
    let hi = SAMPLES;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (cum[mid] < target) lo = mid + 1;
      else hi = mid;
    }
    const i = Math.max(1, lo);
    const s0 = cum[i - 1];
    const s1 = cum[i];
    const u = (i - 1 + (s1 > s0 ? (target - s0) / (s1 - s0) : 0)) / SAMPLES;
    return cubic(y0, u);
  };
  let f0 = 0.2;
  for (let k = 1; k <= 40; k++) {
    const q = k / 40;
    if (pointAt(len * q).x >= 74) {
      f0 = q;
      break;
    }
  }
  return { len, pointAt, f0 };
};
const GEOMS = YS.map(mkPathGeom);

// Source icons, drawn in a 24-unit box with the cream hairline weight (price · flows · news · earnings).
const Icon: React.FC<{ k: number; size: number }> = ({ k, size }) => {
  const s = { fill: 'none', stroke: C.text, strokeWidth: 1.8, strokeLinecap: 'round', strokeLinejoin: 'round' } as const;
  return (
    <svg width={size} height={size} viewBox="0 0 24 24">
      {k === 0 && <polyline {...s} points="3,17 8,11 12,14 17,7 21,9" />}
      {k === 1 && (
        <g {...s}>
          <path d="M5 9h12l-3-3" />
          <path d="M19 15H7l3 3" />
        </g>
      )}
      {k === 2 && (
        <g {...s}>
          <rect x="5" y="4" width="14" height="16" rx="2" />
          <path d="M8 9h8M8 12.5h8M8 16h5" />
        </g>
      )}
      {k === 3 && (
        <g {...s}>
          <path d="M5 19V13M10 19V9M15 19V11M20 19V5" />
        </g>
      )}
    </svg>
  );
};

const mix = (p: number, a: [number, number, number], b: [number, number, number]) => {
  const q = clamp01(p);
  return `rgb(${Math.round(a[0] + (b[0] - a[0]) * q)},${Math.round(a[1] + (b[1] - a[1]) * q)},${Math.round(a[2] + (b[2] - a[2]) * q)})`;
};
const DIM: [number, number, number] = [76, 66, 56]; // --border-strong
const TXT: [number, number, number] = [244, 237, 227]; // --text

export const Converge: React.FC<{
  sources: string[];
  caption: string;
  captionWords: string[];
  duration: number;
}> = ({ sources, captionWords, duration }) => {
  const frame = useCurrentFrame();
  const t = tOf(frame, duration);
  const conv = seg(t, 0.34, 0.74, E.inOutCubic);
  const erase = seg(t, 0.78, 0.9, E.outQuad);
  const pkCycle = (seg(t, 0.1, 0.74, E.linear) * 2) % 1;
  const bp = seg(t, 0.16, 0.26, E.outCubic);
  const badgeScale =
    lerp(bp, 0.7, 1) * (1 + seg(t, 0.7, 0.78, E.outBack) * 0.12 - seg(t, 0.78, 0.86, E.outQuad) * 0.12);
  const capShow = seg(t, 0.84, 0.9);
  const capInn = seg(t, 0.84, 1);
  const capSt = 0.78 / captionWords.length;
  const capWin = capSt * 1.5;
  const BADGE = 34;
  // After the merge, as the paths erase, the frame glides so the mark (and its caption) settles at
  // centre — one motivated move once the sources are gone (design x 352 → 240).
  const glide = seg(t, 0.8, 0.96, E.inOutCubic) * (20 + XC - 240);

  return (
    <DesignStage raster="zoom">
      <div style={{ position: 'absolute', inset: 0, transform: `translateX(${-glide}px)` }}>
      <div style={{ position: 'absolute', left: '50%', top: '46%', width: 440, height: 240, margin: '-120px 0 0 -220px' }}>
        <svg width={440} height={240} viewBox="0 0 440 240" style={{ position: 'absolute', left: 0, top: 0, overflow: 'visible' }}>
          {YS.map((y, i) => {
            const { len } = GEOMS[i];
            const draw = seg(t, 0.04 + i * 0.045, 0.04 + i * 0.045 + 0.17, E.outQuad);
            const off = erase > 0 ? -erase * len : len * (1 - draw);
            return (
              <path
                key={i}
                d={`M -22,${y} L 74,${y} C 186,${y} 214,${YC} ${XC},${YC}`}
                fill="none"
                stroke={C.text}
                strokeOpacity={0.55}
                strokeWidth={1.2}
                strokeDasharray={len}
                strokeDashoffset={off.toFixed(1)}
                opacity={(draw * (1 - clamp01((erase - 0.85) / 0.15))).toFixed(3)}
              />
            );
          })}
        </svg>

        <div style={{ position: 'absolute', left: 0, top: 0, width: 440, height: 240 }}>
          {YS.map((y, i) => {
            const { len, pointAt, f0 } = GEOMS[i];
            const tt = conv;
            const pt = pointAt(len * (f0 + (1 - f0) * tt));
            const size = tt < 0.75 ? lerp(tt / 0.75, 44, 15) : lerp((tt - 0.75) / 0.25, 15, 0);
            const appear = seg(t, 0.02 + i * 0.04, 0.02 + i * 0.04 + 0.1, E.outCubic);
            const pf = f0 + (1 - f0) * ((pkCycle + i * 0.13) % 1);
            const q = pointAt(len * pf);
            const pkOn = seg(t, 0.1, 0.16) * (1 - seg(t, 0.7, 0.76));
            const labelO = appear * (1 - seg(tt, 0, 0.18));
            return (
              <React.Fragment key={i}>
                {/* Source label, left of the resting node; it lets go as the node leaves. */}
                <div
                  style={{
                    position: 'absolute',
                    right: 440 - 74 + 26,
                    top: y - 9,
                    font: `500 14px ${F.sans}`,
                    color: C.text,
                    whiteSpace: 'nowrap',
                    // ebony backing: the straight lead-in line stops at the label instead of striking it
                    background: C.bg,
                    padding: '0 5px',
                    opacity: labelO,
                    transform: `translateX(${(1 - appear) * -6}px)`,
                  }}
                >
                  {sources[i]}
                </div>
                <div
                  style={{
                    position: 'absolute',
                    left: 0,
                    top: 0,
                    borderRadius: '50%',
                    background: C.surface2,
                    border: `1px solid ${C.borderStrong}`,
                    boxSizing: 'border-box',
                    boxShadow: '0 3px 12px rgba(0,0,0,.35)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    width: Math.max(0.1, size),
                    height: Math.max(0.1, size),
                    transform: `translate(${(pt.x - size / 2).toFixed(2)}px,${(pt.y - size / 2).toFixed(2)}px) scale(${appear.toFixed(3)})`,
                    opacity: (appear * (tt > 0.92 ? clamp01((1 - tt) / 0.08) : 1)).toFixed(3),
                  }}
                >
                  <Icon k={i} size={Math.max(1, size * 0.5)} />
                </div>
                <div
                  style={{
                    position: 'absolute',
                    left: 0,
                    top: 0,
                    width: 7,
                    height: 7,
                    borderRadius: '50%',
                    border: `1.5px solid ${C.accent}`,
                    boxSizing: 'border-box',
                    background: 'rgba(242,135,58,.16)',
                    transform: `translate(${(q.x - 3.5).toFixed(2)}px,${(q.y - 3.5).toFixed(2)}px)`,
                    opacity: (pkOn * (1 - Math.abs(((pkCycle + i * 0.13) % 1) - 0.5) * 0.6)).toFixed(3),
                  }}
                />
              </React.Fragment>
            );
          })}
        </div>

        <div
          style={{
            position: 'absolute',
            width: BADGE,
            height: BADGE,
            left: XC - BADGE / 2,
            top: YC - BADGE / 2,
            opacity: bp,
            transform: `scale(${badgeScale})`,
          }}
        >
          {/* Solid backing: the knocked-out logo's open gaps and centre would otherwise show the path
              ends and incoming nodes through the mark (review: f330–435). The demo's badge was solid. */}
          <div style={{ position: 'absolute', inset: 0, borderRadius: '50%', background: C.bg }} />
          {/* The exact logo (logo.png's disc is 924/1024 of the image: offset so the discs coincide). */}
          <div style={{ position: 'absolute', inset: -BADGE * 0.054, filter: `drop-shadow(0 0 ${7 * seg(t, 0.7, 0.78) * (1 - seg(t, 0.86, 1))}px rgba(243,112,33,.55))` }}>
            <LogoImage size={(BADGE * 1024) / 924} />
          </div>
        </div>
      </div>

      {/* Caption under the mark (the card places it by the badge), centred on the mark's x. */}
      <div
        style={{
          position: 'absolute',
          left: 240 - 220 + XC,
          top: 218,
          transform: 'translateX(-50%)',
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'baseline',
          whiteSpace: 'nowrap',
          opacity: capShow,
        }}
      >
        {captionWords.map((w, i) => {
          const q = clamp01((capInn - i * capSt) / capWin);
          return (
            <span
              key={i}
              style={{
                font: `500 14.5px/1.25 ${F.sans}`,
                color: mix(q, DIM, TXT),
                letterSpacing: `${(-0.03 * (1 - q)).toFixed(4)}em`,
                marginRight: i === captionWords.length - 1 ? 0 : 4.5,
              }}
            >
              {w}
            </span>
          );
        })}
      </div>
      </div>
    </DesignStage>
  );
};
