import { createContext, useContext, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import type { NetRole, NetSummary } from "../types";
import { getNet } from "../api/nets";
import { useAuth } from "../hooks/useAuth";

interface CurrentNetContextValue {
  slug: string;
  net: NetSummary | null;
  role: NetRole | "admin" | null;
  loading: boolean;
  error: string | null;
}

const CurrentNetContext = createContext<CurrentNetContextValue | undefined>(undefined);

export function CurrentNetProvider({ children }: { children: React.ReactNode }) {
  const { slug } = useParams<{ slug: string }>();
  const { user } = useAuth();
  const [net, setNet] = useState<NetSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!slug) return;
    setLoading(true);
    setError(null);
    getNet(slug)
      .then((n) => {
        setNet(n);
        setLoading(false);
        // Only remember slugs that resolve — otherwise a deleted/forbidden net
        // gets stuck as the slug-less redirect target.
        localStorage.setItem("lastNetSlug", slug);
      })
      .catch((e) => { setError(String(e)); setLoading(false); });
  }, [slug]);

  const role: NetRole | "admin" | null = user?.is_admin
    ? "admin"
    : (user?.nets.find((n) => n.slug === slug)?.role ?? null);

  return (
    <CurrentNetContext.Provider
      value={{ slug: slug ?? "", net, role, loading, error }}
    >
      {children}
    </CurrentNetContext.Provider>
  );
}

export function useCurrentNet(): CurrentNetContextValue {
  const ctx = useContext(CurrentNetContext);
  if (!ctx) throw new Error("useCurrentNet() must be used inside <CurrentNetProvider>");
  return ctx;
}
