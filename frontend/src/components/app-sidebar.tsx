"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useSyncExternalStore } from "react";
import { ChevronsLeft, ChevronsRight } from "lucide-react";

import { navForRole } from "@/config/nav";
import { APP_NAME } from "@/lib/env";
import { useAuth } from "@/lib/auth/context";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  useSidebar,
} from "@/components/ui/sidebar";

export function isActivePath(pathname: string, href: string): boolean {
  // "/" is a prefix of every path, so the dashboard would otherwise light up on
  // every page. It matches only itself.
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

function Logo({ className }: { className?: string }) {
  return (
    <Image
      src="/logo.png"
      alt={`${APP_NAME} logo`}
      width={32}
      height={32}
      priority
      className={cn("size-8 shrink-0", className)}
    />
  );
}

function subscribeNever() {
  return () => {};
}

/** Whether the visitor is on a Mac, where the shortcut is ⌘B rather than Ctrl+B.
 *  `false` on the server, so the first render matches and a Mac corrects it. */
function useIsMac(): boolean {
  return useSyncExternalStore(
    subscribeNever,
    () => /Mac|iPhone|iPad/.test(window.navigator.platform),
    () => false,
  );
}

const KBD =
  "inline-flex h-5 min-w-5 items-center justify-center rounded-sm bg-background/20 px-1 " +
  "font-mono text-[11px] font-medium";

/** The sidebar's own collapse/expand toggle, with Ctrl+B / ⌘B in its tooltip. */
function SidebarToggle({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const isMac = useIsMac();
  const label = collapsed ? "Expand sidebar" : "Collapse sidebar";
  const Icon = collapsed ? ChevronsRight : ChevronsLeft;
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label={label}
            aria-expanded={!collapsed}
            aria-keyshortcuts={isMac ? "Meta+B" : "Control+B"}
            onClick={onToggle}
            className="text-muted-foreground shrink-0"
          />
        }
      >
        <Icon />
      </TooltipTrigger>
      <TooltipContent side={collapsed ? "right" : "bottom"}>
        {label}
        <span className="flex gap-0.5">
          <kbd data-slot="kbd" className={KBD}>
            {isMac ? "⌘" : "Ctrl"}
          </kbd>
          <kbd data-slot="kbd" className={KBD}>
            B
          </kbd>
        </span>
      </TooltipContent>
    </Tooltip>
  );
}

export function AppSidebar() {
  const pathname = usePathname();
  const { user } = useAuth();
  const items = navForRole(user?.role);
  const { state, isMobile, toggleSidebar } = useSidebar();
  const collapsed = state === "collapsed" && !isMobile;

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem
            className={cn("flex items-center gap-1", collapsed && "flex-col gap-2")}
          >
            {collapsed ? (
              // Collapsed: the toggle on top, the logo kept right below it.
              <>
                <SidebarToggle collapsed onToggle={toggleSidebar} />
                <SidebarMenuButton size="lg" tooltip={APP_NAME} render={<Link href="/" />}>
                  <Logo />
                </SidebarMenuButton>
              </>
            ) : (
              <>
                <SidebarMenuButton
                  size="lg"
                  tooltip={APP_NAME}
                  render={<Link href="/" />}
                  className="min-w-0 flex-1"
                >
                  <Logo />
                  <div className="grid flex-1 text-left text-sm leading-tight">
                    <span className="truncate font-semibold">{APP_NAME}</span>
                    <span className="text-muted-foreground truncate text-xs">
                      Proxy Manager
                    </span>
                  </div>
                </SidebarMenuButton>
                {/* On phones the sidebar is a drawer the top bar opens and
                    closes; collapsing to icons is a desktop layout. */}
                {isMobile ? null : <SidebarToggle collapsed={false} onToggle={toggleSidebar} />}
              </>
            )}
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Manage</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {items.map((item) => (
                <SidebarMenuItem key={item.href}>
                  <SidebarMenuButton
                    isActive={isActivePath(pathname, item.href)}
                    tooltip={item.title}
                    render={<Link href={item.href} />}
                  >
                    <item.icon />
                    <span>{item.title}</span>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      <SidebarRail />
    </Sidebar>
  );
}
