// Copied from the skill's demos/_fixtures/Motion.tsx: content is laid out in a 480×270 design
// space (the coordinates every motion-lab card's parameter table is tuned in) and scaled up to
// the composition. raster="zoom" scales at layout time so text rasterises at full resolution.
import React from 'react';
import { AbsoluteFill, useVideoConfig } from 'remotion';

export const DesignStage: React.FC<{
  w?: number;
  h?: number;
  bg?: string;
  raster?: 'scale' | 'zoom';
  children: React.ReactNode;
}> = ({ w = 480, h = 270, bg, raster = 'scale', children }) => {
  const { width } = useVideoConfig();
  const scale = width / w;
  return (
    <AbsoluteFill style={{ background: bg ?? 'transparent', overflow: 'hidden' }}>
      <div
        style={{
          position: 'absolute',
          left: 0,
          top: 0,
          width: w,
          height: h,
          overflow: 'hidden',
          ...(raster === 'zoom'
            ? { zoom: scale }
            : { transform: `scale(${scale})`, transformOrigin: 'top left' }),
        }}
      >
        {children}
      </div>
    </AbsoluteFill>
  );
};
