import assert from 'node:assert/strict'
import {usageLabel,totalLabel,subtotalLabels} from '../frontend/src/chat/usagePresentation.ts'
const zero={known:0,provided:1,unknown:0},unknown={known:0,provided:0,unknown:2},partial={known:17,provided:1,unknown:1}
assert.equal(usageLabel(zero),'0 token（1 次回执完整）')
assert.equal(usageLabel(unknown),'未知（2 次均未提供）')
assert.equal(usageLabel(partial),'已知部分 17 token（1 次提供，1 次未知）')
assert.equal(totalLabel(zero,zero),'0 token（回执完整）')
assert.equal(totalLabel(unknown,unknown),'未知（输入和输出回执均缺失）')
assert.equal(totalLabel(partial,zero),'已知部分 17 token（完整总量未知）')
assert.equal(usageLabel({known:0,provided:1,unknown:1}),'已知部分 0 token（1 次提供，1 次未知）')
assert.equal(usageLabel({known:0,provided:0,unknown:0}),'未记录（尚无回执）')
assert.equal(subtotalLabels({attempts:1,dispatched:1,input_known:0,output_known:0,input_unknown:1,output_unknown:1}).input,'未知（1 次均未提供）')
assert.equal(subtotalLabels({attempts:2,dispatched:2,input_known:17,output_known:4,input_unknown:1,output_unknown:1}).output,'已知部分 4 token（1 次提供，1 次未知）')
console.log('10 assertions passed: zero / all unknown / partial / no receipts / subtotals')
