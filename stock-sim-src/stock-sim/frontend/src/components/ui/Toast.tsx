"use client";

import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

interface ToastItem {
  id: number;
  text: string;
  tone?: "up" | "dn" | "err";
}

const Ctx = createContext<(text: string, tone?: ToastItem["tone"]) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const push = useCallback((text: string, tone?: ToastItem["tone"]) => {
    const id = Date.now() + Math.random();
    setItems((xs) => [...xs, { id, text, tone }].slice(-4));
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), 5_000);
  }, []);
  return (
    <Ctx.Provider value={push}>
      {children}
      <div className="toasts" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.tone ? `toast-${t.tone}` : ""}`}>
            {t.text}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export const useToast = () => useContext(Ctx);
