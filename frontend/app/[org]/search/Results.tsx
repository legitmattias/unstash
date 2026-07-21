"use client";

import type { ReactNode, CSSProperties } from "react";
import Link from "next/link";
import { docType } from "@/app/lib/doctype";
import type { SearchResponse } from "@/app/lib/api";
import type { Dictionary } from "@/app/lib/i18n/dictionaries";
import styles from "./Results.module.css";

interface Props {
  readonly org: string;
  readonly query: string;
  readonly data: SearchResponse | null;
  readonly failed: boolean;
  readonly dict: Dictionary["search"];
}

function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Mark occurrences of query terms (3+ chars) in the snippet, mirroring the
// substring match the API centres the snippet on.
function highlight(text: string, query: string): ReactNode {
  const terms = query
    .split(/\s+/)
    .filter((term) => term.length >= 3)
    .map((term) => term.toLowerCase());
  if (terms.length === 0) {
    return text;
  }
  const pattern = new RegExp(`(${terms.map(escapeRegex).join("|")})`, "gi");
  return text.split(pattern).map((part, index) =>
    terms.includes(part.toLowerCase()) ? (
      <mark key={index} className={styles.mark}>
        {part}
      </mark>
    ) : (
      part
    ),
  );
}

function State({
  title,
  body,
  tone,
}: {
  readonly title: string;
  readonly body: string;
  readonly tone?: "error";
}) {
  return (
    <div className={tone === "error" ? styles.stateError : styles.state}>
      <p className={styles.stateTitle}>{title}</p>
      <p className={styles.stateBody}>{body}</p>
    </div>
  );
}

export function Results({ org, query, data, failed, dict }: Props) {
  if (!query) {
    return <State title={dict.emptyTitle} body={dict.emptyBody} />;
  }
  if (failed) {
    return <State title={dict.errorTitle} body={dict.errorBody} tone="error" />;
  }
  if (!data || data.result_count === 0) {
    return <State title={dict.zeroTitle.replace("{query}", query)} body={dict.zeroBody} />;
  }

  function reportClick(searchId: string, documentId: string) {
    void fetch(`/api/orgs/${org}/search/${searchId}/click`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ document_id: documentId }),
    }).catch(() => {});
  }

  return (
    <>
      <p className={styles.count}>
        {dict.resultsCount.replace("{count}", String(data.result_count))}
      </p>
      <ol className={styles.list}>
        {data.results.map((result) => {
          const type = docType(result.mime_type);
          return (
            <li
              key={result.chunk_id}
              className={styles.item}
              style={{ "--tab": type.colorVar } as CSSProperties}
            >
              <Link
                className={styles.link}
                href={`/${org}/documents/${result.document_id}`}
                onClick={() => reportClick(data.search_id, result.document_id)}
              >
                <span className={styles.top}>
                  <span className={styles.badge}>{type.label}</span>
                  <span className={styles.title}>{result.title}</span>
                </span>
                <span className={styles.snippet}>{highlight(result.snippet, query)}</span>
              </Link>
            </li>
          );
        })}
      </ol>
    </>
  );
}
