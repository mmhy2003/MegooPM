"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { primaryNav, utilityRoutes } from "@/config/nav";
import { useAuth } from "@/lib/auth/context";
import { ModeToggle } from "@/components/mode-toggle";
import { displayName, initials } from "@/components/users/lib";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Separator } from "@/components/ui/separator";
import { SidebarTrigger } from "@/components/ui/sidebar";

function currentTitle(pathname: string): string {
  const match = primaryNav.find(
    (item) => pathname === item.href || pathname.startsWith(`${item.href}/`),
  );
  return match?.title ?? utilityRoutes[pathname] ?? "Dashboard";
}

export function AppTopbar() {
  const pathname = usePathname();
  const { user } = useAuth();
  const name = user ? displayName(user) : "Profile";

  return (
    <header className="bg-background/80 supports-[backdrop-filter]:bg-background/60 sticky top-0 z-10 flex h-14 shrink-0 items-center gap-2 border-b px-4 backdrop-blur">
      <SidebarTrigger className="-ml-1" />
      <Separator orientation="vertical" className="mr-1 data-[orientation=vertical]:h-4" />
      <h1 className="text-sm font-medium">{currentTitle(pathname)}</h1>

      <div className="ml-auto flex items-center gap-1">
        <ModeToggle />
        {/* The avatar opens the profile page directly; sign-out lives there. */}
        <Link
          href="/profile"
          aria-label="Profile"
          title={name}
          className="rounded-full outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Avatar className="size-8">
            <AvatarFallback className="bg-primary text-xs font-semibold text-primary-foreground">
              {initials(user)}
            </AvatarFallback>
          </Avatar>
        </Link>
      </div>
    </header>
  );
}
