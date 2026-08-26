import { NextResponse, type NextRequest } from "next/server";

import {
  LOGIN_ROUTE,
  REDIRECT_PARAM,
  SESSION_COOKIE,
  isAuthEnabled,
  isPublicRoute,
} from "@/lib/auth/session";

/**
 * Auth-aware routing skeleton (Next.js `proxy` convention, formerly middleware).
 *
 * When `NEXT_PUBLIC_AUTH_ENABLED=true`, requests without a session cookie are
 * redirected to the login route (preserving the intended destination). When
 * disabled (the default in dev) every request passes through untouched.
 */
export function proxy(request: NextRequest) {
  if (!isAuthEnabled()) {
    return NextResponse.next();
  }

  const { pathname, search } = request.nextUrl;
  if (isPublicRoute(pathname)) {
    return NextResponse.next();
  }

  const hasSession = Boolean(request.cookies.get(SESSION_COOKIE)?.value);
  if (hasSession) {
    return NextResponse.next();
  }

  const loginUrl = request.nextUrl.clone();
  loginUrl.pathname = LOGIN_ROUTE;
  loginUrl.search = "";
  loginUrl.searchParams.set(REDIRECT_PARAM, `${pathname}${search}`);
  return NextResponse.redirect(loginUrl);
}

export const config = {
  // Run on everything except Next internals and static assets.
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\..*).*)"],
};
