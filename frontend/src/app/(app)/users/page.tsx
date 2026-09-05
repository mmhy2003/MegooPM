import type { Metadata } from "next";

import { AdminOnly } from "@/components/admin-only";
import { UsersView } from "@/components/users/users-view";

export const metadata: Metadata = { title: "Users" };

export default function UsersPage() {
  return (
    <AdminOnly>
      <UsersView />
    </AdminOnly>
  );
}
