import { createRoot } from 'react-dom/client';
import App from './App.tsx';
// 自托管字体：构建时由 Vite 打进 dist，运行时不再依赖 jsdelivr/google fonts
import '@fontsource/noto-sans-sc/400.css';
import '@fontsource/noto-sans-sc/500.css';
import '@fontsource/noto-sans-sc/700.css';
import '@fontsource/noto-sans-sc/900.css';
import '@fontsource/jetbrains-mono/400.css';
import '@fontsource/jetbrains-mono/500.css';
import './index.css';

createRoot(document.getElementById('root')!).render(<App />);
