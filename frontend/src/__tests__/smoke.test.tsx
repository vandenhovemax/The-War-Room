import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

/**
 * Smoke test — verifies the test framework is wired correctly.
 * Uses minimal components to avoid needing API mocks or context providers.
 */
function TestBadge({ label }: { label: string }) {
  return <span data-testid="badge">{label}</span>;
}

/** Mimics the toast variant styling logic from Toast.tsx */
function ToastMessage({ message, variant }: { message: string; variant: "success" | "error" | "info" }) {
  const styles: Record<string, string> = {
    success: "text-green-500",
    error: "text-red-500",
    info: "text-blue-500",
  };
  return (
    <div data-testid="toast" className={styles[variant]}>
      <p data-testid="toast-message">{message}</p>
      <button data-testid="toast-dismiss">X</button>
    </div>
  );
}

describe("Smoke test", () => {
  it("renders a simple component", () => {
    render(<TestBadge label="PAPER" />);
    expect(screen.getByTestId("badge")).toHaveTextContent("PAPER");
  });

  it("framework assertion works", () => {
    expect(1 + 1).toBe(2);
  });
});

describe("Toast rendering", () => {
  it("renders success toast with message", () => {
    render(<ToastMessage message="Backtest completed" variant="success" />);
    expect(screen.getByTestId("toast-message")).toHaveTextContent("Backtest completed");
    expect(screen.getByTestId("toast")).toHaveClass("text-green-500");
  });

  it("renders error toast with message", () => {
    render(<ToastMessage message="Failed to load data" variant="error" />);
    expect(screen.getByTestId("toast-message")).toHaveTextContent("Failed to load data");
    expect(screen.getByTestId("toast")).toHaveClass("text-red-500");
  });

  it("renders dismiss button", () => {
    render(<ToastMessage message="Info" variant="info" />);
    expect(screen.getByTestId("toast-dismiss")).toBeDefined();
  });
});
