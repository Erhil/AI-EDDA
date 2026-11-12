import type { GenerationForm } from './generationRequest';

type Props = {
  storedTarget: boolean;
  form: GenerationForm;
  onChange: (form: GenerationForm) => void;
  locked: boolean;
  prerequisite: string;
  error: string;
  preparing: boolean;
  jobId: string;
  jobState: string | null;
  jobError: string;
  draftId: string;
  canStart: boolean;
  canRetry: boolean;
  canCancel: boolean;
  canReset: boolean;
  onStart: () => void;
  onRetry: () => void;
  onCancel: () => void;
  onReset: () => void;
};

export function GenerationControls(props: Props) {
  const change = (field: keyof GenerationForm, value: string) => props.onChange({ ...props.form, [field]: value });
  return <section>
    <h2>Генерация</h2>
    <label>Дорожка <select value={props.form.lane} disabled={props.locked} onChange={e => change('lane', e.target.value)}>
      {[0, 1, 2, 3].map(lane => <option key={lane} value={String(lane)}>{lane + 1}</option>)}
    </select></label>
    {props.storedTarget && <p>Деление задаёт сохранённый профиль цели карты.</p>}
    <label>Деление <select value={props.form.subdivision} disabled={props.locked || props.storedTarget} onChange={e => change('subdivision', e.target.value)}>
      {[1, 2, 4].map(value => <option key={value} value={String(value)}>{value}</option>)}
    </select></label>
    <label>Начало, сек. <input type="text" inputMode="decimal" value={props.form.startSec} disabled={props.locked} onChange={e => change('startSec', e.target.value)} /></label>
    <label>Конец, сек. <input type="text" inputMode="decimal" value={props.form.endSec} disabled={props.locked} onChange={e => change('endSec', e.target.value)} /></label>
    <label>Seed <input type="text" inputMode="numeric" value={props.form.seed} disabled={props.locked} onChange={e => change('seed', e.target.value)} /></label>
    {props.prerequisite && <p>{props.prerequisite}</p>}
    {props.error && <p role="alert">{props.error}</p>}
    {props.preparing && <p role="status">Подготовка запроса…</p>}
    {props.jobId && <p>Задача: {props.jobId}. Состояние: {props.jobState || 'ожидание первого ответа'}</p>}
    {props.jobError && <p role="alert">Ошибка задачи: {props.jobError}</p>}
    {props.draftId && <p>Черновик: {props.draftId}</p>}
    <button type="button" disabled={!props.canStart} onClick={props.onStart}>Начать</button>
    <button type="button" disabled={!props.canRetry} onClick={props.onRetry}>Повторить</button>
    <button type="button" disabled={!props.canCancel} onClick={props.onCancel}>Отменить</button>
    <button type="button" disabled={!props.canReset} onClick={props.onReset}>Новый запрос</button>
  </section>;
}
