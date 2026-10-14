// Shot 2 — glass-pill-dictation-typing (demos/interaction/glass-pill-dictation-typing/
// GlassPillDictationTyping.tsx), reskinned. Kept: fixed-width pill, scale 1.25→1 over t 0→0.22
// outCubic with opacity full by 0.025, the inner left-dark→right-bright accent light, caret first
// (0.025) and gone at 0.75–0.8, uniform typing over 0.06→0.73 with the 1e-6 epsilon, the glow
// g = 1 − seg(0.08→0.76, inOutQuad) driving all three shadow layers, the 5-bar icon.
// Changed: ACCENT_RGB = Miru orange, warm ebony glass, the real question as the text.
// Retuned per the card's pitfall note (the sentence is longer than the 18-char placeholder):
// the shot runs 120 f instead of 50 f so typing stays at a human ~2.5–6 f per character, the type
// size is set per language, and the pill width is capped so it never leaves the frame.
import React, { useLayoutEffect, useRef, useState } from 'react';
import { continueRender, delayRender, useCurrentFrame } from 'remotion';
import { DesignStage } from '../DesignStage';
import type { Lang } from '../copy';
import { E, lerp, seg, tOf } from '../motion';
import { ACCENT_RGB, C, F } from '../theme';

const PH = 46;
const ICON = 30;
const BASE = [13, 7, 10, 6, 9];

export const Composer: React.FC<{ text: string; lang: Lang; duration: number }> = ({
  text,
  lang,
  duration,
}) => {
  const frame = useCurrentFrame();
  const t = tOf(frame, duration);
  const fontPx = lang === 'zh-TW' ? 19 : 16;
  const font = `400 ${fontPx}px ${F.sans}`;

  const measRef = useRef<HTMLDivElement>(null);
  const [textW, setTextW] = useState(240);
  // Measure after the web fonts are in (the card's pitfall: the width must come from the exact
  // final font), holding the frame until then so no frame renders with a fallback-font width.
  const [handle] = useState(() => delayRender('measure composer text'));
  useLayoutEffect(() => {
    if (measRef.current) setTextW(measRef.current.offsetWidth);
    void document.fonts.ready.then(() => {
      if (measRef.current) setTextW(measRef.current.offsetWidth);
      continueRender(handle);
    });
  }, [handle]);
  const PW = Math.min(Math.round(textW + 168), 440);

  const s = lerp(seg(t, 0, 0.22, E.outCubic), 1.25, 1);
  const chars = Array.from(text);
  const n = Math.floor(seg(t, 0.06, 0.73) * chars.length + 1e-6);
  const caretO = seg(t, 0.025, 0.045) * (1 - seg(t, 0.75, 0.8));
  const g = 1 - seg(t, 0.08, 0.76, E.inOutQuad);

  return (
    <DesignStage raster="zoom">
      <div
        style={{
          position: 'absolute',
          left: 0,
          top: (270 - PH) / 2,
          height: PH,
          width: PW,
          borderRadius: PH / 2,
          boxSizing: 'border-box',
          display: 'flex',
          alignItems: 'center',
          padding: '0 8px 0 16px',
          overflow: 'hidden',
          background: C.surface,
          opacity: seg(t, 0, 0.025),
          transform: `translateX(${(480 - PW) / 2}px) scale(${s})`,
          boxShadow: `inset 0 0 0 1px rgba(255,240,220,${0.16 + 0.1 * g}), inset 0 14px 22px rgba(255,240,220,${0.03 + 0.04 * g}), 0 0 ${26 * g}px rgba(${ACCENT_RGB},${0.28 * g})`,
        }}
      >
        <div
          style={{
            position: 'absolute',
            inset: 0,
            pointerEvents: 'none',
            opacity: g,
            background: `linear-gradient(90deg,rgba(${ACCENT_RGB},0) 0%,rgba(${ACCENT_RGB},.35) 42%,rgba(${ACCENT_RGB},.95) 100%)`,
          }}
        />
        <div
          style={{
            position: 'relative',
            font,
            color: C.text,
            whiteSpace: 'pre',
            letterSpacing: 0.3,
            flex: 'none',
          }}
        >
          {chars.slice(0, n).join('')}
        </div>
        <div
          style={{
            position: 'relative',
            width: 2,
            height: 23,
            borderRadius: 1,
            background: C.text,
            marginLeft: 2,
            flex: 'none',
            opacity: caretO,
          }}
        />
        <div
          style={{
            position: 'relative',
            marginLeft: 'auto',
            width: ICON,
            height: ICON,
            borderRadius: 9,
            boxSizing: 'border-box',
            border: '1.5px solid rgba(244,237,227,.5)',
            background: 'rgba(244,237,227,.05)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flex: 'none',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 2.2, height: '100%' }}>
            {BASE.map((h, i) => (
              <div
                key={i}
                style={{
                  width: 1.5,
                  borderRadius: 1.5,
                  background: C.text,
                  height: h + 1.6 * Math.sin(t * 18 + i * 1.7),
                }}
              />
            ))}
          </div>
        </div>
      </div>
      <div
        ref={measRef}
        style={{
          position: 'absolute',
          visibility: 'hidden',
          whiteSpace: 'pre',
          font,
          letterSpacing: 0.3,
          top: -999,
        }}
      >
        {text}
      </div>
    </DesignStage>
  );
};
