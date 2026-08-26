import { redirect } from "next/navigation";

import { HOME_ROUTE } from "@/config/nav";

/** The shell has no dedicated dashboard yet; land on the first product area. */
export default function IndexPage() {
  redirect(HOME_ROUTE);
}
