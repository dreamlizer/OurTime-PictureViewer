/* Recoverable library edits. The server checks conflicts again at commit time. */
async function editPhotoMetadata(payload,operationId=operationRuntime.operationId()){
 try{
  const result=await operationRequest('/api/organize/photos',{method:'PATCH',body:JSON.stringify({...payload,operation_id:operationId})},operationId);
  return {...result,undoAvailable:true};
 }catch(error){
  if(![404,405].includes(error.status))throw error;
  // Existing service can serve newer static files before its next safe restart.
  // Only a missing route falls back; a real missing-photo error stays an error.
  if(!['Not Found','Method Not Allowed'].includes(error.message))throw error;
  return {...await api('/api/photos',{method:'PATCH',body:JSON.stringify(payload)}),undoAvailable:false};
 }
}
function photoEditMessage(result){return result.undoAvailable?'补录已保存，可在最近操作中撤销':'补录已保存；当前后台尚未加载撤销功能';}
async function editPhotoNearby(id,payload){
 const operationId=operationRuntime.operationId();
 try{return await operationRequest(`/api/organize/photos/${id}/nearby-place`,{method:'POST',body:JSON.stringify({...payload,operation_id:operationId})},operationId);}
 catch(error){
  if(![404,405].includes(error.status)||!['Not Found','Method Not Allowed'].includes(error.message))throw error;
  return api(`/api/photos/${id}/nearby-place`,{method:'POST',body:JSON.stringify(payload)});
 }
}
(function(){
 const button=document.createElement('button');
 button.type='button';button.className='nav';button.dataset.recentOperations='';button.textContent='最近操作';
 document.querySelector('#organize-nav .details-body').append(button);
 for(const parent of ['#detail-dialog .detail-info','#person-dialog .person-chrome']){
  const shortcut=document.createElement('button');shortcut.type='button';shortcut.className='text-button';
  shortcut.dataset.recentOperations='';shortcut.textContent='最近操作与撤销';$(parent).append(shortcut);
 }
 const dialog=document.createElement('dialog');dialog.id='operations-dialog';dialog.className='compact-dialog';
 dialog.setAttribute('aria-labelledby','operations-title');
 dialog.innerHTML='<button type="button" class="dialog-close" aria-label="关闭最近操作">×</button><h2 id="operations-title">最近操作</h2><p class="muted">照片补录、照片附近地点修改和人物合并可在这里撤销。每次撤销前会检查后续修改。</p><p class="operations-message" role="status" aria-live="polite"></p><div class="operations-list"></div><button type="button" class="text-button operations-reload">刷新记录</button>';
 document.body.append(dialog);
 const list=dialog.querySelector('.operations-list'),message=dialog.querySelector('.operations-message');
 let busy=false,ticket=0;
 async function reload(){
  const token=++ticket;message.textContent='正在读取操作记录…';list.replaceChildren();
  try{
   const result=await api('/api/organize/operations?limit=30');if(token!==ticket)return;
   message.textContent=result.items.length?'最近 30 条可恢复操作；已撤销的记录仍保留。':'还没有可撤销的操作。旧的补录记录不会被误当作可恢复记录。';
   for(const item of result.items){
    const row=document.createElement('article');row.className='operation-row';
    const content=document.createElement('div'),title=document.createElement('b'),detail=document.createElement('p'),time=document.createElement('small');
    title.textContent=item.title;detail.textContent=item.detail;time.textContent=item.created_at.replace('T',' ');
    content.append(title,detail,time);const undo=document.createElement('button');undo.type='button';undo.className='secondary small';
    undo.textContent=item.undone?'已撤销':'撤销';undo.disabled=item.undone;undo.dataset.undoOperation=item.operation_id;
    row.append(content,undo);list.append(row);
   }
  }catch(error){if(token===ticket)message.textContent=error.status===404?'当前后台尚未加载最近操作功能。更新后台后可使用，未执行任何修改。':error.message;}
 }
 document.addEventListener('click',e=>{if(e.target.closest('[data-recent-operations]')){showDialog('#operations-dialog');void reload();}});
 dialog.querySelector('.dialog-close').onclick=()=>{if(!busy)dialog.close();};
 dialog.addEventListener('cancel',e=>{if(busy)e.preventDefault();});
 dialog.querySelector('.operations-reload').onclick=()=>{if(!busy)void reload();};
 list.addEventListener('click',async e=>{
  const target=e.target.closest('[data-undo-operation]');if(!target||busy||target.disabled)return;
  busy=true;target.disabled=true;message.textContent='正在检查是否可以撤销…';
  let committed=false;
  try{
   const url='/api/organize/operations/'+encodeURIComponent(target.dataset.undoOperation)+'/undo';
   const preview=await api(url,{method:'POST',body:JSON.stringify({dry_run:true})});
   if(preview.undone){committed=true;await reload();return;}
   if(!preview.can_undo){message.textContent='未撤销：'+preview.conflicts.join('；');return;}
   message.textContent='正在撤销…';
   try{await api(url,{method:'POST',body:JSON.stringify({dry_run:false})});committed=true;}
   catch(error){
    if(error.status&&error.status<500)throw error;
    const receipt=await api('/api/operations/'+encodeURIComponent(target.dataset.undoOperation));
    if(receipt.status!=='undone')throw error;committed=true;
   }
   // Mark success before refreshing; a failed view update must not invite a second write.
   await reload();message.textContent='已撤销这次操作。';
   try{
    if($('#person-dialog').open)$('#person-dialog').close();
    if($('#detail-dialog').open){
     const id=Number(state.detail.id);viewer.drafts.delete(id);await openPhoto(id,viewer.context);
    }
    if(state.photoMap){state.photoMap.nearby=null;state.photoMap.photo=await api('/api/photos/'+state.photoMap.id);}
    if(!$('#detail-dialog').open)await setView(state.view);
    await refreshStatus();
   }catch(error){message.textContent='已撤销；页面刷新未完成，请刷新页面查看。';}
  }catch(error){message.textContent='未能确认撤销结果：'+error.message;}
  finally{busy=false;if(!committed&&target.isConnected)target.disabled=false;}
 });
})();
