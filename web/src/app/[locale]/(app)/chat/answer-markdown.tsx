import type { ReactNode } from "react";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { TableWithActions } from "@/components/ui/table-actions";
import { Link } from "@/i18n/navigation";
import { cn } from "@/lib/cn";

/** Tag → styled-component map so a rendered answer picks up this project's tokens instead of
 * the browser default stylesheet. Model output is untrusted content rendered as real React
 * elements (never `dangerouslySetInnerHTML`) — markdown syntax becomes structure, nothing here
 * ever becomes executable/markup instructions to the app. */
const MARKDOWN_COMPONENTS: Components = {
  h1: ({ className, ...rest }) => (
    <h1
      className={cn("mt-5 mb-2 font-semibold text-[22px] text-[var(--text)] first:mt-0", className)}
      {...rest}
    />
  ),
  h2: ({ className, ...rest }) => (
    <h2
      className={cn("mt-5 mb-2 font-semibold text-[19px] text-[var(--text)] first:mt-0", className)}
      {...rest}
    />
  ),
  h3: ({ className, ...rest }) => (
    <h3
      className={cn(
        "mt-4 mb-1.5 font-semibold text-[16px] text-[var(--text)] first:mt-0",
        className,
      )}
      {...rest}
    />
  ),
  p: ({ className, ...rest }) => (
    <p
      className={cn("my-2 text-[var(--text)] leading-[1.7] first:mt-0 last:mb-0", className)}
      {...rest}
    />
  ),
  strong: ({ className, ...rest }) => (
    <strong className={cn("font-semibold text-[var(--text)]", className)} {...rest} />
  ),
  em: ({ className, ...rest }) => <em className={cn("italic", className)} {...rest} />,
  ul: ({ className, ...rest }) => (
    <ul className={cn("my-2 list-disc space-y-1 pl-5 text-[var(--text)]", className)} {...rest} />
  ),
  ol: ({ className, ...rest }) => (
    <ol
      className={cn("my-2 list-decimal space-y-1 pl-5 text-[var(--text)]", className)}
      {...rest}
    />
  ),
  li: ({ className, ...rest }) => <li className={cn("leading-[1.7]", className)} {...rest} />,
  a: ({ className, href, ...rest }) => {
    // External/model-cited links open in a new tab (untrusted destination); internal app routes
    // (bare "/settings"-style paths) use the locale-aware Link so navigation stays in-app.
    const isInternal = typeof href === "string" && href.startsWith("/");
    if (isInternal && href) {
      return (
        <Link
          href={href as "/settings"}
          className={cn("text-[var(--accent-text)] underline underline-offset-2", className)}
        />
      );
    }
    return (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className={cn("text-[var(--accent-text)] underline underline-offset-2", className)}
        {...rest}
      />
    );
  },
  // A blockquote reads as a callout card (bordered box, not just an italic left-rule) — the
  // shape model answers reach for to set off a key fact or a clarifying question from the
  // surrounding prose.
  blockquote: ({ className, ...rest }) => (
    <blockquote
      className={cn(
        "my-3 rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface-2)]",
        "px-4 py-3 text-[var(--text)] not-italic [&>p]:my-0",
        className,
      )}
      {...rest}
    />
  ),
  hr: ({ className, ...rest }) => (
    <hr className={cn("my-4 border-[var(--border)]", className)} {...rest} />
  ),
  // Inline code as a pill chip (rounded-full, tight padding) rather than a plain gray span —
  // reads as a distinct token (an identifier, a field name) inline with prose.
  code: ({ className, ...rest }) => (
    <code
      className={cn(
        "rounded-[var(--radius-pill)] bg-[var(--surface-2)] px-2 py-0.5 font-num text-[13px] text-[var(--accent-text)]",
        className,
      )}
      {...rest}
    />
  ),
  pre: ({ className, children, ...rest }) => (
    <pre
      className={cn(
        "my-3 overflow-x-auto rounded-[var(--radius-input)] border border-[var(--border)]",
        "bg-[var(--surface-2)] p-3 font-num text-[13px] text-[var(--text)] leading-relaxed",
        className,
      )}
      {...rest}
    >
      {/* A code *block* (multi-line, inside <pre>) drops the inline chip's pill/accent
          styling — reset via a child selector so `code`'s own component above only pill-styles
          genuinely inline code, not this block's contents. */}
      <span className="[&_code]:rounded-none [&_code]:bg-transparent [&_code]:p-0 [&_code]:text-[var(--text)]">
        {children}
      </span>
    </pre>
  ),
  // Every rendered table gets a copy/download action row below it (`TableWithActions` owns the
  // table's own border/overflow wrapper now, since the action row visually attaches to its
  // bottom edge — `thead`/`th`/`td` below are still the plain per-cell styling it renders inside).
  table: ({ node, className, ...rest }) => <TableWithActions className={className} {...rest} />,
  thead: ({ className, ...rest }) => (
    <thead className={cn("bg-[var(--surface-2)]", className)} {...rest} />
  ),
  th: ({ className, ...rest }) => (
    <th
      className={cn(
        "border-[var(--border)] border-b px-3 py-2 text-left font-medium text-[var(--text-muted)]",
        className,
      )}
      {...rest}
    />
  ),
  td: ({ className, ...rest }) => (
    <td
      className={cn("border-[var(--border)] border-b px-3 py-2 text-[var(--text)]", className)}
      {...rest}
    />
  ),
};

/** Renders a governed assistant answer as real markdown structure — headings, lists, tables,
 * bold/italic, code — instead of literal `##`/`**`/`|` characters. `remark-gfm` adds tables,
 * strikethrough, and autolinks, which model answers use routinely (comparison tables, task
 * lists). Text-only input; there is no raw-HTML pass-through (`skipHtml` default is already
 * true), so untrusted model text can never inject markup, only markdown structure. */
export function AnswerMarkdown({ text }: { text: string }): ReactNode {
  return (
    <div className="text-[16px]">
      <Markdown remarkPlugins={[remarkGfm]} components={MARKDOWN_COMPONENTS}>
        {text}
      </Markdown>
    </div>
  );
}
