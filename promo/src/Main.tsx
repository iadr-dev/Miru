// The whole film. SHOTS is the single source of truth for timing: captions and the SFX table are
// written relative to it, so moving a shot moves everything that belongs to it.
import React from 'react';
import { AbsoluteFill, Sequence } from 'remotion';
import { Caption } from './Caption';
import { COPY, type Lang } from './copy';
import { FocusShot } from './FocusShot';
import { Answer } from './scenes/Answer';
import { Brand } from './scenes/Brand';
import { Composer } from './scenes/Composer';
import { Converge } from './scenes/Converge';
import { Coverage } from './scenes/Coverage';
import { NoiseTunnel } from './scenes/NoiseTunnel';
import { Title } from './scenes/Title';
import { SoundTrack } from './SoundTrack';
import { C } from './theme';
import { CONVERGE_MOTION, SHOTS } from './timeline';


// CJK captions/titles are segmented by meaning by hand (blur-slide card: never per character).
const convergeWords = (lang: Lang, s: string): string[] => (lang === 'zh-TW' ? ['Miru', '一次整理', '所有資料'] : s.split(' '));
const titleLines = (lang: Lang, w: string[]): string[][] => (lang === 'zh-TW' ? [w] : [w.slice(0, 3), w.slice(3)]);

export const Main: React.FC<{ lang: Lang }> = ({ lang }) => {
  const copy = COPY[lang];
  return (
    <AbsoluteFill style={{ background: C.bg }}>
      {/* 1 · noise — runs on under shot 2 as its dimmed, blurred backdrop */}
      <FocusShot from={SHOTS.noise.from} duration={SHOTS.noise.duration + SHOTS.ask.duration} enter="none" exit="focus">
        <NoiseTunnel words={copy.noiseWords} lang={lang} dimFrom={SHOTS.noise.duration - 10} dimDur={30} />
      </FocusShot>
      <Sequence from={SHOTS.noise.from + 24} durationInFrames={62}>
        <Caption text={copy.noiseCaption1} duration={62} />
      </Sequence>
      <Sequence from={SHOTS.noise.from + 92} durationInFrames={68}>
        <Caption text={copy.noiseCaption2} duration={68} />
      </Sequence>

      {/* 2 · ask — the composer drops into the receding noise */}
      <FocusShot from={SHOTS.ask.from} duration={SHOTS.ask.duration} enter="none" exit="focus">
        <Composer text={copy.question} lang={lang} duration={SHOTS.ask.duration} />
      </FocusShot>

      {/* 3 · sources converge into the mark */}
      <FocusShot from={SHOTS.converge.from} duration={SHOTS.converge.duration}>
        <Converge
          sources={copy.sources}
          caption={copy.convergeCaption}
          captionWords={convergeWords(lang, copy.convergeCaption)}
          duration={CONVERGE_MOTION}
        />
      </FocusShot>

      {/* 4 · the answer — then a dark title card (seam D) */}
      <FocusShot from={SHOTS.answer.from} duration={SHOTS.answer.duration} exit="fade">
        <Answer copy={copy} />
      </FocusShot>

      {/* 5 · breath title */}
      <FocusShot from={SHOTS.title.from} duration={SHOTS.title.duration} enter="fade">
        <Title lines={titleLines(lang, copy.titleWords)} subtitle={copy.subtitleWords} lang={lang} duration={SHOTS.title.duration} />
      </FocusShot>

      {/* 6 · what you can ask about */}
      <FocusShot from={SHOTS.coverage.from} duration={SHOTS.coverage.duration}>
        <Coverage stem={copy.stem} pills={copy.pills} finale={copy.finale} lang={lang} />
      </FocusShot>

      {/* 7 · the composer becomes the logo */}
      <FocusShot from={SHOTS.brand.from} duration={SHOTS.brand.duration} exit="none">
        <Brand hint={copy.composerHint} wordmark={copy.wordmark} tagline={copy.tagline} lang={lang} />
      </FocusShot>

      <SoundTrack />
    </AbsoluteFill>
  );
};
