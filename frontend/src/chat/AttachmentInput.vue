<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref } from 'vue'
const props=defineProps<{characterId:number;disabled:boolean;modelValue:any[]}>()
const emit=defineEmits<{ 'update:modelValue':[any[]];error:[string] }>()
const input=ref<HTMLInputElement>();const busy=ref(false);const capture=ref<HTMLCanvasElement|null>(null)
const preview=ref('');const x=ref(0),y=ref(0),width=ref(0),height=ref(0)
const notice=ref('')
let active:AbortController|null=null
let disposed=false
onBeforeUnmount(()=>{disposed=true;active?.abort()})
function cancelUpload(){active?.abort()}
async function upload(files:FileList|File[]|null){
  if(!files||props.disabled||busy.value||disposed)return
  busy.value=true
  const character=props.characterId
  const controller=new AbortController();active=controller
  const chosen=Array.from(files)
  let skipped=0
  try{
    for(const [index,file] of chosen.entries()){
      if(controller.signal.aborted||props.disabled)return
      if(!file.size||file.size>8*1024*1024)throw new Error('请选择非空且不超过 8 MiB 的文件')
      const bytes=await file.arrayBuffer()
      if(controller.signal.aborted)return
      const digest=crypto.subtle ? Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),v=>v.toString(16).padStart(2,'0')).join('') : null
      if(digest && props.modelValue.some(item=>item.localDigest===digest && item.name===file.name)){skipped++;continue}
      if(props.modelValue.length>=8)throw new Error('草稿已满 8 个附件；可移除后继续添加')
      if(controller.signal.aborted || props.disabled)return
      notice.value=`正在添加 ${index+1} / ${chosen.length}`
      const form=new FormData();form.append('character_id',String(character));form.append('file',file)
      const response=await fetch('/api/chat/attachments',{method:'POST',body:form,signal:controller.signal})
      const data=await response.json()
      if(!response.ok)throw new Error(data.detail||'添加失败')
      if(!controller.signal.aborted && character===props.characterId && !props.disabled)
        {emit('update:modelValue',[...props.modelValue,{...data,localDigest:digest}]);await nextTick()}
    }
    notice.value=skipped?`已跳过 ${skipped} 个同名同内容的重复附件。`:''
    return true
  }catch(e){
    if(controller.signal.aborted){if(!disposed)notice.value='已停止添加。已列入草稿的附件保留。'}
    else emit('error',e instanceof Error?e.message:'附件添加失败，请检查草稿中已保留的文件。')
  }
  finally{active=null;busy.value=false;if(input.value)input.value.value='';if(notice.value.startsWith('正在添加'))notice.value=''}
}
function paste(event:ClipboardEvent){const files=event.clipboardData?.files;if(files?.length&&!event.defaultPrevented){event.preventDefault();void upload(files)}}
async function screenshot(){
  let stream:MediaStream|null=null
  try{stream=await navigator.mediaDevices.getDisplayMedia({video:true,audio:false});if(disposed||props.disabled)return;const video=document.createElement('video');video.srcObject=stream;await video.play();if(disposed||props.disabled)return;
    const canvas=document.createElement('canvas');canvas.width=video.videoWidth;canvas.height=video.videoHeight
    canvas.getContext('2d')!.drawImage(video,0,0);capture.value=canvas;preview.value=canvas.toDataURL('image/png');x.value=0;y.value=0;width.value=canvas.width;height.value=canvas.height
  }catch(e){if((e as Error).name!=='NotAllowedError')emit('error','截屏失败，请选择文件或粘贴图片')}finally{stream?.getTracks().forEach(t=>t.stop())}
}
async function confirmCapture(){const source=capture.value;if(!source)return
  if(x.value<0||y.value<0||width.value<1||height.value<1||x.value+width.value>source.width||y.value+height.value>source.height){emit('error','裁剪范围超出画面');return}
  const canvas=document.createElement('canvas');canvas.width=width.value;canvas.height=height.value
  canvas.getContext('2d')!.drawImage(source,x.value,y.value,width.value,height.value,0,0,width.value,height.value)
  const blob=await new Promise<Blob|null>(resolve=>canvas.toBlob(resolve,'image/png'))
  if(blob && await upload([new File([blob],'截图.png',{type:'image/png'})])){preview.value='';capture.value=null}
}
defineExpose({paste,upload,busy})
</script>
<template>
  <div class="chat-attachment-input" @dragover.prevent @drop.prevent="upload($event.dataTransfer?.files??null)" @paste="paste">
    <button type="button" :disabled="disabled||busy" @click="input?.click()">{{busy?'上传中…':'图片／文件'}}</button>
    <button type="button" :disabled="disabled||busy" @click="screenshot">截图</button>
    <input ref="input" type="file" multiple hidden accept="image/png,image/jpeg,image/webp,.txt,.md,.pdf,.docx,.py,.js,.ts,.vue,.json,.csv,.css,.html,.yaml,.yml,.sql,.rs,.go,.java,.cpp,.c,.h,.ps1,.sh" @change="upload(($event.target as HTMLInputElement).files)"/>
    <button v-if="busy" type="button" @click="cancelUpload">停止添加</button><span v-if="notice" role="status">{{notice}}</span><span v-if="modelValue.length">{{modelValue.length}} 个附件，发送时才交给模型</span>
    <div v-for="(file,i) in modelValue" :key="file.id" class="chat-attachment-card">
      <img v-if="file.mime.startsWith('image/')" :src="`/api/chat/attachments/${file.id}?character_id=${characterId}`" :alt="file.name"/>
      <span>{{file.name}}</span><button :disabled="disabled" @click="emit('update:modelValue',modelValue.filter((_,n)=>n!==i))">移除</button>
      <small v-for="warning in file.diagnostics?.warnings" :key="warning">{{warning}}</small>
    </div>
    <section v-if="preview" class="chat-capture-preview"><h3>截图预览与裁剪</h3><img :src="preview" alt="待发送截图，尚未上传"/>
      <div class="chat-crop-fields"><label>左<input v-model.number="x" type="number" min="0"/></label><label>上<input v-model.number="y" type="number" min="0"/></label><label>宽<input v-model.number="width" type="number" min="1"/></label><label>高<input v-model.number="height" type="number" min="1"/></label></div>
      <button :disabled="busy" @click="confirmCapture">添加选定画面</button><button @click="preview='';capture=null">取消截图</button>
    </section>
  </div>
</template>
