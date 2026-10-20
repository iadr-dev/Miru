"use client";

import {
  ArrowsOutIcon,
  CheckIcon,
  CopyIcon,
  DownloadSimpleIcon,
  FileCsvIcon,
  FilePdfIcon,
  FileXlsIcon,
} from "@phosphor-icons/react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useTranslations } from "next-intl";
import {
  type ComponentPropsWithoutRef,
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { TableExpandModal } from "@/components/ui/table-expand-modal";
import { cn } from "@/lib/cn";

/** A rendered markdown table's data, read back from the DOM after mount (approach (a) from the
 * task brief): `react-markdown` v10's `table` override only receives styled `children` (the
 * already-rendered `thead`/`tbody` React tree), not the raw mdast AST, so pulling text content
 * out of the live `<table>` via `querySelectorAll` is the simpler, dependency-free path — no need
 * to thread a table-scoped context through `th`/`td` overrides just to accumulate the same
 * strings the DOM already holds post-render. */
function readTableRows(table: HTMLTableElement): string[][] {
  return Array.from(table.rows).map((row) =>
    Array.from(row.cells).map((cell) => cell.textContent?.trim() ?? ""),
  );
}

/** RFC 4180 field quoting — a field containing a comma, double quote, or newline is wrapped in
 * double quotes with internal quotes doubled; everything else is passed through bare. */
function csvField(value: string): string {
  if (/[",\n\r]/.test(value)) {
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}

function rowsToCsv(rows: string[][]): string {
  return rows.map((row) => row.map(csvField).join(",")).join("\r\n");
}

function rowsToTsv(rows: string[][]): string {
  // Tab-separated, no quoting — this is the clipboard format Excel/Sheets' paste-parser expects
  // for a plain multi-cell paste (RFC 4180 quoting is a CSV-file convention, not what the
  // clipboard's text/plain paste path looks for).
  return rows.map((row) => row.join("\t")).join("\n");
}

function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

/** Derives a filesystem-safe base filename from the table's first header cell (falls back to a
 * generic name), shared across all three export formats so a given table's CSV/XLSX/PDF agree. */
function tableFilenameBase(rows: string[][]): string {
  const firstHeader = rows[0]?.[0]?.trim();
  const stamp = new Date().toISOString().slice(0, 10);
  if (firstHeader) {
    const slug = firstHeader
      .toLowerCase()
      .replace(/[^\p{L}\p{N}]+/gu, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 40);
    if (slug) {
      return `${slug}-${stamp}`;
    }
  }
  return `table-export-${stamp}`;
}

async function downloadCsv(rows: string[][], base: string): Promise<void> {
  // A leading UTF-8 BOM is required for Excel on Windows to auto-detect UTF-8 — without it,
  // Excel falls back to the system's legacy codepage (e.g. Big5) and every multi-byte CJK
  // character renders as mojibake. Google Sheets/other tools ignore the BOM harmlessly.
  const csv = `﻿${rowsToCsv(rows)}`;
  triggerDownload(new Blob([csv], { type: "text/csv;charset=utf-8" }), `${base}.csv`);
}

async function downloadXlsx(rows: string[][], base: string): Promise<void> {
  const XLSX = await import("xlsx");
  const worksheet = XLSX.utils.aoa_to_sheet(rows);
  const workbook = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(workbook, worksheet, "Sheet1");
  // `write` (not `writeFile`) so we control the Blob + anchor ourselves, matching the CSV/PDF
  // download path above — `writeFile`'s browser codepath does the same thing internally but
  // keeping all three formats on one `triggerDownload` helper is simpler to verify/maintain.
  const data = XLSX.write(workbook, { bookType: "xlsx", type: "array" }) as ArrayBuffer;
  triggerDownload(
    new Blob([data], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }),
    `${base}.xlsx`,
  );
}

const CJK_FONT_NAME = "NotoSansTC";
const CJK_FONT_URL = "/fonts/NotoSansTC-subset.ttf";

/** Converts an `ArrayBuffer` to a base64 string in fixed-size chunks — a single-pass
 * `String.fromCharCode(...bytes)` spread would blow the call stack for a file this size (the
 * subset font is ~1.6MB), so bytes are walked in bounded slices and joined instead. */
function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  const chunkSize = 0x8000;
  const chunks: string[] = [];
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    const chunk = bytes.subarray(offset, offset + chunkSize);
    chunks.push(String.fromCharCode(...chunk));
  }
  return btoa(chunks.join(""));
}

/** Module-level memoization so the ~1.6MB CJK font is fetched + base64-encoded at most once per
 * page session, no matter how many times the user exports a PDF. */
