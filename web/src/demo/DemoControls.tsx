import { useState } from 'react';
import { recoverDemoStorage } from './store';
import './demo.css';
export type Scenario = 'normal' | 'slow' | 'error';
export function resetDemo() { recoverDemoStorage(localStorage); location.reload(); }
export function DemoControls({ setScenario }: { setScenario: (value: Scenario) => void }) {
 const [scenario, set] = useState<Scenario>('normal');
 return <aside className="demo-controls" aria-label="Демо-режим"><strong>Демо-режим.</strong> Данные хранятся локально в браузере. Анализ и данные синтетические. Доступны метаданные, добавление, перемещение и удаление нот, выбор анализа. Генерация доступна только для дорожки 0, деления 1, seed 0 и всего аудио от 0 до 30 секунд. Задания и черновики хранятся только до перезагрузки страницы. Применение черновика и другие операции не поддерживаются. <label>Сценарий <select aria-label="Сценарий демо" value={scenario} onChange={e => { const value = e.target.value as Scenario; set(value); setScenario(value); }}><option value="normal">Обычный</option><option value="slow">Медленный</option><option value="error">Ошибка</option></select></label> <button onClick={resetDemo}>Сбросить демо</button></aside>;
}
