import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';

const root = createRoot(document.getElementById('root')!);
async function start() {
  if (import.meta.env.MODE === 'demo') {
    try {
      const demo = await import('./demo/browser');
      const controls = await demo.startDemo();
      root.render(<React.StrictMode>{controls}<App /></React.StrictMode>);
    } catch (error) {
      root.render(<main><h1>Не удалось запустить демо</h1><p>{error instanceof Error ? error.message : 'Ошибка запуска'}</p><button onClick={() => { localStorage.removeItem('ragnarock-demo-v1'); location.reload(); }}>Сбросить демо и повторить</button></main>);
    }
    return;
  }
  root.render(<React.StrictMode><App /></React.StrictMode>);
}
void start();