let cjkFontBase64Promise: Promise<string> | null = null;

function loadCjkFontBase64(): Promise<string> {
  if (!cjkFontBase64Promise) {
    cjkFontBase64Promise = fetch(CJK_FONT_URL)
      .then((response) => response.arrayBuffer())
      .then(arrayBufferToBase64)
      .catch((error: unknown) => {
        // Allow a subsequent export attempt to retry the fetch rather than being stuck with a
        // permanently-rejected cached promise (e.g. transient network failure).
        cjkFontBase64Promise = null;
        throw error;
      });
  }
  return cjkFontBase64Promise;
}

async function downloadPdf(rows: string[][], base: string): Promise<void> {
  const [{ jsPDF }, autoTableModule, fontBase64] = await Promise.all([
    import("jspdf"),
    import("jspdf-autotable"),
    loadCjkFontBase64(),
  ]);
  const autoTable = autoTableModule.default;
  const doc = new jsPDF();
  // Register the Traditional-Chinese-subset font so table text (headers/body are commonly
  // zh-TW) renders as real glyphs instead of jsPDF's default Helvetica, which has no CJK
  // coverage and silently drops/garbles those characters.
  doc.addFileToVFS(`${CJK_FONT_NAME}.ttf`, fontBase64);
  doc.addFont(`${CJK_FONT_NAME}.ttf`, CJK_FONT_NAME, "normal");
  doc.setFont(CJK_FONT_NAME);
  const [head, ...body] = rows;
  autoTable(doc, {
    head: head ? [head] : [],
    body,
    // `styles` alone isn't enough: autoTable's own default `headStyles` sets
    // `fontStyle: "bold"`, which overrides `styles.font` for the header row specifically. Only
    // the "normal" weight of the CJK subset font was registered above (`doc.addFont(...,
    // "normal")`) — jsPDF has no "bold" variant of it to fall back to, so it silently substitutes
    // its built-in Helvetica for the header, which has no CJK coverage and renders as mojibake
    // (exactly the header-broken/body-fine split this fixes). Forcing `headStyles.fontStyle` to
    // "normal" keeps the header on the same registered CJK font as the body.
    styles: { font: CJK_FONT_NAME },
    headStyles: { font: CJK_FONT_NAME, fontStyle: "normal" },
  });
  doc.save(`${base}.pdf`);
}

type DownloadFormat = "csv" | "xlsx" | "pdf";

/** The action row rendered directly below every markdown table: copy-as-TSV (pastable into
 * Excel/Sheets) plus a download menu offering real CSV/XLSX/PDF files. Wraps the styled `<table>`
 * itself so it can read the table's own rendered rows via `tableRef` once, on click — no parser
 * changes needed in `answer-markdown.tsx`. */
