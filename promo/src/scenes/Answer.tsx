// Shot 4 — ai-stream-response (demos/interaction/ai-stream-response/StreamResponse.tsx).
// Kept from the demo: panel in over 0–16 f with bezier(0.2,0.75,0.25,1); the summary as one
// semantic block (clip wipe + rise, 18→30 f) before any evidence; rows from cue 42 with the
// tightening cadence [0,11,21,30,38]; each row y 18→0 + blur 6→0 over 12 f with the status icon
// lagging 3 f (pending ring → done check); one panel-level completion pulse; ≥15 f of true stillness
// after it; camera 1.04→1.0 front-on (Q6).
// Changed for Miru: five rows of invented market context instead of seven code-review rows, type
// sized to Q11 (summary 44px, rows 32px), the accent orange as the done colour (chrome only), Miru
// header with the mark, and the non-trading caveat row that every Miru answer carries (§4).
// No backplate screenshot: the film is concept-led, so the panel sits on a soft ebony glow.
import React from 'react';
import { AbsoluteFill, Easing, interpolate, useCurrentFrame } from 'remotion';
import type { Copy } from '../copy';
import { LogoImage } from '../LogoImage';
import { C, F } from '../theme';

const ROW_CUES = [42, 53, 63, 72, 80];
const clamp = { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' } as const;
const ease = Easing.bezier(0.2, 0.75, 0.25, 1);

const CheckIcon: React.FC<{ progress: number }> = ({ progress }) => {
  const ringOpacity = interpolate(progress, [0, 0.45, 0.75], [0.36, 1, 0], clamp);
  const checkOpacity = interpolate(progress, [0.62, 1], [0, 1], clamp);
  const checkScale = interpolate(progress, [0.62, 1], [0.72, 1], { ...clamp, easing: ease });
  return (
    <div style={{ position: 'relative', width: 36, height: 36, flex: '0 0 auto' }}>
      <div
        style={{
          position: 'absolute',
          inset: 3,
          borderRadius: 99,
          border: `2.5px solid rgba(169,157,142,.55)`,
          borderTopColor: C.accent,
          opacity: ringOpacity,
          transform: `rotate(${progress * 100}deg)`,
        }}
      />
      <div
        style={{
          position: 'absolute',
          inset: 2,
          borderRadius: 99,
          background: C.accent,
          opacity: checkOpacity,
          transform: `scale(${checkScale})`,
          display: 'grid',
          placeItems: 'center',
        }}
      >
        <svg width="20" height="20" viewBox="0 0 16 16" fill="none">
          <path d="M3.4 8.3 6.6 11l6-6" stroke="#2a1304" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </div>
    </div>
  );
};

const EvidenceRow: React.FC<{ cue: number; title: string; meta: string; index: number }> = ({
  cue,
  title,
  meta,
  index,
}) => {
  const frame = useCurrentFrame();
  const body = interpolate(frame, [cue, cue + 12], [0, 1], { ...clamp, easing: ease });
  const status = interpolate(frame, [cue + 3, cue + 11], [0, 1], { ...clamp, easing: ease });
  return (
    <div
      style={{
        position: 'absolute',
        left: 0,
        right: 0,
        top: index * 80,
        height: 72,
        borderRadius: 16,
        border: `1px solid ${C.border}`,
        background: 'rgba(244,237,227,.025)',
        display: 'flex',
        alignItems: 'center',
        padding: '0 26px',
        gap: 20,
        opacity: body,
        transform: `translateY(${18 * (1 - body)}px)`,
        filter: `blur(${6 * (1 - body)}px)`,
      }}
    >
      <CheckIcon progress={status} />
      <div style={{ fontFamily: F.sans, fontSize: 32, color: C.text, flex: 1, whiteSpace: 'nowrap' }}>{title}</div>
      <div
        style={{
          fontFamily: /\d/.test(meta) ? F.mono : F.sans, // mono is for numbers only
          fontSize: 30,
          fontWeight: 500,
          color: status > 0.78 ? C.text : C.muted,
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {meta}
      </div>
    </div>
  );
};

export const Answer: React.FC<{ copy: Copy }> = ({ copy }) => {
  const frame = useCurrentFrame();
  const panelIn = interpolate(frame, [0, 16], [0, 1], { ...clamp, easing: ease });
  const summary = interpolate(frame, [18, 30], [0, 1], { ...clamp, easing: ease });
  const camera = interpolate(frame, [0, 100], [1.04, 1], { ...clamp, easing: ease });
  const lastDone = ROW_CUES[ROW_CUES.length - 1] + 11;
  const pulse = interpolate(frame, [lastDone + 6, lastDone + 11, lastDone + 16], [0.25, 0.55, 0.25], clamp);
  const complete = interpolate(frame, [lastDone + 6, lastDone + 16], [0, 1], { ...clamp, easing: ease });
  const caveat = interpolate(frame, [lastDone + 12, lastDone + 24], [0, 1], { ...clamp, easing: ease });

  return (
    <AbsoluteFill style={{ background: C.bg, overflow: 'hidden' }}>
      <AbsoluteFill
        style={{
          background: `radial-gradient(48% 52% at 50% 46%, rgba(242,135,58,.10), transparent 70%), radial-gradient(70% 70% at 50% 50%, ${C.surface}, ${C.bg} 80%)`,
        }}
      />
      <AbsoluteFill style={{ transform: `scale(${camera})`, willChange: 'transform' }}>
        <div
          style={{
            position: 'absolute',
            left: 264,
            top: 56,
            width: 1392,
            height: 968,
            borderRadius: 16, // card token
            overflow: 'hidden',
            background: C.surface,
            border: `1px solid rgba(242,135,58,${pulse})`,
            boxShadow: '0 46px 100px rgba(0,0,0,.6), inset 0 1px 0 rgba(255,240,220,.05)',
            opacity: panelIn,
            transform: `translateY(${12 * (1 - panelIn)}px) scale(${0.985 + 0.015 * panelIn})`,
          }}
        >
          {/* Header */}
          <div style={{ height: 92, display: 'flex', alignItems: 'center', padding: '0 40px', borderBottom: `1px solid ${C.border}` }}>
            <LogoImage size={(44 * 1024) / 924} />
            <div style={{ marginLeft: 16, fontFamily: F.serif, fontSize: 34, fontWeight: 600, color: C.text }}>{copy.answerTitle}</div>
            <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 12, color: C.muted, fontFamily: F.sans, fontSize: 32 }}>
              <span style={{ width: 10, height: 10, background: C.accent, borderRadius: 99 }} /> {copy.answerBadge}
            </div>
          </div>

          <div style={{ padding: '28px 44px 0' }}>
            {/* The question, as the user asked it */}
            <div
              style={{
                height: 64,
                borderRadius: 10, // input token
                background: C.surface2,
                display: 'flex',
                alignItems: 'center',
                padding: '0 24px',
                color: C.muted,
                fontFamily: F.sans,
                fontSize: 32,
              }}
            >
              {copy.question}
            </div>

            {/* Summary first: one readable conclusion before any evidence */}
            <div style={{ height: 178, marginTop: 24, borderBottom: `1px solid ${C.border}`, overflow: 'hidden', position: 'relative' }}>
              <div
                style={{
                  position: 'absolute',
                  inset: 0,
                  opacity: summary,
                  transform: `translateY(${10 * (1 - summary)}px)`,
                  clipPath: `inset(0 ${100 * (1 - summary)}% 0 0)`,
                }}
              >
                <div style={{ color: C.accent, fontFamily: F.sans, fontSize: 30, letterSpacing: '.1em', marginBottom: 10 }}>
                  {copy.summaryLabel.toUpperCase()}
                </div>
                <div style={{ color: C.text, fontFamily: F.sans, fontSize: 42, fontWeight: 500, lineHeight: 1.4, whiteSpace: 'pre-line' }}>{copy.summary}</div>
              </div>
            </div>

            {/* Evidence rows */}
            <div style={{ position: 'relative', height: 80 * 5, marginTop: 22 }}>
              {copy.rows.map(([title, meta], index) => (
                <EvidenceRow key={title} cue={ROW_CUES[index]} title={title} meta={meta} index={index} />
              ))}
            </div>

            {/* Completion, then the caveat every Miru answer carries (own line: readable in both languages) */}
            <div style={{ marginTop: 4, borderTop: `1px solid ${C.border}`, paddingTop: 18, display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 14, opacity: complete, transform: `translateY(${5 * (1 - complete)}px)` }}>
                <div style={{ width: 32, height: 32, borderRadius: 99, background: C.accent, display: 'grid', placeItems: 'center' }}>
                  <svg width="18" height="18" viewBox="0 0 16 16" fill="none">
                    <path d="M3.4 8.3 6.6 11l6-6" stroke="#2a1304" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </div>
                <span style={{ color: C.text, fontFamily: F.sans, fontSize: 32, fontWeight: 600 }}>{copy.doneTitle}</span>
                <span style={{ color: C.muted, fontFamily: F.sans, fontSize: 32 }}>{copy.doneMeta}</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, color: C.muted, fontFamily: F.sans, fontSize: 32, opacity: caveat }}>
                <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke={C.muted} strokeWidth="1.8">
                  <circle cx="12" cy="12" r="9" />
                  <path d="M12 8v5M12 16v.5" strokeLinecap="round" />
                </svg>
                {copy.caveat}
              </div>
            </div>
          </div>
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
