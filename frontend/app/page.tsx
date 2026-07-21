import { redirect } from "next/navigation";
import { getMe, getMyOrgs } from "./lib/session";

export default async function Home() {
  const me = await getMe();
  if (!me) {
    redirect("/login");
  }
  const orgs = await getMyOrgs();
  if (orgs.length === 1) {
    redirect(`/${orgs[0].slug}/search`);
  }
  redirect("/orgs");
}
