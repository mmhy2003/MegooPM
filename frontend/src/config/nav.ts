import {
  Globe,
  ShieldCheck,
  ListChecks,
  Network,
  ShieldAlert,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  title: string;
  href: string;
  icon: LucideIcon;
  /** Short description used for the page header / tooltips. */
  description: string;
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
    title: "Security",
    href: "/security",
    icon: ShieldAlert,
    description: "CrowdSec integration, bouncers and blocklists.",
  },
];

/** Home / dashboard route the shell redirects to after login. */
export const HOME_ROUTE = "/proxy-hosts";
