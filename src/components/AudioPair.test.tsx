import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AudioPair } from "./AudioPair";

describe("AudioPair", () => {
  it("shows placeholders and disables playback when no audio is available", () => {
    render(<AudioPair source={null} output={null} />);
    expect(screen.getByText(/原声 · 暂无音频/)).toBeInTheDocument();
    expect(screen.getByText(/输出 · 暂无音频/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "播放对照" })).toBeDisabled();
  });

  it("explains that browser preview cannot open local audio", () => {
    render(<AudioPair source={"C:/a.wav"} output={"C:/b.flac"} />);
    expect(screen.getByText(/浏览器预览下不加载本地音频/)).toBeInTheDocument();
  });
});
