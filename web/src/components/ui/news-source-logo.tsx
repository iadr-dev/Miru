"use client";

import { GlobeIcon } from "@phosphor-icons/react";
import { type ReactNode, useState } from "react";
import { sourceLogoUrl } from "@/lib/source-logo";

/** A small source-site favicon/logo for a news-article URL — shared by every news list
 * (`stock-news-panel.tsx`'s TW feed, `news-filings-panel.tsx`'s US feed) so the visual treatment
 * never drifts between markets. Falls back to a generic globe glyph when the domain has no
 * resolvable logo, or when the image itself fails to load. */
export function NewsSourceLogo({ url }: { readonly url: string }): ReactNode {
  const logoUrl = sourceLogoUrl(url);
  const [failed, setFailed] = useState(false);
  if (!logoUrl || failed) {
    return (
      <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center text-[var(--text-muted)]">
        <GlobeIcon size={14} weight="regular" aria-hidden />
      </span>
    );
  }
  return (
    // biome-ignore lint/performance/noImgElement: dynamic per-source domain, remotePatterns can't allowlist it
    <img
      src={logoUrl}
      alt=""
      width={20}
      height={20}
      className="mt-0.5 size-5 shrink-0 rounded-[4px] object-contain"
      onError={() => setFailed(true)}
    />
  );
}
