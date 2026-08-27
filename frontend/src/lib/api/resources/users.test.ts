import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api/client";
import { USER_ROLE_LABELS, USER_ROLES, users } from "@/lib/api/resources/users";

describe("users resource", () => {
  afterEach(() => vi.restoreAllMocks());

  it("targets the users collection and members", async () => {
    vi.spyOn(api, "get").mockResolvedValue([] as never);
    vi.spyOn(api, "post").mockResolvedValue({} as never);
    vi.spyOn(api, "patch").mockResolvedValue({} as never);
    vi.spyOn(api, "put").mockResolvedValue(undefined as never);
    vi.spyOn(api, "delete").mockResolvedValue(undefined as never);

    await users.list();
    await users.create({
      email: "a@b.c",
      password: "password123",
      full_name: "",
      role: "member",
      is_active: true,
    });
    await users.update(7, { role: "admin" });
    await users.resetPassword(7, { password: "brandnew123" });
    await users.remove(7);
    await users.updateMe({ full_name: "Me" });
    await users.changeMyPassword({ new_password: "brandnew123" });

    expect(api.get).toHaveBeenCalledWith("/api/v1/users");
    expect(api.post).toHaveBeenCalledWith(
      "/api/v1/users",
      expect.objectContaining({ email: "a@b.c" }),
    );
    expect(api.patch).toHaveBeenCalledWith("/api/v1/users/7", { role: "admin" });
    expect(api.put).toHaveBeenCalledWith("/api/v1/users/7/password", { password: "brandnew123" });
    expect(api.delete).toHaveBeenCalledWith("/api/v1/users/7");
    expect(api.patch).toHaveBeenCalledWith("/api/v1/users/me", { full_name: "Me" });
    expect(api.put).toHaveBeenCalledWith("/api/v1/users/me/password", {
      new_password: "brandnew123",
    });
  });

  it("labels every role", () => {
    for (const role of USER_ROLES) expect(USER_ROLE_LABELS[role]).toBeTruthy();
  });
});
