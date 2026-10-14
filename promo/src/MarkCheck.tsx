// QA-only composition: the knocked-out real logo on ebony, beside the vector assembly mark.
import React from 'react';
import { AbsoluteFill } from 'remotion';
import { LogoImage } from './LogoImage';
import { MiruMark } from './MiruMark';
import { C } from './theme';

export const MarkCheck: React.FC = () => (
  <AbsoluteFill style={{ background: C.bg, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-around' }}>
    <LogoImage size={800} />
    <div style={{ width: 800, height: 800, display: 'grid', placeItems: 'center' }}>
      <MiruMark size={(800 * 924) / 1024} />
    </div>
  </AbsoluteFill>
);
