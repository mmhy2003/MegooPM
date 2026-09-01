import {
  Globe,
  ShieldCheck,
  ListChecks,
  Network,
  Server,
  Settings,
  ArrowRightLeft,
  Ban,
  FileCode2,
  ShieldAlert,
  Users,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  title: string;
  href: string;
  icon: LucideIcon;
  /** Short description used for the page header / tooltips. */
  description: string;
  /** Only rendered for `admin` users (see `navForRole`). */
  adminOnly?: boolean;
}

/**
 * Primary sidebar navigation for the MegooPM app shell.
 * Order and labels mirror the product areas defined in the PRD.
 */
export const primaryNav: NavItem[] = [
  {
    title: "Proxy Hosts",
    href: "/proxy-hosts",
    icon: Globe,
    description: "Reverse-proxy hosts forwarding traffic to upstream services.",
  },
  {
    title: "Upstream Pools",
    href: "/upstreams",
    icon: Server,
    description: "Backend server pools that proxy hosts and streams forward to.",
  },
  {
    title: "Certificates",
    href: "/certificates",
    icon: ShieldCheck,
    description: "TLS certificates and Let's Encrypt automation.",
  },
  {
    title: "Access Lists",
    href: "/access-lists",
    icon: ListChecks,
    description: "Authorization rules controlling who can reach a host.",
  },
  {
    title: "Streams",
    href: "/streams",
    icon: Network,
    description: "Raw TCP/UDP stream forwarding.",
  },
  {
    title: "Redirection Hosts",
    href: "/redirection-hosts",
    icon: ArrowRightLeft,
    description: "Redirect domains to another domain with a chosen status code.",
  },
  {
    title: "404 Hosts",
    href: "/dead-hosts",
    icon: Ban,
    description: "Park domains and return a 404 for every request.",
  },
  {
    title: "Custom Pages",
    href: "/custom-pages",
    icon: FileCode2,
    description: "HTML pages you author here and reference elsewhere.",
  },
  {
    title: "Security",
    href: "/security",
    icon: ShieldAlert,
    description: "CrowdSec integration, bouncers and blocklists.",
  },
  {
    title: "Users",
    href: "/users",
    icon: Users,
    description: "Accounts and roles for people who sign in to MegooPM.",
    adminOnly: true,
  },
  {
    title: "Settings",
    href: "/settings",
    icon: Settings,
    description: "Instance configuration.",
    adminOnly: true,
  },
];

/** Home / dashboard route the shell redirects to after login. */
export const HOME_ROUTE = "/proxy-hosts";

/**
 * The sidebar items a user with `role` may see. Admin-only entries are
 * hidden from members and from signed-out visitors (`null`/`undefined`);
 * the API's RBAC (403) remains the enforcement.
 */
export function navForRole(role: "admin" | "member" | null | undefined): NavItem[] {
  return primaryNav.filter((item) => !item.adminOnly || role === "admin");
}

/**
 * Pages reachable from the topbar avatar rather than the sidebar. The topbar
 * uses this to title them; they are deliberately absent from `primaryNav`.
 */
export const utilityRoutes: Record<string, string> = {
  "/profile": "Profile",
};
