/** 把后端返回的 AppError / Error / 任意值变成可读文本。
 *
 * 之前页面里用 `String(e)`，后端抛的是 `{code,message,cause,...}` 对象，
 * 结果界面上显示 `[object Object]`。
 */
export function toErrorText(e: unknown): string {
  if (e == null) return "未知错误";
  if (typeof e === "string") return e;
  if (e instanceof Error) return e.message;

  if (typeof e === "object") {
    const obj = e as Record<string, unknown>;
    const message = typeof obj.message === "string" ? obj.message : "";
    const code = typeof obj.code === "string" ? obj.code : "";
    const cause = typeof obj.cause === "string" ? obj.cause : "";
    const detail = typeof obj.detail === "string" ? obj.detail : "";
    const parts = [code ? `[${code}] ${message}` : message, cause, detail].filter(
      (part) => typeof part === "string" && part.trim().length > 0,
    );
    if (parts.length > 0) return parts.join(" —— ");
    try {
      return JSON.stringify(e);
    } catch {
      return String(e);
    }
  }
  return String(e);
}
