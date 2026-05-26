"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Github } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";

import webConfig from "@/constants/common-env";
import { getValidatedAuthSession } from "@/lib/auth-session";
import { cn } from "@/lib/utils";
import { clearStoredAuthSession, type StoredAuthSession } from "@/store/auth";

const adminNavItems = [
  { href: "/image", label: "试验" },
  { href: "/accounts", label: "号池管理" },
  { href: "/image-tasks", label: "任务管理" },
  { href: "/image-manager", label: "图片管理" },
  { href: "/logs", label: "日志管理" },
  { href: "/settings", label: "设置" },
];

const userNavItems = [{ href: "/image", label: "试验" }];

export function TopNav() {
  const pathname = usePathname();
  const router = useRouter();
  const [session, setSession] = useState<StoredAuthSession | null | undefined>(undefined);

  useEffect(() => {
    let active = true;

    const load = async () => {
      if (pathname === "/login") {
        if (!active) {
          return;
        }
        setSession(null);
        return;
      }

      const storedSession = await getValidatedAuthSession();
      if (!active) {
        return;
      }
      setSession(storedSession);
    };

    void load();
    return () => {
      active = false;
    };
  }, [pathname]);

  const handleLogout = async () => {
    await clearStoredAuthSession();
    router.replace("/login");
  };

  if (pathname === "/login" || session === undefined || !session) {
    return null;
  }

  const navItems = session.role === "admin" ? adminNavItems : userNavItems;
  const roleLabel = session.role === "admin" ? "管理员" : "普通用户";
  const displayName = session.name.trim() || roleLabel;

  return (
    <header className="sticky top-0 z-40 rounded-b-[28px] border border-t-0 border-white/70 bg-[rgba(250,247,239,0.78)] shadow-[0_18px_60px_-46px_rgba(68,64,60,0.65)] backdrop-blur-xl sm:top-3 sm:rounded-[28px] sm:border-t">
      <div className="flex min-h-14 flex-col gap-2 px-3 py-3 sm:h-14 sm:flex-row sm:items-center sm:justify-between sm:gap-3 sm:px-5 sm:py-0">
        <div className="flex items-center justify-between gap-2 sm:justify-start sm:gap-3">
          <Link
            href="/image"
            className="inline-flex shrink-0 items-center gap-2 rounded-full py-1 pr-2 text-[15px] font-bold tracking-tight text-stone-950 transition hover:text-stone-700"
            aria-label="webchat2api 管理后台首页"
          >
            <Image
              src="/webchat2api-logo.png"
              alt="webchat2api logo"
              width={30}
              height={30}
              className="size-7.5 rounded-xl border border-white/80 bg-white/70 object-contain shadow-sm"
              priority
            />
            <span className="leading-none">webchat2api</span>
          </Link>
          <a
            href="https://github.com/zqbxdev/webchat2api"
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-sm text-stone-500 transition hover:bg-white/55 hover:text-stone-800"
            aria-label="GitHub repository"
          >
            <Github className="size-4" />
            <span className="hidden md:inline">webchat2api</span>
          </a>
          <button
            type="button"
            className="ml-auto shrink-0 rounded-full px-2.5 py-1 text-xs text-stone-500 transition hover:bg-white/60 hover:text-stone-800 sm:hidden"
            onClick={() => void handleLogout()}
          >
            退出
          </button>
        </div>
        <nav className="hide-scrollbar -mx-1 flex min-w-0 flex-1 gap-1 overflow-x-auto px-1 sm:mx-0 sm:justify-center sm:gap-1.5 sm:overflow-visible sm:px-0">
          {navItems.map((item) => {
            const active = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "relative shrink-0 whitespace-nowrap rounded-full px-3 py-1.5 text-[13px] font-medium transition sm:text-[14px]",
                  active
                    ? "bg-stone-900 text-white shadow-[0_12px_26px_-18px_rgba(28,25,23,0.9)]"
                    : "text-stone-500 hover:bg-white/55 hover:text-stone-900",
                )}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="hidden items-center justify-end gap-2 sm:flex sm:gap-2">
          <span className="hidden rounded-full border border-stone-200/80 bg-white/55 px-2.5 py-1 text-[10px] font-medium text-stone-500 sm:inline-block sm:text-[11px]">
            {roleLabel} · {displayName}
          </span>
          <span className="hidden rounded-full border border-stone-200/80 bg-white/55 px-2.5 py-1 text-[10px] font-medium text-stone-500 sm:inline-block sm:text-[11px]">
            v{webConfig.appVersion}
          </span>
          <button
            type="button"
            className="rounded-full px-2.5 py-1 text-xs text-stone-500 transition hover:bg-white/60 hover:text-stone-800 sm:text-sm"
            onClick={() => void handleLogout()}
          >
            退出
          </button>
        </div>
      </div>
    </header>
  );
}
