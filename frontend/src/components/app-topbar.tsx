"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { CircleUser, LogOut, UserCog } from "lucide-react";

import { primaryNav, utilityRoutes } from "@/config/nav";
import { useAuth } from "@/lib/auth/context";
import { ModeToggle } from "@/components/mode-toggle";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { SidebarTrigger } from "@/components/ui/sidebar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

function currentTitle(pathname: string): string {
  const match = primaryNav.find(
    (item) => pathname === item.href || pathname.startsWith(`${item.href}/`),
  );
  return match?.title ?? utilityRoutes[pathname] ?? "Dashboard";
}

export function AppTopbar() {
  const pathname = usePathname();
  const { user, logout } = useAuth();
  const accountLabel = user?.full_name || user?.email || "Account";

  return (
    <header className="bg-background/80 supports-[backdrop-filter]:bg-background/60 sticky top-0 z-10 flex h-14 shrink-0 items-center gap-2 border-b px-4 backdrop-blur">
      <SidebarTrigger className="-ml-1" />
      <Separator orientation="vertical" className="mr-1 data-[orientation=vertical]:h-4" />
      <h1 className="text-sm font-medium">{currentTitle(pathname)}</h1>

      <div className="ml-auto flex items-center gap-1">
        <ModeToggle />
        <DropdownMenu>
          <DropdownMenuTrigger
            render={<Button variant="ghost" size="icon" aria-label="Account" />}
          >
            <CircleUser className="size-5" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuLabel className="truncate">{accountLabel}</DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem render={<Link href="/account" />}>
              <UserCog className="size-4" />
              Account
            </DropdownMenuItem>
            <DropdownMenuItem onClick={logout}>
              <LogOut className="size-4" />
              Sign out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
