// Shot 6 — pill-slot-cycle (demos/typography/pill-slot-cycle/PillSlotCycle.tsx). Kept exactly:
// the stem absolutely anchored at a fixed left edge (never flex-centred — the card's verdict),
// 12 f stem intro, 21 f per beat with an 8 f swap, incoming +120→0 with blur 14→4→0 ease-out,
// outgoing 0→−130 with blur 0→10 ease-in, the hidden sizer pill, the finale's last pill leaving
// in 7 f and the closing phrase landing with Easing.back(1.4) over 14 f — the only overshoot —
// at the same size/weight as the stem, then a ≥45 f hold.
// Changed: Miru tokens (warm surface pill, cream type), drawn icons instead of text glyphs, the
// stem's left anchor set per language so the longest state sits centred.
import React from 'react';
import { AbsoluteFill, Easing, interpolate, useCurrentFrame } from 'remotion';
import type { Lang } from '../copy';
import { C, F } from '../theme';

const BEAT = 21;
const INTRO = 12;
const SWAP = 8;

const PillIcon: React.FC<{ k: number }> = ({ k }) => {
  const s = { fill: 'none', stroke: C.text, strokeWidth: 1.8, strokeLinecap: 'round', strokeLinejoin: 'round' } as const;
  if (k <= 1) {
    return (
      <span style={{ fontFamily: F.mono, fontSize: 26, fontWeight: 600, color: C.accent, letterSpacing: '0.02em' }}>
        {k === 0 ? 'TW' : 'US'}
      </span>
    );
  }
  return (
    <svg width={34} height={34} viewBox="0 0 24 24">
      {k === 2 && (
        <g {...s}>
          <path d="M12 3 3 8l9 5 9-5-9-5Z" />
          <path d="m3 13 9 5 9-5" />
        </g>
      )}
      {k === 3 && (
        <g {...s}>
          <path d="M20 12a8 8 0 0 1-13.7 5.7M4 12a8 8 0 0 1 13.7-5.7" />
          <path d="M18 3v4h-4M6 21v-4h4" />
        </g>
      )}
      {k === 4 && (
        <g {...s}>
          <path d="M5 9h12l-3-3" />
          <path d="M19 15H7l3 3" />
        </g>
      )}
    </svg>
  );
};

const Pill: React.FC<{ label: string; k: number; style?: React.CSSProperties }> = ({ label, k, style }) => (
  <div
    style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: 18,
      padding: '14px 38px 14px 22px',
      borderRadius: 999,
      background: C.surface2,
      border: `3px solid ${C.borderStrong}`,
      boxShadow: '0 10px 30px rgba(0,0,0,.35)',
      fontFamily: F.sans,
      fontWeight: 600,
      fontSize: 64,
      color: C.text,
      whiteSpace: 'nowrap',
      ...style,
    }}
  >
    <span
      style={{
        width: 62,
        height: 62,
        borderRadius: 16,
        background: C.accentWeak,
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        flexShrink: 0,
      }}
    >
      <PillIcon k={k} />
    </span>
    {label}
  </div>
);

export const Coverage: React.FC<{ stem: string; pills: string[]; finale: string; lang: Lang }> = ({
  stem,
  pills,
  finale,
  lang,
}) => {
  const frame = useCurrentFrame();
  const cycles = pills.length;
  const stemT = interpolate(frame, [0, INTRO], [0, 1], { extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic) });
  const cycleEnd = INTRO + cycles * BEAT;
  const rel = frame - INTRO;
  const idx = Math.min(Math.floor(rel / BEAT), cycles - 1);
  const beatFrame = rel - idx * BEAT;
  const isFinale = frame >= cycleEnd;
  const finT = interpolate(frame, [cycleEnd, cycleEnd + 14], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.back(1.4)),
  });
  const cjk = lang === 'zh-TW';
  const stemStyle: React.CSSProperties = {
    fontFamily: cjk ? F.sans : F.serif,
    fontWeight: cjk ? 600 : 500,
    fontSize: 96,
    color: C.text,
    letterSpacing: cjk ? '0.02em' : '-1px',
    whiteSpace: 'nowrap',
  };

  let slot: React.ReactNode = null;
  if (!isFinale && rel >= 0) {
    const incoming = pills[idx];
    const outgoing = idx > 0 ? pills[idx - 1] : null;
    const inT = interpolate(beatFrame, [0, SWAP], [0, 1], { extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic) });
    const inY = interpolate(inT, [0, 1], [120, 0]);
    const inBlur = interpolate(inT, [0, 0.7, 1], [14, 4, 0]);
    const outT = interpolate(beatFrame, [0, SWAP - 1], [0, 1], { extrapolateRight: 'clamp', easing: Easing.in(Easing.cubic) });
    const outY = interpolate(outT, [0, 1], [0, -130]);
    slot = (
      <div style={{ position: 'relative', display: 'inline-block' }}>
        <Pill label={incoming} k={idx} style={{ visibility: 'hidden' }} />
        {outgoing !== null && outT < 1 && (
          <div style={{ position: 'absolute', left: 0, top: 0, transform: `translateY(${outY}px)`, opacity: 1 - outT, filter: `blur(${outT * 10}px)` }}>
            <Pill label={outgoing} k={idx - 1} />
          </div>
        )}
        <div
          style={{
            position: 'absolute',
            left: 0,
            top: 0,
            transform: `translateY(${inY}px)`,
            opacity: idx === 0 ? inT : Math.min(1, inT * 1.6),
            filter: `blur(${inBlur}px)`,
          }}
        >
          <Pill label={incoming} k={idx} />
        </div>
      </div>
    );
  } else if (isFinale) {
    const lastOutT = interpolate(frame, [cycleEnd, cycleEnd + 7], [0, 1], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
      easing: Easing.in(Easing.cubic),
    });
    slot = (
      <div style={{ position: 'relative', display: 'inline-block' }}>
        <span
          style={{
            ...stemStyle,
            color: C.accent,
            display: 'inline-block',
            opacity: finT,
            transform: `translateY(${(1 - finT) * 90}px)`,
            filter: `blur(${(1 - finT) * 8}px)`,
          }}
        >
          {finale}
        </span>
        {lastOutT < 1 && (
          <div
            style={{
              position: 'absolute',
              left: 0,
              top: -8,
              transform: `translateY(${-130 * lastOutT}px)`,
              opacity: 1 - lastOutT,
              filter: `blur(${lastOutT * 10}px)`,
            }}
          >
            <Pill label={pills[cycles - 1]} k={cycles - 1} />
          </div>
        )}
      </div>
    );
  }

  return (
    <AbsoluteFill>
      <div
        style={{
          position: 'absolute',
          left: cjk ? 440 : 190,
          top: 540,
          transform: `translateY(calc(-50% + ${(1 - stemT) * 50}px))`,
          display: 'flex',
          alignItems: 'center',
          gap: 36,
          opacity: stemT,
        }}
      >
        <span style={stemStyle}>{stem}</span>
        {slot}
      </div>
    </AbsoluteFill>
  );
};
