// One pending setting per tab. Never store credentials, prompts or model defaults.
export const NEXT_SETTINGS_KEY='YOSAKI.next-request-settings.v1'
export type Overrides={reasoning_effort?:'low'|'medium'|'high'|null;max_tokens?:number}
export type SettingsScope={user:string;character:number;conversation:number|null;model:number;revision:string}
export type PendingSettings={version:1;scope:SettingsScope;value:Overrides;phase:'ready'|'sending';id:string}
type StoragePort=Pick<Storage,'getItem'|'setItem'|'removeItem'>
export function modelRevision(p:any){return JSON.stringify([p.id,p.updated_at,p.protocol,p.model_name,p.reasoning_effort,p.max_tokens,p.temperature,p.top_p,p.context_limit,p.supports_streaming,p.enabled])}
export function sameScope(a:SettingsScope|null,b:SettingsScope|null){return a!==null&&b!==null&&JSON.stringify(a)===JSON.stringify(b)}
export function validOverrides(value:any):value is Overrides{
  if(!value||typeof value!=='object'||Array.isArray(value)||!Object.keys(value).length)return false
  return Object.entries(value).every(([key,v])=>key==='reasoning_effort'?[null,'low','medium','high'].includes(v as any):key==='max_tokens'&&Number.isInteger(v)&&Number(v)>=1&&Number(v)<=128000)
}
export function pendingSettings(storage:StoragePort){
  function read():PendingSettings|null{
    const raw=storage.getItem(NEXT_SETTINGS_KEY)
    if(!raw)return null
    const p=JSON.parse(raw),s=p?.scope
    if(p.version!==1||!['ready','sending'].includes(p.phase)||typeof p.id!=='string'||!s||typeof s.user!=='string'||!s.user||!Number.isInteger(s.character)||s.character<1||!(s.conversation===null||Number.isInteger(s.conversation)&&s.conversation>0)||!Number.isInteger(s.model)||s.model<1||typeof s.revision!=='string'||!validOverrides(p.value))throw Error('已暂存的单次设置格式无效，未恢复')
    return p
  }
  function write(p:PendingSettings){storage.setItem(NEXT_SETTINGS_KEY,JSON.stringify(p));return p}
  function clear(){storage.removeItem(NEXT_SETTINGS_KEY)}
  function apply(scope:SettingsScope,value:Overrides){
    if(!validOverrides(value))throw Error('单次设置参数无效，未应用')
    return write({version:1,scope,value:{...value},phase:'ready',id:crypto.randomUUID()})
  }
  function reserve(scope:SettingsScope):PendingSettings|null{
    const p=read()
    if(!p)return null
    if(p.phase!=='ready'||!sameScope(p.scope,scope))throw Error('单次设置身份已变化或正在提交，请重新核对')
    return write({...p,phase:'sending'}) // Persist BEFORE fetch so refresh cannot replay an accepted request.
  }
  function reject(ticket:PendingSettings,scope:SettingsScope|null){
    const p=read()
    if(p?.id===ticket.id&&p.phase==='sending'&&sameScope(scope,ticket.scope))return write({...ticket,phase:'ready'})
    return null
  }
  function consume(ticket:PendingSettings){if(read()?.id===ticket.id)clear()}
  return {read,clear,apply,reserve,reject,consume}
}
