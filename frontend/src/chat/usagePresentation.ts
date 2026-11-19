export type UsageCounter = {known:number;provided:number;unknown:number}
export function usageLabel(value:UsageCounter|undefined):string {
  if(!value || value.provided===0) return value?.unknown ? `未知（${value.unknown} 次均未提供）` : '未记录（尚无回执）'
  return value.unknown ? `已知部分 ${value.known} token（${value.provided} 次提供，${value.unknown} 次未知）` : `${value.known} token（${value.provided} 次回执完整）`
}
export function totalLabel(input:UsageCounter,output:UsageCounter):string {
  if(input.provided+output.provided===0) return input.unknown+output.unknown>0 ? '未知（输入和输出回执均缺失）' : '未记录（尚无回执）'
  const amount=input.known+output.known
  return input.unknown+output.unknown>0 ? `已知部分 ${amount} token（完整总量未知）` : `${amount} token（回执完整）`
}
export function subtotalLabels(value:any){return {attempts:value.attempts,local_dispatches:value.dispatched,input:usageLabel({known:value.input_known,provided:value.dispatched-value.input_unknown,unknown:value.input_unknown}),output:usageLabel({known:value.output_known,provided:value.dispatched-value.output_unknown,unknown:value.output_unknown})}}
