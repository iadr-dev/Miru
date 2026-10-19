"use client";

import dynamic from "next/dynamic";
import { Component, type ErrorInfo, type ReactNode } from "react";
import type { EntityGraphSphereProps } from "@/components/stock/entity-graph-sphere";
import { Skeleton } from "@/components/ui/skeleton";

// three.js is about 1 MB and only needed once someone picks the sphere view, so it loads on
// demand (web/OVERVIEW.md §5), never with the stock page.
const EntityGraphSphere = dynamic(
  () => import("@/components/stock/entity-graph-sphere").then((m) => m.EntityGraphSphere),
  { ssr: false, loading: () => <SphereSkeleton /> },
);

/** The sphere's own shape while its chunk loads: a round placeholder in the same box. */
function SphereSkeleton(): ReactNode {
  return (
    <div
      aria-hidden
      className="flex h-[clamp(20rem,75cqw,36rem)] w-full items-center justify-center rounded-[var(--radius-card)] border border-[var(--border)] p-6"
    >
      <Skeleton className="aspect-square h-full max-w-full rounded-full" />
    </div>
  );
}

type BoundaryProps = { readonly children: ReactNode; readonly onFailed: () => void };

/** A WebGL context that cannot be created (or a chunk that fails to load) throws while the sphere
 * renders; this catches it and hands control back so the flat graph is shown instead. */
class SphereBoundary extends Component<BoundaryProps, { readonly failed: boolean }> {
  override state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("entity_graph_sphere_failed", error, info.componentStack);
    this.props.onFailed();
  }

  override render(): ReactNode {
    return this.state.failed ? null : this.props.children;
  }
}

export function EntityGraphSpherePanel({
  onFailed,
  ...props
}: EntityGraphSphereProps & { readonly onFailed: () => void }): ReactNode {
  return (
    <SphereBoundary onFailed={onFailed}>
      <EntityGraphSphere {...props} />
    </SphereBoundary>
  );
}
