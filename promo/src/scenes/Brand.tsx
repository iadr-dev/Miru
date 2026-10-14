// Shot 7 — ui-to-brand-morph · input-morph-assemble (demos/outro/ui-to-brand-morph/
// InputMorphsIntoLogo.tsx). Kept: cursor in over 22 f ease-out, press scale 0.82 + send-button
// flash 6 f, the text flying off up-right with Easing.in over 12 f (+700,−380, −10°) — accelerating,
// as the card requires — the box→shape morph from 34 f driven by ONE spring (damping 13,
// stiffness 90, mass 0.9) for x/y/w/h/r together while the outline fills solid, three staggered
// arrivals at 56/70/84 f each on spring(damping 12, stiffness 110), then a breathing settle.
// Adapted to Miru's logo (the card: "replace with your own logo's primitive breakdown, 3–4 pieces"):
// the box morphs into the aperture disc; the three arrivals are three pairs of aperture gaps slicing
// in; the centre opens; the vector then crossfades into the exact knocked-out logo.png. Added after
// the morph (the card has no wordmark): the logo lifts into a vertical lockup, the wordmark settles
// letter by letter big→small with blur→sharp (the card's own recommended wordmark treatment), the
// tagline follows, and the lockup holds well over 1 s (R1).
import React from 'react';
import { AbsoluteFill, Easing, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import type { Lang } from '../copy';
import { LogoImage } from '../LogoImage';
import { MiruMark } from '../MiruMark';
import { ACCENT_RGB, C, F } from '../theme';

const CX = 960;
const CY = 540;
const CLICK = 22;
const FLY = 26;
const MORPH = 34;
const DROPS = [56, 70, 84];
const CORE = 92;
const SETTLE = 104;
const LIFT = 118;
const WORD = 128;
const TAG = 146;

const clamp = { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' } as const;

export const Brand: React.FC<{ hint: string; wordmark: string; tagline: string; lang: Lang }> = ({
  hint,
  wordmark,
  tagline,
  lang,
}) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const cjk = lang === 'zh-TW';

  const box0 = { w: 1100, h: 132, r: 66 };
  const DISC = 360;

  const cursorX = interpolate(f, [0, CLICK], [1560, CX + box0.w / 2 - 70], { ...clamp, easing: Easing.out(Easing.cubic) });
  const cursorY = interpolate(f, [0, CLICK], [920, CY + 6], { ...clamp, easing: Easing.out(Easing.cubic) });
  const press = f >= CLICK && f <= CLICK + 4 ? 0.82 : 1;
  const cursorGone = interpolate(f, [FLY + 4, FLY + 12], [1, 0], clamp);
  const btnFlash = f >= CLICK && f <= CLICK + 6;
  const flyT = interpolate(f, [FLY, FLY + 12], [0, 1], { ...clamp, easing: Easing.in(Easing.cubic) });

  const m = spring({ frame: f - MORPH, fps, config: { damping: 13, stiffness: 90, mass: 0.9 } });
  const bw = interpolate(m, [0, 1], [box0.w, DISC]);
  const bh = interpolate(m, [0, 1], [box0.h, DISC]);
  const br = interpolate(m, [0, 1], [box0.r, DISC / 2]);

  const drop = (i: number) => spring({ frame: f - DROPS[i], fps, config: { damping: 12, stiffness: 110, mass: 0.85 } });
  const pairs = [drop(0), drop(1), drop(2)];
  // gap k and its opposite k+3 arrive together: [0,3], [1,4], [2,5]
  const blades = [pairs[0], pairs[1], pairs[2], pairs[0], pairs[1], pairs[2]].map((v) => (f >= DROPS[0] ? v : 0));
  const core = spring({ frame: f - CORE, fps, config: { damping: 14, stiffness: 120 } });
  const spinIn = interpolate(pairs[2], [0, 1], [-24, 0]); // the blades settle with a last quarter-turn

  // The exact logo fades in ON TOP of the still-opaque vector over 4 f, then the vector drops out.
  // (A 10 f mutual crossfade doubled the blades and dimmed the disc ~12% — review f1003–1013.)
  const toReal = interpolate(f, [SETTLE, SETTLE + 4], [0, 1], clamp);
  const underneath = toReal < 1 ? 1 : 0;
  const breathe = f >= SETTLE && f < LIFT ? 1 + 0.03 * Math.sin((f - SETTLE) * 0.18) : 1;
  const lift = interpolate(f, [LIFT, LIFT + 18], [0, 1], { ...clamp, easing: Easing.bezier(0.4, 0, 0.6, 1) });
  const markSize = interpolate(lift, [0, 1], [DISC, 250]);
  const markY = interpolate(lift, [0, 1], [CY, 380]);

  const letters = Array.from(wordmark);
  const tagT = interpolate(f, [TAG, TAG + 16], [0, 1], { ...clamp, easing: Easing.out(Easing.cubic) });
  const morphing = m < 0.995 || f < DROPS[0];

  return (
    <AbsoluteFill style={{ background: C.bg, overflow: 'hidden' }}>
      <AbsoluteFill style={{ background: `radial-gradient(40% 46% at 50% 46%, rgba(${ACCENT_RGB},${0.1 * m}), transparent 72%)` }} />

      {/* The composer box, morphing into the disc */}
      {f < SETTLE + 10 && (
        <div
          style={{
            position: 'absolute',
            left: CX - bw / 2,
            top: (f < LIFT ? CY : markY) - bh / 2,
            width: bw,
            height: bh,
            borderRadius: br,
            boxSizing: 'border-box',
            border: `3px solid rgba(${ACCENT_RGB},${0.9 - 0.9 * m})`,
            background: m > 0.02 ? `rgba(243,112,33,${m})` : C.surface,
            boxShadow: `0 0 ${40 * (1 - m)}px rgba(${ACCENT_RGB},.28)`,
            display: 'flex',
            alignItems: 'center',
            padding: '0 34px 0 48px',
            transform: `scale(${breathe})`,
            opacity: morphing ? 1 : underneath,
            willChange: 'transform', // one antialiasing mode throughout (review: AA switched mid-shot)
          }}
        >
          <div
            style={{
              fontFamily: F.sans,
              fontSize: 46,
              color: C.text,
              whiteSpace: 'nowrap',
              opacity: (1 - flyT) * (1 - m),
              transform: `translate(${flyT * 700}px, ${-flyT * 380}px) rotate(${-flyT * 10}deg)`,
            }}
          >
            {hint}
          </div>
          <div
            style={{
              marginLeft: 'auto',
              width: 76,
              height: 76,
              borderRadius: 999,
              background: btnFlash ? C.text : C.accent,
              opacity: 1 - m,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              transform: `scale(${press})`,
              flexShrink: 0,
            }}
          >
            <svg width={34} height={34} viewBox="0 0 24 24" fill="none" stroke="#2a1304" strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 19V5M5.5 11.5 12 5l6.5 6.5" />
            </svg>
          </div>
        </div>
      )}

      {/* The aperture gaps slicing into the disc, then the exact logo */}
      {f >= DROPS[0] && (
        <div style={{ position: 'absolute', left: CX - markSize / 2, top: markY - markSize / 2, width: markSize, height: markSize, transform: `scale(${breathe})` }}>
          <div style={{ position: 'absolute', inset: 0, opacity: underneath }}>
            <MiruMark size={markSize} blades={blades} core={Math.max(0, core)} spin={spinIn} disc={Math.min(1, Math.min(bw, bh) / DISC)} />
          </div>
          <div style={{ position: 'absolute', inset: `${-markSize * 0.054}px`, opacity: toReal }}>
            {/* logo.png's disc spans 924/1024 of the image; inset so the discs coincide. */}
            <LogoImage size={(markSize * 1024) / 924} />
          </div>
        </div>
      )}

      {/* Wordmark: each letter settles big→small, blur→sharp */}
      <div style={{ position: 'absolute', left: 0, right: 0, top: 560, display: 'flex', justifyContent: 'center' }}>
        {letters.map((ch, i) => {
          const p = interpolate(f, [WORD + i * 4, WORD + i * 4 + 14], [0, 1], { ...clamp, easing: Easing.out(Easing.cubic) });
          return (
            <span
              key={i}
              style={{
                display: 'inline-block',
                fontFamily: F.serif,
                fontWeight: 600,
                fontSize: 150,
                lineHeight: 1,
                color: C.text,
                letterSpacing: '-0.01em',
                opacity: p,
                transform: `scale(${interpolate(p, [0, 1], [1.6, 1])})`,
                filter: `blur(${(1 - p) * 14}px)`,
              }}
            >
              {ch}
            </span>
          );
        })}
      </div>
      <div
        style={{
          position: 'absolute',
          left: 0,
          right: 0,
          top: 752,
          textAlign: 'center',
          fontFamily: F.sans,
          fontWeight: cjk ? 500 : 400,
          fontSize: 50,
          letterSpacing: cjk ? '0.06em' : '0.01em',
          color: C.muted,
          opacity: tagT,
          transform: `translateY(${(1 - tagT) * 14}px)`,
        }}
      >
        {tagline}
      </div>

      {/* Cursor */}
      <div style={{ position: 'absolute', left: cursorX, top: cursorY, opacity: cursorGone, transform: `scale(${press})` }}>
        <svg width={44} height={48} viewBox="0 0 40 44">
          <path d="M4 2 L4 34 L13 26 L19 40 L26 37 L20 23 L32 22 Z" fill={C.text} stroke={C.bg} strokeWidth={2} />
        </svg>
      </div>
    </AbsoluteFill>
  );
};
