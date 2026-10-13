"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useSession } from "@/components/session";

export function TopBar() {
  const { me, signOut } = useSession();
  const router = useRouter();
  return (
    <header className="top">
      <strong>Legal CMS Onboarding</strong>
      {me && (
        <nav aria-label="Main">
          <Link href="/projects">Projects</Link>
        </nav>
      )}
      {me && (
        <div className="who">
          {me.user.display_name} ({me.user.role.replace("_", " ")}) · {me.firm.name}{" "}
          <button
            className="secondary"
            onClick={async () => {
              await signOut();
              router.push("/");
            }}
          >
            Sign out
          </button>
        </div>
      )}
    </header>
  );
}
