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

interface CategoryOption {
  readonly id: string;
  readonly label: string;
}

interface Props {
  readonly org: string;
  readonly mimeType: string;
  readonly dateFrom: string;
  readonly dateTo: string;
  readonly category: string;
  readonly categories: readonly CategoryOption[];
  readonly dict: Dictionary["filters"];
}

export function Filters({ org, mimeType, dateFrom, dateTo, category, categories, dict }: Props) {
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

  const hasFilters = Boolean(mimeType || dateFrom || dateTo || category);

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

      {categories.length > 0 ? (
        <section className={styles.group}>
          <h2 className={styles.heading}>{dict.category}</h2>
          <select
            className={styles.select}
            value={category}
            onChange={(e) => update("category", e.target.value)}
            aria-label={dict.category}
          >
            <option value="">{dict.allCategories}</option>
            {categories.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
        </section>
      ) : null}

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
