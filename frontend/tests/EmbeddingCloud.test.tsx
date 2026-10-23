/**
 * EmbeddingCloud.test.tsx
 * =======================
 * The 3D embedding map on the search page (F17). jsdom has no WebGL, so the
 * three.js scene is replaced by a stub that reports what it was given; what
 * is under test is everything around it:
 *
 *   * nothing is fetched until the map is opened
 *   * the table fallback: filename, snippet, coordinates, search hits
 *   * no WebGL -> the table is the only view
 *   * the truncation note, the error and empty states
 *   * reduced motion reaches the scene
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import EmbeddingCloud from "@/components/EmbeddingCloud";
import { ApiError } from "@/lib/api";

const getEmbeddingMap = vi.fn();
const webgl = { value: true };

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, getEmbeddingMap: (...a: unknown[]) => getEmbeddingMap(...a) } };
});

// next/dynamic -> the stub scene, synchronously.
vi.mock("next/dynamic", () => ({
  default: () =>
    function SceneStub(props: { points: unknown[]; hits: Set<string>; reduced: boolean; active: boolean }) {
      return (
        <div
          data-testid="scene"
          data-count={props.points.length}
          data-hits={[...props.hits].join(",")}
          data-reduced={String(props.reduced)}
        />
      );
    },
}));

const MAP = {
  points: [
    { chunk_id: "c1", document_id: "d1", filename: "handbook.pdf", snippet: "Annual leave is 25 days", x: 0.1, y: -0.5, z: 1 },
    { chunk_id: "c2", document_id: "d2", filename: "expenses.pdf", snippet: "Claim within 30 days", x: -1, y: 0.25, z: 0 },
  ],
  total: 2500,
  truncated: true,
  explained_variance: [0.3, 0.2, 0.1],
};

function mockMedia(reduced: boolean) {
  window.matchMedia = ((query: string) => ({
    matches: reduced && query.includes("reduce"),
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia;
}

beforeEach(() => {
  getEmbeddingMap.mockReset().mockResolvedValue(MAP);
  webgl.value = true;
  mockMedia(false);
  HTMLCanvasElement.prototype.getContext = vi.fn(() => (webgl.value ? ({} as never) : null)) as never;
});

async function open(props: { hitIds?: string[] } = {}) {
  const user = userEvent.setup();
  render(<EmbeddingCloud projectId="p1" {...props} />);
  await user.click(screen.getByRole("button", { name: /show embedding map/i }));
  return user;
}

describe("EmbeddingCloud", () => {
  it("fetches nothing until opened, then the project's map", async () => {
    render(<EmbeddingCloud projectId="p1" />);
    expect(getEmbeddingMap).not.toHaveBeenCalled();
    await userEvent.setup().click(screen.getByRole("button", { name: /show embedding map/i }));
    expect(getEmbeddingMap).toHaveBeenCalledWith("p1");
    expect(await screen.findByTestId("scene")).toHaveAttribute("data-count", "2");
  });

  it("says when the map is a sample, and how much variance it keeps", async () => {
    await open();
    expect(await screen.findByText(/Showing 2 of 2500 chunks/)).toBeInTheDocument();
    expect(screen.getByText(/keep 60% of the variation/)).toBeInTheDocument();
  });

  it("passes the search hits to the scene and marks them in the table", async () => {
    const user = await open({ hitIds: ["c2"] });
    expect(await screen.findByTestId("scene")).toHaveAttribute("data-hits", "c2");
    expect(screen.getByText(/1 search hit highlighted/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /view as table/i }));
    const table = screen.getByRole("table");
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("handbook.pdf");
    expect(rows[0]).toHaveTextContent("Annual leave is 25 days");
    expect(rows[0]).toHaveTextContent("0.10, -0.50, 1.00");
    expect(rows[0]).toHaveAttribute("data-hit", "false");
    expect(rows[1]).toHaveAttribute("data-hit", "true");
    expect(screen.queryByTestId("scene")).not.toBeInTheDocument();
  });

  it("falls back to the table without WebGL", async () => {
    webgl.value = false;
    await open();
    expect(await screen.findByRole("table")).toBeInTheDocument();
    expect(screen.queryByTestId("scene")).not.toBeInTheDocument();
    expect(screen.getByText(/cannot draw 3D graphics/)).toBeInTheDocument();
  });

  it("keeps the scene still under reduced motion", async () => {
    mockMedia(true);
    await open();
    expect(await screen.findByTestId("scene")).toHaveAttribute("data-reduced", "true");
  });

  it("shows a failure as an alert", async () => {
    getEmbeddingMap.mockRejectedValue(new ApiError("Project not found.", 404));
    await open();
    expect(await screen.findByRole("alert")).toHaveTextContent("Project not found.");
  });

  it("explains an empty project", async () => {
    getEmbeddingMap.mockResolvedValue({ points: [], total: 0, truncated: false, explained_variance: [0, 0, 0] });
    await open();
    expect(await screen.findByText(/No processed chunks yet/)).toBeInTheDocument();
  });

  it("fetches once, however often it is toggled", async () => {
    const user = await open();
    await screen.findByTestId("scene");
    await user.click(screen.getByRole("button", { name: /hide embedding map/i }));
    await user.click(screen.getByRole("button", { name: /show embedding map/i }));
    await waitFor(() => expect(screen.getByTestId("scene")).toBeInTheDocument());
    expect(getEmbeddingMap).toHaveBeenCalledTimes(1);
  });
});
