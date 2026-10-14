import React from 'react';
import { Composition, Still } from 'remotion';
import { Main } from './Main';
import { TOTAL_FRAMES } from './timeline';
import { MarkCheck } from './MarkCheck';
import type { Lang } from './copy';

export type PromoProps = { lang: Lang };

export const RemotionRoot: React.FC = () => (
  <>
    <Composition
      id="MiruPromo"
      component={Main}
      durationInFrames={TOTAL_FRAMES}
      fps={30}
      width={1920}
      height={1080}
      defaultProps={{ lang: 'zh-TW' } satisfies PromoProps}
    />
    <Still id="MarkCheck" component={MarkCheck} width={1920} height={1080} />
  </>
);
