<script setup lang="ts">
import { ref } from 'vue'
const props=defineProps<{characterId:number;disabled:boolean;modelValue:any[]}>()
const emit=defineEmits<{ 'update:modelValue':[any[]];error:[string] }>()
const input=ref<HTMLInputElement>();const busy=ref(false);const capture=ref<HTMLCanvasElement|null>(null)
const preview=ref('');const x=ref(0),y=ref(0),width=ref(0),height=ref(0)
async function upload(files:FileList|File[]|null){
  if(!files||props.disabled||busy.value)return
  busy.value=true
  const character=props.characterId
  try{for(const file of Array.from(files)){
    if(props.modelValue.length>=8)throw new Error('每条最多 8 个附件')
    const form=new FormData();form.append('character_id',String(character));form.append('file',file)
    const response=await fetch('/api/chat/attachments',{method:'POST',body:form});const data=await response.json()
    if(!response.ok)throw new Error(data.detail||'上传失败')
    if(character===props.characterId)emit('update:modelValue',[...props.modelValue,data])
  }}catch(e){emit('error',e instanceof Error?e.message:'附件失败')}finally{busy.value=false;if(input.value)input.value.value=''}
}
function paste(event:ClipboardEvent){const files=event.clipboardData?.files;if(files?.length){event.preventDefault();void upload(files)}}
async function screenshot(){
  let stream:MediaStream|null=null
  try{stream=await navigator.mediaDevices.getDisplayMedia({video:true,audio:false});const video=document.createElement('video');video.srcObject=stream;await video.play();
    const canvas=document.createElement('canvas');canvas.width=video.videoWidth;canvas.height=video.videoHeight
    canvas.getContext('2d')!.drawImage(video,0,0);capture.value=canvas;preview.value=canvas.toDataURL('image/png');x.value=0;y.value=0;width.value=canvas.width;height.value=canvas.height
  }catch(e){if((e as Error).name!=='NotAllowedError')emit('error','截屏失败，请选择文件或粘贴图片')}finally{stream?.getTracks().forEach(t=>t.stop())}
}
async function confirmCapture(){const source=capture.value;if(!source)return
  if(x.value<0||y.value<0||width.value<1||height.value<1||x.value+width.value>source.width||y.value+height.value>source.height){emit('error','裁剪范围超出画面');return}
  const canvas=document.createElement('canvas');canvas.width=width.value;canvas.height=height.value
  canvas.getContext('2d')!.drawImage(source,x.value,y.value,width.value,height.value,0,0,width.value,height.value)
  const blob=await new Promise<Blob|null>(resolve=>canvas.toBlob(resolve,'image/png'))
  if(blob){await upload([new File([blob],'截图.png',{type:'image/png'})]);preview.value='';capture.value=null}
}
defineExpose({paste,upload})
</script>
<template>
  <div class="chat-attachment-input" @dragover.prevent @drop.prevent="upload($event.dataTransfer?.files??null)" @paste="paste">
    <button type="button" :disabled="disabled||busy" @click="input?.click()">{{busy?'上传中…':'图片／文件'}}</button>
    <button type="button" :disabled="disabled||busy" @click="screenshot">截图</button>
    <input ref="input" type="file" multiple hidden accept="image/png,image/jpeg,image/webp,.txt,.md,.pdf,.docx,.py,.js,.ts,.vue,.json,.csv,.css,.html,.yaml,.yml,.sql,.rs,.go,.java,.cpp,.c,.h,.ps1,.sh" @change="upload(($event.target as HTMLInputElement).files)"/>
    <span v-if="modelValue.length">{{modelValue.length}} 个附件，发送时才交给模型</span>
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
