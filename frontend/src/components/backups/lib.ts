/** Sizes as a person would say them: three significant digits, binary units. */
export function formatBytes(bytes: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return unit === 0 ? `${value} B` : `${value.toFixed(1)} ${units[unit]}`;
}

/** How a backup came to exist, in the words the page uses. */
export function triggerLabel(trigger: string): string {
  if (trigger === "auto") return "Automatic";
  if (trigger === "upload") return "Uploaded";
  return "Manual";
}
