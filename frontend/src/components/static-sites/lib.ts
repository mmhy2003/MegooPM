import type { FolderStatus } from "@/lib/api";

/** What the list and the save toast say about a folder that is not `ok`. */
export const FOLDER_STATUS_TEXT: Record<Exclude<FolderStatus, "ok">, string> = {
  missing: "Folder not found",
  no_index: "No index.html",
  unreadable: "Can't read folder",
  symlink: "Symlinks aren't served",
  unmounted: "Sites folder not mounted",
};

/** Mirrors the API's folder rule: relative segments of letters, digits, `.`,
 *  `_` and `-`, none starting with `.`, so a path can never climb out of /sites
 *  or name a hidden folder. */
const FOLDER_RE = /^[A-Za-z0-9_-][A-Za-z0-9._-]*(\/[A-Za-z0-9_-][A-Za-z0-9._-]*)*$/;
export const FOLDER_MAX_LENGTH = 255;

export function isValidFolder(folder: string): boolean {
  return folder.length <= FOLDER_MAX_LENGTH && FOLDER_RE.test(folder);
}
