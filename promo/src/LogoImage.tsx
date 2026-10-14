// The real logo (web/public/logo.png) knocked out for a dark background: a colour matrix maps the
// PNG's white to transparent and its orange to opaque #f37021, so the gaps and the centre show the
// ebony behind them, an open aperture. Used wherever the mark must be exact.
// Rendered as Remotion's <Img> (whose load Remotion waits for) with the matrix applied as a CSS
// filter. An SVG <image> is NOT load-tracked: a frame could render before the PNG arrived, and the
// matrix would turn the empty area into a solid orange square (found in QA at f1059).
// Clipped to the disc (924/1024 of the image): the PNG's near-white border keeps a sliver of
// alpha and otherwise draws a faint square hairline.
import React from 'react';
import { Img, staticFile } from 'remotion';

let seq = 0;

export const LogoImage: React.FC<{ size: number; opacity?: number }> = ({ size, opacity = 1 }) => {
  const [id] = React.useState(() => `miru-knockout-${seq++}`);
  return (
    <div style={{ width: size, height: size, opacity }}>
      <svg width={0} height={0} style={{ position: 'absolute' }} aria-hidden>
        <defs>
          {/* Region = the element's own box; RGB → logo orange; A = (1 − B) / 0.87. */}
          <filter id={id} colorInterpolationFilters="sRGB" filterUnits="objectBoundingBox" x={0} y={0} width={1} height={1}>
            <feColorMatrix
              type="matrix"
              values="0 0 0 0 0.953
                      0 0 0 0 0.439
                      0 0 0 0 0.129
                      0 0 -1.149 0 1.149"
            />
          </filter>
        </defs>
      </svg>
      <Img src={staticFile('logo.png')} style={{ width: size, height: size, display: 'block', filter: `url(#${id})`, clipPath: 'circle(45.2% at 50% 50%)' }} />
    </div>
  );
};
