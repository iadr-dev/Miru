"use client";

import { useTranslations } from "next-intl";
import { Component, type ErrorInfo, type ReactNode } from "react";

type Props = {
  readonly children: ReactNode;
  /** Changing any of these (the active tab, the symbol) clears a caught error. */
  readonly resetKeys: readonly unknown[];
  readonly title: string;
  readonly body: string;
  readonly retryLabel: string;
};

type State = { readonly failed: boolean };

/**
 * Contains a render crash to the panel it happened in. Without it, one tab throwing during render
 * unmounts the whole app shell (nav, watchlist, chat) along with it. The fallback is a calm inline
 * card with a retry that re-renders the panel; moving to another tab or symbol also resets it.
 */
export class PanelErrorBoundary extends Component<Props, State> {
  override state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    // Surface it in the console for development; there is no client error reporter yet.
    console.error("panel_render_failed", error, info.componentStack);
  }

  override componentDidUpdate(previous: Props): void {
    if (
      this.state.failed &&
      (previous.resetKeys.length !== this.props.resetKeys.length ||
        previous.resetKeys.some((key, i) => !Object.is(key, this.props.resetKeys[i])))
    ) {
      this.setState({ failed: false });
    }
  }

  override render(): ReactNode {
    if (!this.state.failed) {
      return this.props.children;
    }
    return (
      <section role="alert" className="surface-card p-4 sm:p-5">
        <h2 className="font-semibold text-[15px] text-[var(--text)]">{this.props.title}</h2>
        <p className="mt-1 text-[13px] text-[var(--text-muted)]">{this.props.body}</p>
        <button
          type="button"
          onClick={() => this.setState({ failed: false })}
          className="mt-3 font-medium text-[13px] text-[var(--accent-text)] hover:underline"
        >
          {this.props.retryLabel}
        </button>
      </section>
    );
  }
}

/** `PanelErrorBoundary` with the standard copy, for wrapping a tab's panels. */
export function SectionBoundary({
  resetKeys,
  children,
}: {
  readonly resetKeys: readonly unknown[];
  readonly children: ReactNode;
}): ReactNode {
  const t = useTranslations("common.panelError");
  return (
    <PanelErrorBoundary
      resetKeys={resetKeys}
      title={t("title")}
      body={t("body")}
      retryLabel={t("retry")}
    >
      {children}
    </PanelErrorBoundary>
  );
}
