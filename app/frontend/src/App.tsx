import { Toaster } from '@/components/ui/sonner';
import { TooltipProvider } from '@/components/ui/tooltip';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import type { ReactNode } from 'react';
import Shell from './components/Shell';
import Home from './pages/Home';
import ProjectPage from './pages/ProjectPage';
import CreatePage from './pages/CreatePage';
import PublicApp from './pages/PublicApp';

const queryClient = new QueryClient();

const WithShell = ({ children }: { children: ReactNode }) => <Shell>{children}</Shell>;

const AppRoutes = () => (
  <Routes>
    <Route path="/" element={<WithShell><Home /></WithShell>} />
    <Route path="/create" element={<WithShell><CreatePage /></WithShell>} />
    <Route path="/project/:id" element={<WithShell><ProjectPage /></WithShell>} />
    <Route path="/app/:slug" element={<PublicApp />} />
  </Routes>
);

const App = () => (
  <QueryClientProvider client={queryClient}>
    <TooltipProvider>
      <Toaster />
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </TooltipProvider>
  </QueryClientProvider>
);

export default App;
export { AppRoutes };