export function TableWithActions({
  className,
  ...rest
}: ComponentPropsWithoutRef<"table">): ReactNode {
  const t = useTranslations("chat.table");
  const tableRef = useRef<HTMLTableElement | null>(null);
  const [copied, setCopied] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [expandTitle, setExpandTitle] = useState("");
  const reduceMotion = useReducedMotion();
  const copyTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (copyTimeoutRef.current) {
        clearTimeout(copyTimeoutRef.current);
      }
    };
  }, []);

  const onCopy = useCallback(async () => {
    const table = tableRef.current;
    if (!table) {
      return;
    }
    try {
      await navigator.clipboard.writeText(rowsToTsv(readTableRows(table)));
      setCopied(true);
      if (copyTimeoutRef.current) {
        clearTimeout(copyTimeoutRef.current);
      }
      copyTimeoutRef.current = setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard permission denied/unavailable — no toast, matching this codebase's convention
      // for non-fatal UI dismissals (see composer.tsx's screenshot-capture cancel).
    }
  }, []);

  const onDownload = useCallback((format: DownloadFormat) => {
    const table = tableRef.current;
    if (!table) {
      return;
    }
    const rows = readTableRows(table);
    const base = tableFilenameBase(rows);
    if (format === "csv") {
      void downloadCsv(rows, base);
    } else if (format === "xlsx") {
      void downloadXlsx(rows, base);
    } else {
      void downloadPdf(rows, base);
    }
  }, []);

  const onExpand = useCallback(() => {
    const table = tableRef.current;
    if (!table) {
      return;
    }
    // Same first-header-cell-as-title convention `tableFilenameBase` already uses for the
    // download filename — one derivation, reused, rather than a second title-guessing rule.
    const firstHeader = table.rows[0]?.cells[0]?.textContent?.trim();
    setExpandTitle(firstHeader || t("expand"));
    setExpanded(true);
  }, [t]);

  return (
    <div className="my-3">
      <div className="overflow-x-auto rounded-t-[var(--radius-input)] border border-[var(--border)]">
        <table
          ref={tableRef}
          {...rest}
          className={cn("w-full border-collapse text-[14px]", className)}
        />
      </div>
      <div
        className={cn(
          "flex items-center gap-1 rounded-b-[var(--radius-input)] border border-[var(--border)] border-t-0",
          "bg-[var(--surface-2)] px-2 py-1",
        )}
      >
        <button
          type="button"
          onClick={onCopy}
          aria-label={copied ? t("copied") : t("copy")}
          title={copied ? t("copied") : t("copy")}
          className={cn(
            "relative inline-flex size-7 items-center justify-center rounded-[var(--radius-button)]",
            "text-[var(--text-muted)] transition-colors hover:bg-[var(--surface)] hover:text-[var(--text)]",
            "!outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
          )}
        >
          <AnimatePresence initial={false} mode="wait">
            <motion.span
              key={copied ? "copied" : "copy"}
              initial={{ opacity: 0, scale: 0.6 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.6 }}
              transition={{ duration: reduceMotion ? 0 : 0.15 }}
              className="inline-flex"
            >
              {copied ? (
                <CheckIcon size={14} weight="regular" className="text-[var(--accent)]" />
              ) : (
                <CopyIcon size={14} weight="regular" />
              )}
            </motion.span>
          </AnimatePresence>
        </button>
        <DropdownMenu.Root>
          <DropdownMenu.Trigger asChild>
            <button
              type="button"
              aria-label={t("download")}
              title={t("download")}
              className={cn(
                "inline-flex size-7 items-center justify-center rounded-[var(--radius-button)]",
                "text-[var(--text-muted)] transition-colors hover:bg-[var(--surface)] hover:text-[var(--text)]",
                "!outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
                "data-[state=open]:bg-[var(--surface)] data-[state=open]:text-[var(--text)]",
              )}
            >
              <DownloadSimpleIcon size={14} weight="regular" />
            </button>
          </DropdownMenu.Trigger>
          <DropdownMenu.Portal>
            <DropdownMenu.Content
              side="bottom"
              align="start"
              sideOffset={6}
              className={cn(
                "z-50 w-44 rounded-[var(--radius-card)] border border-[var(--border)]",
                "bg-[var(--surface)] p-1.5 shadow-2xl",
              )}
            >
              <DownloadMenuItem
                icon={FileCsvIcon}
                label={t("downloadCsv")}
                onSelect={() => onDownload("csv")}
              />
              <DownloadMenuItem
                icon={FileXlsIcon}
                label={t("downloadXlsx")}
                onSelect={() => onDownload("xlsx")}
              />
              <DownloadMenuItem
                icon={FilePdfIcon}
                label={t("downloadPdf")}
                onSelect={() => onDownload("pdf")}
              />
            </DropdownMenu.Content>
          </DropdownMenu.Portal>
        </DropdownMenu.Root>
        <button
          type="button"
          onClick={onExpand}
          aria-label={t("expand")}
          title={t("expand")}
          className={cn(
            "inline-flex size-7 items-center justify-center rounded-[var(--radius-button)]",
            "text-[var(--text-muted)] transition-colors hover:bg-[var(--surface)] hover:text-[var(--text)]",
            "!outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
          )}
        >
          <ArrowsOutIcon size={14} weight="regular" />
        </button>
      </div>
      <TableExpandModal
        open={expanded}
        title={expandTitle}
        sourceTable={tableRef.current}
        closeLabel={t("collapse")}
        onClose={() => setExpanded(false)}
      />
    </div>
  );
}

/** One row of the download-format menu — mirrors `composer.tsx`'s `FeatureMenuItem` construction
 * (icon + label, no trailing checkmark since these aren't toggles). */
function DownloadMenuItem({
  icon: Icon,
  label,
  onSelect,
}: Readonly<{
  icon: typeof DownloadSimpleIcon;
  label: string;
  onSelect: () => void;
}>): ReactNode {
  return (
    <DropdownMenu.Item
      onSelect={onSelect}
      className={cn(
        "flex min-h-8 cursor-pointer items-center gap-2.5 rounded-[var(--radius-button)] px-2.5",
        "text-[var(--text)] text-sm outline-none transition-colors",
        "data-[highlighted]:bg-[var(--surface-2)]",
      )}
    >
      <Icon size={15} weight="regular" aria-hidden />
      <span className="flex-1">{label}</span>
    </DropdownMenu.Item>
  );
}
