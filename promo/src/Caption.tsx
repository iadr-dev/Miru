// Adapted from the skill's assets/lib/Caption.tsx. Raised from the 22px info-strip to the Q11
// narration size (58px, ≥5% of frame height) and reskinned to Miru's cream text + orange square.
import React from 'react';
import { interpolate, useCurrentFrame } from 'remotion';
import { C, F } from './theme';

export const Caption: React.FC<{ text: string; duration: number; bottom?: number }> = ({
  text,
  duration,
  bottom = 96,
}) => {
  const frame = useCurrentFrame();
  const clamp = { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' } as const;
  const inT = interpolate(frame, [0, 10], [0, 1], clamp);
  const outT = interpolate(frame, [duration - 10, duration], [1, 0], clamp);
  return (
    <div
      style={{
        position: 'absolute',
        left: 0,
        right: 0,
        bottom,
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        gap: 22,
        opacity: inT * outT,
        transform: `translateY(${(1 - inT) * 10}px)`,
        pointerEvents: 'none',
      }}
    >
      <span style={{ width: 12, height: 12, background: C.accent, display: 'inline-block' }} />
      <span
        style={{
          fontFamily: F.sans,
          fontWeight: 500,
          fontSize: 58,
          letterSpacing: '0.01em',
          color: C.text,
          textShadow: '0 2px 24px rgba(16,13,11,.9), 0 0 2px rgba(16,13,11,.8)',
          whiteSpace: 'nowrap',
        }}
      >
        {text}
      </span>
    </div>
  );
};
