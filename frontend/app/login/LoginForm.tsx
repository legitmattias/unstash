"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import type { Dictionary } from "@/app/lib/i18n/dictionaries";
import styles from "./login.module.css";

interface Props {
  readonly dict: Dictionary["login"];
  readonly appName: string;
  readonly next: string;
}

export function LoginForm({ dict, appName, next }: Props) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(false);
  const [pending, setPending] = useState(false);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(false);
    const body = new URLSearchParams({ username: email, password });
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    });
    if (res.ok) {
      router.push(next);
      router.refresh();
    } else {
      setError(true);
      setPending(false);
    }
  }

  return (
    <main className={styles.wrap}>
      <form className={styles.card} onSubmit={onSubmit} noValidate>
        <span className={styles.brand}>{appName}</span>
        <h1 className={styles.title}>{dict.title}</h1>
        <p className={styles.subtitle}>{dict.subtitle}</p>

        {error ? (
          <p className={styles.error} role="alert">
            {dict.error}
          </p>
        ) : null}

        <label className={styles.field}>
          <span className={styles.label}>{dict.email}</span>
          <input
            className={styles.input}
            type="email"
            name="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </label>
        <label className={styles.field}>
          <span className={styles.label}>{dict.password}</span>
          <input
            className={styles.input}
            type="password"
            name="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>

        <button className={styles.submit} type="submit" disabled={pending}>
          {dict.submit}
        </button>
      </form>
    </main>
  );
}
