import { displayValue, type Denomination, type FieldValue, type SchemaField, type StructuredField } from './api'
import { Field } from './ui'

const verification: Record<string, string> = {
  verified: '검증됨', rejected: '불일치', uncertain: '확인 필요', confirmed: '확인됨', unconfirmed: '미확인',
}
const visibility = { public: '공개용', verification_only: '본인확인용', restricted: '비공개', discard: '미수집' }

export function StandardizedValues({ fields, categoryNames }: {
  fields: StructuredField[]; categoryNames: Record<string, string | null | undefined>
}) {
  return <dl className="values">
    {[...fields].sort((first, second) => first.display_order - second.display_order).map(field => <div key={field.field_key}>
      <dt>{field.label}</dt>
      <dd>{field.visibility === 'discard' ? '미수집' : displayValue(categoryNames[field.field_key] ?? field.value)}
        <small className="hint block">{verification[field.verification_status]} · {visibility[field.visibility]}{field.source && ` · ${field.source}`}</small>
      </dd>
    </div>)}
  </dl>
}

export function DynamicInput({ field, value, change, onBlur, error }: {
  field: SchemaField; value: FieldValue | undefined; change: (value: FieldValue) => void
  onBlur?: () => void; error?: string
}) {
  const id = `edit-${field.field_key}`
  if (!field.store || field.extraction_source === 'calculated') {
    return <div className="field"><strong>{field.label}</strong><span className="hint">
      {field.store ? `${displayValue(value)} · 권종별 수량 확인 후 서버에서 계산합니다.` : '미수집 · 원문은 저장하지 않습니다.'}
    </span></div>
  }
  if (field.data_type === 'denominations') {
    const rows = Array.isArray(value) ? value as Denomination[] : []
    return <fieldset className="field form-wide">
      <legend>{field.label}</legend>
      {rows.map((row, index) => <div key={index} className="flex flex-wrap items-end gap-2">
        <Field id={`${id}-value-${index}`} label={`권종 ${index + 1}`}>
          <input type="number" min="1" id={`${id}-value-${index}`} value={row.denomination || ''}
            onChange={event => change(rows.map((entry, position) => position === index ? { ...entry, denomination: Number(event.target.value) } : entry))} />
        </Field>
        <Field id={`${id}-count-${index}`} label={`장수·개수 ${index + 1}`}>
          <input type="number" min="1" id={`${id}-count-${index}`} value={row.count || ''}
            onChange={event => change(rows.map((entry, position) => position === index ? { ...entry, count: Number(event.target.value) } : entry))} />
        </Field>
        <button type="button" className="button secondary compact" onClick={() => change(rows.filter((_, position) => position !== index))}>행 삭제</button>
      </div>)}
      <button className="button secondary compact self-start" type="button" disabled={rows.length >= 40}
        onClick={() => change([...rows, { denomination: 0, count: 1 }])}>권종 추가</button>
      {error && <span role="alert" className="field-error">{error}</span>}
    </fieldset>
  }
  if (field.data_type === 'multiselect') {
    const chosen = Array.isArray(value) ? value as string[] : []
    return <fieldset className="field"><legend>{field.label}</legend>
      {field.options.map(option => <label key={option} className="checkbox-label">
        <input type="checkbox" checked={chosen.includes(option)}
          onChange={event => change(event.target.checked ? [...chosen, option] : chosen.filter(entry => entry !== option))} /> {option}
      </label>)}
    </fieldset>
  }
  const common = { id, name: field.field_key, required: field.required,
    'aria-invalid': error ? true as const : undefined, 'aria-describedby': error ? `${id}-error` : undefined }
  return <Field id={id} label={field.label} required={field.required} error={error}>
    {field.data_type === 'boolean' || field.data_type === 'select'
      ? <select {...common} value={value == null ? '' : String(value)} onChange={event => {
        const selected = event.target.value
        change(selected === '' ? null : field.data_type === 'boolean' ? selected === 'true' : selected)
      }}>
        <option value="">미확인</option>
        {field.data_type === 'boolean' ? <><option value="true">예</option><option value="false">아니요</option></>
          : field.options.map(option => <option key={option}>{option}</option>)}
      </select>
      : <input {...common} type={field.data_type === 'number' ? 'number' : 'text'} maxLength={120} min={0}
        value={value == null ? '' : String(value)} onBlur={onBlur} onChange={event => {
          const typed = event.target.value
          change(typed === '' ? null : field.data_type === 'number' ? Number(typed) : typed)
        }} />}
  </Field>
}
