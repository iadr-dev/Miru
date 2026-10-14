// Seam treatment from shot-transitions: C "focus relay" (前景 blur 0→8px while 後景 8→0, a 10–16 f
// exchange, the two starting 2–4 f apart) and D "dark title card" (fade to the ebony field 6–10 f).
// A shot starts LEAD frames early frozen on its first frame while it comes into focus, and ends
// TAIL frames late frozen on its last frame while it leaves; the shot's own timing is untouched.
import React from 'react';
import { AbsoluteFill, Freeze, Sequence, interpolate, useCurrentFrame } from 'remotion';

const LEAD = 8;
const TAIL = 6;
const clamp = { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' } as const;

type Seam = 'focus' | 'fade' | 'none';

const Inner: React.FC<{ duration: number; enter: Seam; exit: Seam; lead: number; tail: number; children: React.ReactNode }> = ({
  duration,
  enter,
  exit,
  lead,
  tail,
  children,
}) => {
  const local = useCurrentFrame();
  const sceneFrame = Math.min(duration - 1, Math.max(0, local - lead));
  const end = lead + duration + tail;
  const inWin = lead + 4; // 12 f in
  const outWin = tail + 6; // 12 f out, starting 2 f after the next shot begins its lead
  const inP = enter === 'none' ? 1 : interpolate(local, [0, inWin], [0, 1], clamp);
  const outP = exit === 'none' ? 1 : interpolate(local, [end - outWin, end], [1, 0], clamp);
  const blurIn = enter === 'focus' ? (1 - inP) * 8 : 0;
  const blurOut = exit === 'focus' ? (1 - outP) * 8 : 0;
  const blur = blurIn + blurOut;
  return (
    <AbsoluteFill style={{ opacity: inP * outP, filter: blur > 0.01 ? `blur(${blur}px)` : undefined }}>
      <Freeze frame={sceneFrame}>{children}</Freeze>
    </AbsoluteFill>
  );
};

export const FocusShot: React.FC<{
  from: number;
  duration: number;
  enter?: Seam;
  exit?: Seam;
  children: React.ReactNode;
}> = ({ from, duration, enter = 'focus', exit = 'focus', children }) => {
  const lead = enter === 'none' ? 0 : LEAD;
  const tail = exit === 'none' ? 0 : TAIL;
  return (
    <Sequence from={from - lead} durationInFrames={duration + lead + tail}>
      <Inner duration={duration} enter={enter} exit={exit} lead={lead} tail={tail}>
        {children}
      </Inner>
    </Sequence>
  );
};
