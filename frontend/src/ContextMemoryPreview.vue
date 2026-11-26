<script setup lang="ts">
defineProps<{report:any}>()
const names:Record<string,string>={entry_synopsis:'单经历梗概',title_index:'仅标题',period_digest:'周期摘要',narrative:'完整精修',entry_narrative:'完整精修'}
const reasons:Record<string,string>={retained_budget_exhausted:'超出本轮记忆预算，整条省略',confirmed_synopsis_missing_title_only:'没有可用的已确认梗概，本轮只带标题',current_content_not_eligible:'当前内容或来源不具备使用资格',projection_budget_or_qualification:'时间投影、资格或预算不满足，未带入',memory_or_chronicle_disabled:'记忆或Chronicle通道未开启',current_content_or_capacity_unavailable:'当前内容资格或候选容量不满足，未带入'}
</script>
<template>
  <section v-if="report" class="memory-preview" aria-label="Chronicle 实际带入与预算">
    <h3>Chronicle 实际带入与预算</h3>
    <p v-if="report.selectionDetails?.length" role="status">当前带入 {{report.selectionDetails.filter((item:any)=>item.included).length}} 项，共核对 {{report.selectionDetails.length}} 项。</p>
    <details v-if="report.budget"><summary>查看上下文用量估算</summary><p>{{report.budget.usedTokens}} / {{report.budget.limitTokens}} 估算 token · {{report.budget.usedBlocks}} / {{report.budget.limitBlocks}} 块。不是供应商计费值；共享周期块只计一次。</p></details>
    <p v-if="report.reason">{{reasons[report.reason] || '当前通道未带入记忆。'}}</p>
    <p v-if="report.enabled && !report.selectionDetails?.length">本轮没有合资格的候选或常驻条目。</p>
    <article v-for="item in report.selectionDetails || []" :key="item.entryId">
      <strong>{{item.title || '当前不可用的条目'}}</strong>
      <span>{{item.included ? '已带入' : '未带入'}} · {{item.retained ? '常驻／固定参考' : '普通召回'}}</span>
      <p v-if="item.included">{{names[item.projection] || item.projection}} · {{item.retained ? '跨时间档保留' : item.level || '当前视图'}} · {{item.estimatedTokens}} 估算 token{{item.sharedBlock ? '（共享块）' : ''}}</p>
      <p v-if="item.reason">{{reasons[item.reason] || '当前条件不满足，未沿用旧正文。'}}</p>
    </article>
    <small>使用已保存配置和当前来源，只读预览。设置或内容变化后请重新预览；不会发起付费补查。</small>
  </section>
</template>
<style scoped>
.memory-preview{padding:16px;border:1px solid var(--ui-line);border-radius:12px;background:var(--ui-surface);color:var(--ui-ink)}
.memory-preview article{padding:12px 0;border-top:1px solid var(--ui-line)}
.memory-preview span,.memory-preview small{display:block;color:var(--ui-muted)}
.memory-preview p{margin:6px 0;font-size:var(--ui-reading-size);line-height:var(--ui-reading-leading)}.memory-preview summary{cursor:pointer;color:var(--ui-accent);padding:6px 0}
</style>
