import { NavLink } from 'react-router-dom';
import { Flame, GitCompare, Home } from 'lucide-react';
import type { ReactNode } from 'react';

const ITEMS = [
  { to: '/', label: '首页', icon: Home },
  { to: '/race', label: '对比', icon: GitCompare },
];

export default function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-background text-foreground md:flex">
      <aside className="fixed inset-x-0 bottom-0 z-30 flex h-14 items-center justify-around bg-ink md:static md:h-auto md:w-[72px] md:flex-col md:justify-start md:gap-1 md:py-4">
        <div className="hidden items-center justify-center pb-4 text-ember md:flex" aria-label="Atoms Forge">
          <Flame className="h-6 w-6" />
        </div>
        {ITEMS.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) =>
              `relative flex min-h-[44px] w-14 flex-col items-center justify-center gap-0.5 text-[11px] transition-colors ${
                isActive ? 'text-[hsl(43,50%,96%)]' : 'text-[hsl(40,10%,60%)] hover:text-[hsl(43,50%,96%)]'
              }`
            }
          >
            {({ isActive }) => (
              <>
                {isActive && <span className="absolute left-[-8px] top-2 hidden h-8 w-0.5 bg-ember md:block" />}
                <Icon className="h-5 w-5" aria-hidden />
                <span>{label}</span>
              </>
            )}
          </NavLink>
        ))}
      </aside>
      <main className="min-w-0 flex-1 pb-14 md:pb-0">{children}</main>
    </div>
  );
}
