import {
  AlertCircle,
  ArrowUpRight,
  Check,
  CheckCircle2,
  LoaderCircle,
  RefreshCw,
  Search,
  X,
} from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
export function Badge({ status, label }: { status: string; label?: string }) {
  return (
    <span className={`badge ${status}`}>
      <span />
      {label ?? status.replaceAll("_", " ")}
    </span>
  );
}
export function Loading({
  label = "Loading workspace data…",
}: {
  label?: string;
}) {
  return (
    <div className="state-block">
      <LoaderCircle className="spin" size={24} />
      <p>{label}</p>
    </div>
  );
}
export function ErrorState({
  message,
  retry,
}: {
  message: string;
  retry?: () => void;
}) {
  return (
    <div className="state-block error-state">
      <AlertCircle size={28} />
      <h3>Unable to load this workspace</h3>
      <p>
        {message === "Failed to fetch"
          ? "The platform API is unavailable. Start the backend and try again."
          : message}
      </p>
      {retry && (
        <button className="button secondary" onClick={retry}>
          <RefreshCw size={15} />
          Try again
        </button>
      )}
    </div>
  );
}
export function Empty({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="state-block">
      <span className="empty-symbol">◇</span>
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}
export function SearchField({
  value,
  onChange,
  placeholder = "Search…",
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  return (
    <label className="search-field">
      <Search size={17} />
      <input
        aria-label={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
      />
      {value && (
        <button
          className="icon-button"
          aria-label="Clear search"
          onClick={() => onChange("")}
        >
          <X size={14} />
        </button>
      )}
    </label>
  );
}
export function PageHeading({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow: string;
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions && <div className="heading-actions">{actions}</div>}
    </div>
  );
}
export function PanelHeading({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
}) {
  return (
    <div className="panel-heading">
      <div>
        <h2>{title}</h2>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}
export function TextLink({
  children,
  onClick,
}: {
  children: ReactNode;
  onClick: () => void;
}) {
  return (
    <button className="text-link" onClick={onClick}>
      {children}
      <ArrowUpRight size={14} />
    </button>
  );
}
export function Modal({
  title,
  subtitle,
  onClose,
  children,
  className = "",
}: {
  title: string;
  subtitle?: string;
  onClose: () => void;
  children: ReactNode;
  className?: string;
}) {
  const dialog = useRef<HTMLElement>(null);
  const close = useRef(onClose);
  const returnFocusTo = useRef(
    document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null,
  );
  useEffect(() => {
    close.current = onClose;
  }, [onClose]);
  useEffect(() => {
    const focusableElements = () =>
      Array.from(
        dialog.current?.querySelectorAll<HTMLElement>(
          'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [tabindex]:not([tabindex="-1"])',
        ) ?? [],
      ).filter(
        (element) => element.tabIndex >= 0 && element.getClientRects().length,
      );

    // React autofocus may already have selected an input. After StrictMode's
    // effect replay, restore focus inside the dialog without changing its opener.
    if (!dialog.current?.contains(document.activeElement)) {
      const focusable = focusableElements();
      const initial = focusable.find((element) =>
        element.matches("[autofocus], input, select, textarea"),
      );
      (initial ?? focusable[0] ?? dialog.current)?.focus();
    }

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        close.current();
      }
      if (e.key === "Tab") {
        const focusable = focusableElements();
        if (!focusable.length) {
          e.preventDefault();
          dialog.current?.focus();
          return;
        }
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        const focusOutside = !dialog.current?.contains(document.activeElement);
        if (e.shiftKey && (document.activeElement === first || focusOutside)) {
          e.preventDefault();
          last.focus();
        } else if (
          !e.shiftKey &&
          (document.activeElement === last || focusOutside)
        ) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
      if (returnFocusTo.current?.isConnected) returnFocusTo.current.focus();
    };
  }, []);
  return (
    <div
      className="modal-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <section
        ref={dialog}
        className={`modal ${className}`.trim()}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
      >
        <header>
          <div>
            <h2>{title}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
          <button
            className="icon-button"
            aria-label="Close dialog"
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}
export function Toast({
  message,
  onClose,
}: {
  message: string;
  onClose: () => void;
}) {
  useEffect(() => {
    const timer = setTimeout(onClose, 4500);
    return () => clearTimeout(timer);
  }, [message, onClose]);
  return (
    <div className="toast" role="status">
      <CheckCircle2 size={18} />
      {message}
      <button
        aria-label="Dismiss notification"
        className="icon-button"
        onClick={onClose}
      >
        <X size={14} />
      </button>
    </div>
  );
}
export function CopyButton({
  text,
  label = "Copy",
}: {
  text: string;
  label?: string;
}) {
  const [copied, setCopied] = useState(false),
    [failed, setFailed] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setFailed(true);
    }
  }
  return (
    <button type="button" className="button secondary small" onClick={copy}>
      {copied ? <Check size={14} /> : null}
      {failed ? "Select text to copy" : copied ? "Copied" : label}
    </button>
  );
}
