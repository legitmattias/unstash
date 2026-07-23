"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import type { Dictionary } from "@/app/lib/i18n/dictionaries";
import styles from "./categories.module.css";

interface Props {
  readonly org: string;
  readonly dict: Dictionary["categories"];
}

type Status = "idle" | "requested" | "inProgress" | "failed";

export function ReclusterButton({ org, dict }: Props) {
  const router = useRouter();
  const [status, setStatus] = useState<Status>("idle");

  async function requestRecluster() {
    try {
      const res = await fetch(`/api/orgs/${org}/clusters/recluster`, { method: "POST" });
      if (res.status === 409) {
        setStatus("inProgress");
      } else if (res.ok) {
        setStatus("requested");
        router.refresh();
      } else {
        setStatus("failed");
      }
    } catch {
      setStatus("failed");
    }
  }

  return (
    <div className={styles.reclusterRow}>
      <button
        className={styles.recluster}
        type="button"
        onClick={requestRecluster}
        disabled={status === "requested"}
      >
        {dict.recluster}
      </button>
      {status !== "idle" ? (
        <p className={styles.status} role="status">
          {dict[status]}
        </p>
      ) : null}
    </div>
  );
}
