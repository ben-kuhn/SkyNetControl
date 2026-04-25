import { useToast } from "../context/ToastContext";

const typeClasses = {
  success: "border-success text-success",
  error: "border-danger text-danger",
  info: "border-accent text-accent",
};

export function ToastContainer() {
  const { toasts, removeToast } = useToast();

  if (toasts.length === 0) return null;

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className={`
            rounded-md border bg-bg-surface px-4 py-3 text-sm shadow-lg
            flex items-center gap-3
            ${typeClasses[toast.type]}
          `}
        >
          <span className="text-text-secondary">{toast.message}</span>
          <button
            onClick={() => removeToast(toast.id)}
            className="text-text-muted hover:text-text-primary ml-auto"
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
