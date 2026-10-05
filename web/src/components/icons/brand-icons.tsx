import type { ReactNode } from "react";

/**
 * Official brand marks for the two OAuth providers. Phosphor's icon set is deliberately
 * monochrome (one icon family, `weight="regular"`) — brand logos are the one
 * sanctioned exception, since Google and LINE's own brand guidelines require their marks render
 * in full color, never recolored to a UI token. Kept as static inline SVG (no extra dependency);
 * do not wrap these in the tokenized icon system or tint them via `currentColor`.
 */

/** Google "G" mark, official 4-color version (per Google's brand guidelines — never monochrome). */
export function GoogleIcon({ size = 20 }: { size?: number }): ReactNode {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 48 48"
      role="presentation"
      aria-hidden="true"
      focusable="false"
    >
      <path
        fill="#4285F4"
        d="M45.12 24.5c0-1.56-.14-3.06-.4-4.5H24v8.51h11.84c-.51 2.75-2.06 5.08-4.39 6.64v5.52h7.11c4.16-3.83 6.56-9.47 6.56-16.17z"
      />
      <path
        fill="#34A853"
        d="M24 46c5.94 0 10.92-1.97 14.56-5.33l-7.11-5.52c-1.97 1.32-4.49 2.1-7.45 2.1-5.73 0-10.58-3.87-12.32-9.07H4.34v5.7C7.96 41.07 15.4 46 24 46z"
      />
      <path
        fill="#FBBC05"
        d="M11.68 28.18A13.93 13.93 0 0 1 10.75 24c0-1.45.25-2.86.93-4.18v-5.7H4.34A23.93 23.93 0 0 0 2 24c0 3.86.92 7.51 2.34 10.88l7.34-5.7z"
      />
      <path
        fill="#EA4335"
        d="M24 10.75c3.23 0 6.13 1.11 8.41 3.29l6.31-6.31C34.91 4.18 29.93 2 24 2 15.4 2 7.96 6.93 4.34 14.12l7.34 5.7c1.74-5.2 6.59-9.07 12.32-9.07z"
      />
    </svg>
  );
}

/** LINE's official app mark — solid rounded-square with the white "LINE" speech-bubble glyph. */
export function LineIcon({ size = 20 }: { size?: number }): ReactNode {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 48 48"
      role="presentation"
      aria-hidden="true"
      focusable="false"
    >
      <rect width="48" height="48" rx="10" fill="#06C755" />
      <path
        fill="#FFFFFF"
        d="M38.7 22.1c0-6.9-6.9-12.5-15.4-12.5S7.9 15.2 7.9 22.1c0 6.2 5.5 11.4 12.9 12.3.5.1 1.2.3 1.4.8.1.4.1 1 .1 1.4l-.2 1.4c-.1.4-.3 1.6 1.4.9 1.7-.7 9.1-5.4 12.4-9.2 2.3-2.5 3.8-5.1 3.8-7.6zm-20.8 3.1h-2.9c-.4 0-.8-.3-.8-.8v-5.8c0-.4.3-.8.8-.8.4 0 .8.3.8.8v5h2.1c.4 0 .8.3.8.8s-.4.8-.8.8zm3.5-.8c0 .4-.3.8-.8.8-.4 0-.8-.3-.8-.8v-5.8c0-.4.3-.8.8-.8.4 0 .8.3.8.8zm6.7 0c0 .3-.2.6-.5.7-.1 0-.2.1-.3.1-.2 0-.5-.1-.6-.3l-3-4v3.4c0 .4-.3.8-.8.8-.4 0-.8-.3-.8-.8v-5.8c0-.3.2-.6.5-.7.1 0 .2-.1.3-.1.2 0 .5.1.6.3l3 4v-3.5c0-.4.3-.8.8-.8.4 0 .8.3.8.8zm5.4-4.2h-2.1v1.4h2.1c.4 0 .8.3.8.8s-.4.8-.8.8h-2.1v1.4h2.1c.4 0 .8.3.8.8s-.4.8-.8.8h-2.9c-.4 0-.8-.3-.8-.8v-5.8c0-.4.3-.8.8-.8h2.9c.4 0 .8.3.8.8s-.4.7-.8.7z"
      />
    </svg>
  );
}

/** Telegram's official mark — blue disc with the white paper plane. */
export function TelegramIcon({ size = 20 }: { size?: number }): ReactNode {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 48 48"
      role="presentation"
      aria-hidden="true"
      focusable="false"
    >
      <circle cx="24" cy="24" r="24" fill="#27A7E7" />
      <path
        fill="#FFFFFF"
        d="M10.6 23.7c7-3.1 11.7-5.1 14-6.1 6.7-2.8 8.1-3.3 9-3.3.2 0 .6 0 .9.3.2.2.3.4.3.7 0 .2.1.6 0 .9-.4 4.7-2.5 16-3.5 21.2-.4 2.2-1.2 3-2.1 3.1-1.8.2-3.1-1.2-4.8-2.3-2.7-1.8-4.1-2.9-6.6-4.5-2.9-1.9-1-3 .6-4.8.4-.5 7.9-7.3 8-7.9 0-.1 0-.3-.1-.4s-.3-.1-.5-.1c-.2 0-3.9 2.5-11.3 7.5-1.1.7-2 1.1-2.9 1.1-1 0-2.8-.5-4.2-1-1.7-.5-3.1-.8-3-1.8 0-.5.7-1 1.800-1.500z"
      />
    </svg>
  );
}
