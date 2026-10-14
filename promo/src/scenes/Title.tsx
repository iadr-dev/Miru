// Shot 5 — blur-slide (demos/typography/blur-slide/BlurSlide.tsx). Kept exactly: one progress p
// per word drives opacity, y (dy→0) and blur ((1−p)·10px) through the same outCubic; title window
// seg(0.06→0.62) with gap 0.055 and a 0.32 per-word window, dy 40; subtitle window seg(0.34→0.9),
// gap 0.04, dy 26, overlapping the title by design; words, not letters.
// Changed: Miru's display serif for Latin (Noto Sans TC for CJK, as fonts.ts prescribes — a serif
// CJK face renders poorly), cream/etoupe colours, and the English title wraps onto two lines while
// the word index keeps counting across the break so it still reads as one sentence.
import React from 'react';
import { useCurrentFrame } from 'remotion';
import { DesignStage } from '../DesignStage';
import type { Lang } from '../copy';
import { E, lerp, seg, tOf } from '../motion';
import { C, F } from '../theme';

const Word: React.FC<{ w: string; p: number; dy: number }> = ({ w, p, dy }) => (
  <span style={{ display: 'inline-block', opacity: p, transform: `translateY(${lerp(p, dy, 0)}px)`, filter: `blur(${(1 - p) * 10}px)` }}>
    {w}
  </span>
);

export const Title: React.FC<{
  lines: string[][]; // title words, grouped into display lines
  subtitle: string[];
  lang: Lang;
  duration: number;
}> = ({ lines, subtitle, lang, duration }) => {
  const frame = useCurrentFrame();
  const t = tOf(frame, duration);
  const tTitle = seg(t, 0.06, 0.62);
  const tSub = seg(t, 0.34, 0.9);
  const cjk = lang === 'zh-TW';
  let idx = 0;
  return (
    <DesignStage raster="zoom">
      <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 16 }}>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
          {lines.map((line, li) => (
            <div
              key={li}
              style={{
                display: 'flex',
                gap: cjk ? '0.05em' : '0.28em',
                fontFamily: cjk ? F.sans : F.serif,
                fontWeight: cjk ? 600 : 500,
                fontSize: cjk ? 30 : 32,
                lineHeight: 1.18,
                color: C.text,
                letterSpacing: cjk ? '0.02em' : '-0.3px',
              }}
            >
              {line.map((w) => {
                const i = idx++;
                return <Word key={i} w={w} p={seg(tTitle, i * 0.055, i * 0.055 + 0.32, E.outCubic)} dy={40} />;
              })}
            </div>
          ))}
        </div>
        <div style={{ display: 'flex', gap: cjk ? 0 : '0.32em', fontFamily: F.sans, fontWeight: 400, fontSize: 14.5, lineHeight: 1.4, color: C.muted }}>
          {subtitle.map((w, i) => (
            <Word key={i} w={w} p={seg(tSub, i * 0.04, i * 0.04 + 0.32, E.outCubic)} dy={26} />
          ))}
        </div>
      </div>
    </DesignStage>
  );
};
