import { Component, type ReactNode } from 'react';
import { Toaster } from '@/components/ui/sonner';
import { TooltipProvider } from '@/components/ui/tooltip';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import Shell from './components/Shell';
import Home from './pages/Home';
import ProjectPage from './pages/ProjectPage';
import CreatePage from './pages/CreatePage';

const queryClient = new QueryClient();

// 捕获渲染期异常，避免一个组件抛错把整页白屏（同时把错误打到控制台便于定位）。
class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null; stack: string }> {
  state = { error: null as Error | null, stack: '' };
  static getDerivedStateFromError(error: Error) {
    return { error, stack: '' };
  }
  componentDidCatch(error: Error, info: { componentStack?: string }) {
    console.error('[ErrorBoundary]', error);
    if (info.componentStack) console.error('[ErrorBoundary] componentStack:', info.componentStack);
    this.setState({ stack: info.componentStack || '' });
  }
  render() {
    if (this.state.error) {
      return (
        <div className="p-8 text-destructive">
          <h1 className="font-display text-lg font-bold">页面出错了</h1>
          <pre className="mt-2 whitespace-pre-wrap text-sm">{this.state.error.message}</pre>
          {this.state.stack && <pre className="mt-3 whitespace-pre-wrap text-xs text-muted-foreground">{this.state.stack}</pre>}
        </div>
      );
    }
    return this.props.children;
  }
}

const WithShell = ({ children }: { children: ReactNode }) => <Shell>{children}</Shell>;

const AppRoutes = () => (
  <Routes>
    <Route path="/" element={<WithShell><Home /></WithShell>} />
    <Route path="/create" element={<WithShell><CreatePage /></WithShell>} />
    <Route path="/project/:id" element={<WithShell><ProjectPage /></WithShell>} />
  </Routes>
);

const App = () => (
  <QueryClientProvider client={queryClient}>
    <TooltipProvider>
      <Toaster />
      <BrowserRouter>
        <ErrorBoundary>
          <AppRoutes />
        </ErrorBoundary>
      </BrowserRouter>
    </TooltipProvider>
  </QueryClientProvider>
);

export default App;
export { AppRoutes };
