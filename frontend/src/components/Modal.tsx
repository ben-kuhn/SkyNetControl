import { useEffect, useRef } from "react";
import type { ReactNode } from "react";

type ModalSize = "sm" | "lg" | "xl";

const SIZE_CLASSES: Record<ModalSize, string> = {
  sm: "max-w-md",
  lg: "max-w-2xl",
  xl: "max-w-5xl",
};

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  // Backdrop clicks discard unsaved work silently — operator lost template
  // edits multiple times before this defaulted to false (backlog item 2).
  // Opt-in only for modals where a stray click *should* dismiss.
  closeOnBackdropClick?: boolean;
  // Width preset. Existing modals stay at "sm" by default; opt up to "lg"
  // for forms with side-by-side fields, "xl" for the check-in editor when
  // a Winlink form view is rendered alongside the fields.
  size?: ModalSize;
  // Optional sticky footer (typically Save/Cancel) — pinned at the bottom
  // of the modal even when the children area scrolls. Without this, tall
  // content pushes the buttons off-screen on short viewports.
  footer?: ReactNode;
}

export function Modal({
  open,
  onClose,
  title,
  children,
  closeOnBackdropClick = false,
  size = "sm",
  footer,
}: ModalProps) {
  const overlayRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 z-[1100] flex items-center justify-center bg-black/60 p-4"
      onClick={(e) => {
        if (closeOnBackdropClick && e.target === overlayRef.current) onClose();
      }}
    >
      <div
        className={`w-full ${SIZE_CLASSES[size]} max-h-[90vh] flex flex-col rounded-lg bg-bg-surface border border-border shadow-xl`}
      >
        <div className="flex items-center justify-between p-6 pb-4 flex-shrink-0">
          <h2 className="text-lg font-semibold text-text-primary">{title}</h2>
          <button
            onClick={onClose}
            className="text-text-muted hover:text-text-primary transition-colors"
          >
            <svg
              className="h-5 w-5"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M6 18L18 6M6 6l12 12"
              />
            </svg>
          </button>
        </div>
        <div className="px-6 pb-6 overflow-y-auto flex-1 min-h-0">{children}</div>
        {footer && (
          <div className="px-6 py-4 border-t border-border bg-bg-surface flex-shrink-0 rounded-b-lg">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}
