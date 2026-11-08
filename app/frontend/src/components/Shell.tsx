import { Link } from 'react-router-dom';
import type { ReactNode } from 'react';

export default function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="sticky top-0 z-30 flex h-14 items-center border-b border-border bg-background px-5">
        <Link to="/" className="flex items-center gap-2">
          <span className="flex h-6 w-6 items-center justify-center rounded-md bg-primary text-sm font-bold text-primary-foreground">M</span>
          <span className="font-display text-base font-bold">Molecules</span>
        </Link>
      </header>
      <main className="min-h-[calc(100vh-56px)] min-w-0">{children}</main>
    </div>
  );
}
