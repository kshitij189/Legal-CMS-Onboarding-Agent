"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useSession } from "@/components/session";
import { ErrorNotice } from "@/components/ui";
import { api, describeError } from "@/lib/api";

export default function Home() {
  const { me, loading, signIn } = useSession();
  const router = useRouter();
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (me) router.replace("/projects");
  }, [me, router]);

  if (loading || me) return <p className="muted">Loading…</p>;

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const value = (name: string) => String(form.get(name) ?? "");
    setBusy(true);
    setError(null);
    try {
      if (mode === "signup") {
        await api("/firms", {
          method: "POST",
          body: {
            firm: { slug: value("slug"), name: value("firm_name") },
            admin: {
              username: value("username"),
              display_name: value("display_name"),
              password: value("password"),
            },
          },
        });
      }
      await signIn(value("username"), value("password"));
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel" style={{ maxWidth: 520 }}>
      <h1>{mode === "signin" ? "Sign in" : "Create a firm"}</h1>
      <p className="muted">
        Onboard a law firm&apos;s intake forms into its case management system: the agent proposes
        mappings, people approve them, and every change is verified against the CMS.
      </p>
      <ErrorNotice error={error} />
      <form onSubmit={submit}>
        {mode === "signup" && (
          <>
            <label htmlFor="firm_name">Firm name</label>
            <input id="firm_name" name="firm_name" required />
            <label htmlFor="slug">Firm identifier (lowercase, dashes)</label>
            <input id="slug" name="slug" required pattern="[a-z0-9][a-z0-9-]{1,63}" />
            <label htmlFor="display_name">Your name</label>
            <input id="display_name" name="display_name" required />
          </>
        )}
        <label htmlFor="username">Username</label>
        <input id="username" name="username" autoComplete="username" required />
        <label htmlFor="password">Password</label>
        <input
          id="password"
          name="password"
          type="password"
          autoComplete={mode === "signin" ? "current-password" : "new-password"}
          minLength={mode === "signup" ? 8 : undefined}
          required
        />
        <div className="row" style={{ marginTop: "1rem" }}>
          <button type="submit" disabled={busy}>
            {mode === "signin" ? "Sign in" : "Create firm and sign in"}
          </button>
          <button
            type="button"
            className="secondary"
            onClick={() => setMode(mode === "signin" ? "signup" : "signin")}
          >
            {mode === "signin" ? "Create a new firm instead" : "I already have an account"}
          </button>
        </div>
      </form>
      <p className="muted">Demo accounts: a.admin / a.specialist (password: demo-password).</p>
    </section>
  );
}
