"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import type { Dictionary } from "@/app/lib/i18n/dictionaries";
import styles from "./SearchBox.module.css";

interface Props {
  readonly org: string;
  readonly initialQuery: string;
  readonly dict: Dictionary["search"];
}

export function SearchBox({ org, initialQuery, dict }: Props) {
  const router = useRouter();
  const [value, setValue] = useState(initialQuery);

  function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    const params = new URLSearchParams(window.location.search);
    const trimmed = value.trim();
    if (trimmed) {
      params.set("q", trimmed);
    } else {
      params.delete("q");
    }
    router.push(`/${org}/search?${params.toString()}`);
  }

  return (
    <form className={styles.form} role="search" onSubmit={onSubmit}>
      <svg
        className={styles.icon}
        width="18"
        height="18"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        aria-hidden="true"
      >
        <circle cx="11" cy="11" r="7" />
        <path d="m21 21-4.3-4.3" />
      </svg>
      <input
        className={styles.input}
        type="search"
        name="q"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder={dict.placeholder}
        aria-label={dict.label}
        autoComplete="off"
      />
      <button className={styles.submit} type="submit">
        {dict.submit}
      </button>
    </form>
  );
}
