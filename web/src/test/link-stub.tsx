import type { AnchorHTMLAttributes, ReactNode } from "react";

/** Stand-in for `@/i18n/navigation`'s `Link` in jsdom tests: a plain anchor. */
export function LinkStub({
  href,
  children,
  ...rest
}: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }): ReactNode {
  return (
    <a href={href} {...rest}>
      {children}
    </a>
  );
}
