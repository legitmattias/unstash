"use client";

import { useRouter } from "next/navigation";
import styles from "./Header.module.css";

interface Props {
  readonly label: string;
}

export function SignOutButton({ label }: Props) {
  const router = useRouter();

  async function signOut() {
    await fetch("/api/auth/logout", { method: "POST" }).catch(() => {});
    router.push("/login");
    router.refresh();
  }

  return (
    <button type="button" className={styles.signout} onClick={signOut}>
      {label}
    </button>
  );
}
