import "server-only";
import { apiFetch } from "./api";

export interface Me {
  readonly id: string;
  readonly email: string;
}

export interface OrgSummary {
  readonly slug: string;
  readonly name: string;
  readonly role: string;
}

export async function getMe(): Promise<Me | null> {
  const res = await apiFetch("/api/auth/me");
  if (!res.ok) {
    return null;
  }
  return (await res.json()) as Me;
}

export async function getMyOrgs(): Promise<OrgSummary[]> {
  const res = await apiFetch("/api/me/organisations");
  if (!res.ok) {
    return [];
  }
  return (await res.json()) as OrgSummary[];
}
