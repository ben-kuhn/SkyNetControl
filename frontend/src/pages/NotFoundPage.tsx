import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <div className="min-h-screen bg-bg-base flex items-center justify-center p-4">
      <div className="text-center">
        <h1 className="text-4xl font-bold text-text-primary mb-2">404</h1>
        <p className="text-text-muted mb-6">Page not found</p>
        <Link
          to="/schedule"
          className="text-accent hover:text-accent-hover transition-colors text-sm"
        >
          Back to schedule
        </Link>
      </div>
    </div>
  );
}
