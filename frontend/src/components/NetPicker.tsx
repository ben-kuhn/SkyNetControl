import { useEffect, useState } from "react";
import { useNavigate, useParams, useLocation } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { listNets } from "../api/nets";
import type { NetMembershipSummary } from "../types";

/**
 * Dropdown that lets the user switch between nets.
 *
 * - Regular users see only their memberships (from user.nets populated by /auth/me).
 * - Admins also call /api/nets to see nets they may not be explicitly members of.
 * - On selection, navigates to the same sub-page under the new slug.
 */
export function NetPicker({ slug: slugOverride }: { slug?: string } = {}) {
  const { user } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const params = useParams<{ slug: string }>();
  // When mounted on a global page the URL has no slug; the Sidebar passes its
  // localStorage-derived fallback so the picker shows the right net.
  const currentSlug = slugOverride ?? params.slug;
  const [adminNets, setAdminNets] = useState<NetMembershipSummary[]>([]);

  useEffect(() => {
    if (user?.is_admin) {
      listNets()
        .then(setAdminNets)
        .catch(() => { /* non-fatal */ });
    }
  }, [user?.is_admin]);

  if (!user) return null;

  const nets: NetMembershipSummary[] = user.is_admin ? adminNets : user.nets;

  if (nets.length <= 1) {
    // Only one net — show it as static text rather than a picker
    const label = nets[0]?.name ?? currentSlug ?? "";
    return (
      <span className="font-semibold text-sm text-text-primary truncate max-w-[160px]" title={label}>
        {label}
      </span>
    );
  }

  function handleChange(e: React.ChangeEvent<HTMLSelectElement>) {
    const newSlug = e.target.value;
    if (!newSlug || newSlug === currentSlug) return;

    // Determine the current sub-page under /nets/:slug/…
    // location.pathname might be /nets/old-slug/schedule — extract suffix
    const prefix = `/nets/${currentSlug}`;
    const suffix = location.pathname.startsWith(prefix)
      ? location.pathname.slice(prefix.length) || "/schedule"
      : "/schedule";

    navigate(`/nets/${newSlug}${suffix}`);
  }

  return (
    <select
      value={currentSlug ?? ""}
      onChange={handleChange}
      className="bg-bg-elevated border border-border text-text-primary text-sm rounded-md px-2 py-1 focus:outline-none focus:ring-2 focus:ring-accent/50 focus:border-accent max-w-[200px] truncate"
      aria-label="Switch net"
    >
      {nets.map((n) => (
        <option key={n.slug} value={n.slug}>
          {n.name}
        </option>
      ))}
    </select>
  );
}
