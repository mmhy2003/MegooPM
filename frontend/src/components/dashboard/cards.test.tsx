import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import {
  CertificatesCard,
  ConfigHealthCard,
  InventoryCard,
  SecurityCard,
  TrafficCard,
} from "@/components/dashboard/cards";

afterEach(() => cleanup());

describe("CertificatesCard", () => {
  it("leads with what needs acting on", () => {
    render(
      <CertificatesCard
        certificates={{ expiring_soon: 2, expired: 1, failed: 0, total: 9 }}
      />,
    );
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText(/expiring/i)).toBeInTheDocument();
  });

  it("says everything is healthy rather than showing bare zeros", () => {
    render(
      <CertificatesCard
        certificates={{ expiring_soon: 0, expired: 0, failed: 0, total: 4 }}
      />,
    );
    expect(screen.getByText(/all healthy/i)).toBeInTheDocument();
  });
});

describe("TrafficCard", () => {
  it("says traffic is unmeasured rather than zero before any scrape", () => {
    // A node that has never reported has unknown connections, not none, and
    // "0" would read as a quiet server rather than a missing measurement.
    render(
      <TrafficCard
        traffic={{
          active_connections: null,
          requests_per_second: null,
          reporting_nodes: 0,
          stale_nodes: 0,
        }}
      />,
    );
    expect(screen.getByText(/no data yet/i)).toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });

  it("shows the numbers when nodes are reporting", () => {
    render(
      <TrafficCard
        traffic={{
          active_connections: 17,
          requests_per_second: 3.5,
          reporting_nodes: 2,
          stale_nodes: 0,
        }}
      />,
    );
    expect(screen.getByText("17")).toBeInTheDocument();
    expect(screen.getByText(/3\.5/)).toBeInTheDocument();
  });

  it("labels the rate as an average rather than implying a live figure", () => {
    // stub_status reports cumulative counters, so the rate is a delta between
    // scrapes. Presenting it as live would misrepresent the data.
    render(
      <TrafficCard
        traffic={{
          active_connections: 1,
          requests_per_second: 2,
          reporting_nodes: 1,
          stale_nodes: 0,
        }}
      />,
    );
    expect(screen.getByText(/avg/i)).toBeInTheDocument();
  });

  it("warns when a node has stopped reporting", () => {
    render(
      <TrafficCard
        traffic={{
          active_connections: 4,
          requests_per_second: 1,
          reporting_nodes: 1,
          stale_nodes: 1,
        }}
      />,
    );
    expect(screen.getByText(/1 node not reporting/i)).toBeInTheDocument();
  });
});

describe("SecurityCard", () => {
  it("says CrowdSec is unavailable rather than showing no threats", () => {
    // "0 active bans" and "CrowdSec is down" mean opposite things.
    render(<SecurityCard security={null} />);
    expect(screen.getByText(/unavailable/i)).toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });

  it("shows the counts when CrowdSec answers", () => {
    render(
      <SecurityCard
        security={{ active_decisions: 12, alerts_24h: 40, top_scenarios: ["http-probing"] }}
      />,
    );
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText(/http-probing/)).toBeInTheDocument();
  });
});

describe("ConfigHealthCard", () => {
  it("calls out nodes that have not applied the current config", () => {
    render(
      <ConfigHealthCard
        config={{
          config_version: 7,
          nodes_total: 3,
          nodes_in_sync: 2,
          nodes_stale: 0,
          converged: false,
        }}
      />,
    );
    expect(screen.getByText(/2 of 3/i)).toBeInTheDocument();
    expect(screen.getByText(/not converged/i)).toBeInTheDocument();
  });

  it("reports a converged cluster plainly", () => {
    render(
      <ConfigHealthCard
        config={{
          config_version: 7,
          nodes_total: 2,
          nodes_in_sync: 2,
          nodes_stale: 0,
          converged: true,
        }}
      />,
    );
    expect(screen.getByText(/in sync/i)).toBeInTheDocument();
  });
});

describe("InventoryCard", () => {
  it("shows enabled hosts against the total", () => {
    render(
      <InventoryCard
        inventory={{
          proxy_hosts_total: 10,
          proxy_hosts_enabled: 8,
          redirection_hosts: 1,
          dead_hosts: 2,
          streams: 3,
        }}
      />,
    );
    expect(screen.getByText(/8 of 10/i)).toBeInTheDocument();
  });
});
