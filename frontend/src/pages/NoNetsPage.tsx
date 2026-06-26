/**
 * Shown to authenticated users who have no net memberships and are not admins.
 * Admins always have access via /api/nets (they see all nets).
 */
export function NoNetsPage() {
  return (
    <div className="min-h-screen bg-bg-base flex items-center justify-center p-4">
      <div className="max-w-md text-center">
        <h1 className="text-2xl font-bold text-text-primary mb-2">No nets yet</h1>
        <p className="text-text-muted text-sm">
          You don&apos;t have access to any nets. Contact a net administrator to be added
          to a net.
        </p>
      </div>
    </div>
  );
}
