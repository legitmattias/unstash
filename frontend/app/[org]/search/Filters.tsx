"use client";

import { useRouter } from "next/navigation";
import type { Dictionary } from "@/app/lib/i18n/dictionaries";
import styles from "./Filters.module.css";

const DOCX =
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

const TYPE_OPTIONS: readonly { value: string; label?: string }[] = [
  { value: "" },
  { value: "application/pdf", label: "PDF" },
  { value: DOCX, label: "Word" },
  { value: "text/markdown", label: "Text" },
];

interface Props {
  readonly org: string;
  readonly mimeType: string;
  readonly dateFrom: string;
  readonly dateTo: string;
  readonly dict: Dictionary["filters"];
}

export function Filters({ org, mimeType, dateFrom, dateTo, dict }: Props) {
  const router = useRouter();

  function update(key: string, value: string) {
    const params = new URLSearchParams(window.location.search);
    if (value) {
      params.set(key, value);
    } else {
      params.delete(key);
    }
    router.push(`/${org}/search?${params.toString()}`);
  }

  function clearFilters() {
    const current = new URLSearchParams(window.location.search);
    const params = new URLSearchParams();
    const q = current.get("q");
    if (q) {
      params.set("q", q);
    }
    router.push(`/${org}/search?${params.toString()}`);
  }

  const hasFilters = Boolean(mimeType || dateFrom || dateTo);

  return (
    <aside className={styles.side} aria-label={dict.heading}>
      <section className={styles.group}>
        <h2 className={styles.heading}>{dict.date}</h2>
        <label className={styles.field}>
          <span className={styles.label}>{dict.from}</span>
          <input
            className={styles.date}
            type="date"
            value={dateFrom}
            onChange={(e) => update("date_from", e.target.value)}
          />
        </label>
        <label className={styles.field}>
          <span className={styles.label}>{dict.to}</span>
          <input
            className={styles.date}
            type="date"
            value={dateTo}
            onChange={(e) => update("date_to", e.target.value)}
          />
        </label>
      </section>

      <section className={styles.group}>
        <h2 className={styles.heading}>{dict.type}</h2>
        <select
          className={styles.select}
          value={mimeType}
          onChange={(e) => update("mime_type", e.target.value)}
          aria-label={dict.type}
        >
          {TYPE_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label ?? dict.allTypes}
            </option>
          ))}
        </select>
      </section>

      {hasFilters ? (
        <button className={styles.clear} type="button" onClick={clearFilters}>
          {dict.clear}
        </button>
      ) : null}
    </aside>
  );
}
