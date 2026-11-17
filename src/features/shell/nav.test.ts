import { describe, expect, it } from "vitest";
import { NAV_ITEMS } from "./nav";

describe("nav", () => {
  it("has the 8 product areas with unique routes", () => {
    expect(NAV_ITEMS).toHaveLength(8);
    const routes = NAV_ITEMS.map((i) => i.to);
    expect(new Set(routes).size).toBe(8);
    expect(routes).toContain("/inference");
    expect(routes).toContain("/models");
  });

  it("every item has label, icon and hint", () => {
    for (const item of NAV_ITEMS) {
      expect(item.label.length).toBeGreaterThan(0);
      expect(item.icon).toBeTruthy();
      expect(item.hint.length).toBeGreaterThan(0);
    }
  });
});
