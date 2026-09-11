// Browsing order is an immutable list of IDs from the current collection.
const viewer={ids:[],index:0,target:0,offset:0,total:0,context:null,generation:0,busy:false,timer:null,playing:false,scale:1,fit:true,loader:null,drafts:new Map(),window:200,faceNames:true,faceAlias:false,faceVertical:true};
function currentBrowseContext(){return {q:state.q,filter:state.view,person:state.person,directory:state.directory,sort:state.sort,max_id:state.maxId};}
function contextLabel(c){const person=state.people.find(p=>String(p.id)===c.person);const filter=c.filter||'';const heading=c.person?(person?.name||'人物 '+c.person):filter.startsWith('year:')?(filter.slice(5)==='unknown'?'时间未记录':filter.slice(5)+' 年'):filter.startsWith('group:')?(filter.slice(6)==='10plus'?'10人及以上':Number(filter.slice(6))+'人合影'):filter.startsWith('place:')?(filter.slice(6)==='unknown'?'地点未记录':filter.slice(6)):titles[filter]?.[0]||'照片';return [heading,c.directory?basename(c.directory):'',c.q?'搜索：'+c.q:''].filter(Boolean).join(' · ');}
function viewerMessage(text){$('#viewer-message').textContent=text;}
function stopSlides(){viewer.playing=false;clearTimeout(viewer.timer);viewer.timer=null;syncViewerTools();}
function pressTool(id, on){const el=$(id); if(!el)return; el.setAttribute('aria-pressed', String(!!on));}
function syncViewerTools(){
 pressTool('#toggle-face-names', viewer.faceNames!==false);
 pressTool('#toggle-face-alias', viewer.faceAlias===true);
 pressTool('#toggle-face-dir', viewer.faceVertical!==false);
 pressTool('#viewer-info', !$('#detail-dialog').classList.contains('hide-info'));
 pressTool('#viewer-play', viewer.playing);
 const aliasBtn=$('#toggle-face-alias'); if(aliasBtn) aliasBtn.disabled=viewer.faceNames===false;
 const dirBtn=$('#toggle-face-dir'); if(dirBtn) dirBtn.disabled=viewer.faceNames===false;
}
function scheduleSlide(){clearTimeout(viewer.timer);viewer.timer=setTimeout(async()=>{try {await movePhoto(1,true);}catch(e){viewerMessage(e.message);stopSlides();}},Number($('#slide-delay').value)*1000);}
function updatePosition(){
 const absolute=Number.isFinite(viewer.absolute)?viewer.absolute:(viewer.offset||0)+(viewer.index||0);
 $('#viewer-position').textContent=`${fmt(absolute+1)} / ${fmt(viewer.total||viewer.ids.length)}`;
 $('#viewer-prev').disabled=absolute<=0;$('#viewer-next').disabled=absolute>=(viewer.total||viewer.ids.length)-1;
 $('#viewer-context').textContent=contextLabel(viewer.context||{});
}
function updateZoom(reset=false){
 const img=$('#detail-img'),area=$('#image-viewport'),mat=$('#photo-mat');if(!img.naturalWidth)return;
 const padding=0;
 if(viewer.fit){
  const width=Math.max(40, area.clientWidth);
  const height=Math.max(40, area.clientHeight);
  viewer.scale=Math.max(.01, Math.min(width/img.naturalWidth, height/img.naturalHeight));
 }
 mat.classList.remove('compact-signature');
 const w=Math.max(1,Math.round(img.naturalWidth*viewer.scale));
 const h=Math.max(1,Math.round(img.naturalHeight*viewer.scale));
 area.classList.toggle('zoomed', !viewer.fit);
 mat.style.width=w+'px';
 img.style.width=w+'px';
 img.style.height=h+'px';
 $('#zoom-level').textContent=Math.round(viewer.scale*100)+'%';
 if(reset){area.scrollTop=0;area.scrollLeft=0;}
 if(state.detail && viewer.faceNames!==false) requestAnimationFrame(()=>renderFaceNames(state.detail));
}
function renderSignature(a,file){
 const tags=a.metadata?.ExifTool||{};
 const tag=name=>Object.entries(tags).find(([key])=>key.split(':').at(-1)===name)?.[1];
 const clean=value=>String(value||'').replace(/\0/g,'').trim();
 const make=clean(tag('Make')),model=clean(tag('Model'));
 $('#signature-camera').textContent=model?(model.toLowerCase().startsWith(make.toLowerCase())?model:[make,model].filter(Boolean).join(' ')):clean(a.camera)||'拍摄设备未记录';
 const source=a.effective_source||'';
 const timeKind=source.includes('修改')?'修改时间参考':source.includes('推测')?'推测时间':source==='人工确认'?'补录时间':'拍摄时间';
 const size=file?.size;
 const fileSize=Number.isFinite(size)?(size>=1024*1024?(size/1024/1024).toFixed(2)+' MB':(size/1024).toFixed(1)+' KB'):'大小未记录';
 $('#signature-format').textContent=[a.format||'图片',fileSize].join('  ·  ');
 $('#signature-format').title=Number.isFinite(size)?`原文件大小：${size.toLocaleString('zh-CN')} 字节`:'原文件大小未记录';
 const number=name=>{const n=Number(tag(name));return Number.isFinite(n)&&n>0?n:null;};
 const focal=number('FocalLengthIn35mmFormat')||number('FocalLength'),aperture=number('FNumber'),shutter=number('ExposureTime'),iso=number('ISO');
 const parts=[];
 if(focal)parts.push([`${Number(focal.toFixed(1))} 毫米`,number('FocalLengthIn35mmFormat')?'等效焦距':'焦距']);
 if(aperture)parts.push([`ƒ/${Number(aperture.toFixed(1))}`,'光圈']);
 if(shutter)parts.push([shutter<1?`1/${Math.round(1/shutter)} 秒`:`${Number(shutter.toFixed(2))} 秒`,'快门']);
 if(iso)parts.push([String(iso),'感光度']);
 $('#signature-settings').innerHTML=parts.length?parts.map(([value,label])=>`<div><b>${esc(value)}</b><span>${label}</span></div>`).join(''):'<span class="no-exposure">曝光参数未记录</span>';
 const date=a.effective_date||'时间待确认';
 const shownDate=/^\d{4}-\d\d-\d\dT/.test(date)?date.slice(0,16).replaceAll('-','.').replace('T','  '):date;
 $('#signature-date').innerHTML=`<span class="signature-time-label">${a.effective_date?timeKind:'时间'}</span><span class="signature-time-value">${esc(shownDate)}</span>`;
 $('#signature-date').title=timeKind+'：'+date;
 $('#signature-place').textContent=prettyPlace(a.effective_place)||'地点待补充';
 $('#signature-place').title=a.manual_place?'人工补录地点':a.place_source||'未记录地点';
}
function namedFaces(photo){return (photo&&photo.faces||[]).filter(f=>f.name&&!f.ignored);}
function visibleFaces(photo){return (photo&&photo.faces||[]).filter(f=>!f.ignored);}
function viewerImageSrc(photo, file, id){
 const w=Number(photo&&photo.width)||0, h=Number(photo&&photo.height)||0;
 const format=String(photo&&photo.format||'').toUpperCase();
 const path=String(file&&file.path||'');
 const ext=(path.split('.').pop()||'').toLowerCase();
 const heavyExt=['heic','heif','dng','arw','cr2','nef','raf','rw2','orf','tif','tiff'];
 const browserOk=['jpg','jpeg','png','gif','webp','bmp'];
 const tooBig=(w&&h&&(w*h>12000000 || Math.max(w,h)>4000)) || Number(file&&file.size)>12*1024*1024;
 if(!photo||!photo.in_library) return '/api/thumb/'+id+'?v='+state.thumbRevision;
 if(tooBig || heavyExt.includes(ext) || ['HEIF','TIFF','MPO'].includes(format) || (ext && !browserOk.includes(ext))){
  return '/api/preview/'+id+'?v='+state.thumbRevision;
 }
 return '/api/original/'+id;
}

function faceBox(face){
 let box=face.bbox; if(typeof box==='string'){try{box=JSON.parse(box);}catch(e){box=null;}}
 if(!Array.isArray(box)||box.length<4)return null;
 const [x1,y1,x2,y2,w,h]=box.map(Number);
 if(![x1,y1,x2,y2].every(Number.isFinite))return null;
 return {x1,y1,x2,y2,w:w||0,h:h||0,cx:(x1+x2)/2,cy:(y1+y2)/2,width:Math.max(1,x2-x1),height:Math.max(1,y2-y1)};
}
function faceLabelText(face, alias){
  if(face.name) return alias&&face.alias?(face.name+' / '+face.alias):face.name;
  return '命名';
}
function rectsOverlap(a,b,gap=6){
  return a.x<b.x+b.w+gap && a.x+a.w+gap>b.x && a.y<b.y+b.h+gap && a.y+a.h+gap>b.y;
}
function clampFaceLabel(rect, layerW, layerH, pad=6){
  rect.x=Math.min(Math.max(pad, rect.x), Math.max(pad, layerW-rect.w-pad));
  rect.y=Math.min(Math.max(pad, rect.y), Math.max(pad, layerH-rect.h-pad));
  return rect;
}
function layoutFaceNameButtons(layer, faces, alias){
  const img=$('#detail-img');
  if(!img||!img.naturalWidth||!layer.clientWidth)return;
  const layerW=layer.clientWidth, layerH=layer.clientHeight;
  const imgRect=img.getBoundingClientRect();
  const layerRect=layer.getBoundingClientRect();
  const ox=imgRect.left-layerRect.left;
  const oy=imgRect.top-layerRect.top;
  const imgW=imgRect.width, imgH=imgRect.height;
  const items=[];
  faces.forEach(face=>{
    const box=faceBox(face); if(!box||!box.w||!box.h)return;
    const fx=ox+box.x1/box.w*imgW;
    const fy=oy+box.y1/box.h*imgH;
    const fw=box.width/box.w*imgW;
    const fh=box.height/box.h*imgH;
    items.push({face, named:Boolean(face.name), label:faceLabelText(face,alias), fx, fy, fw, fh, cx:fx+fw/2, cy:fy+fh/2});
  });
  items.sort((a,b)=>a.cx-b.cx||a.cy-b.cy);
  layer.innerHTML=items.map(it=>'<button type="button" class="face-name'+(it.named?'':' unnamed')+'" data-face-person="'+it.face.person_id+'">'+esc(it.label)+'</button>').join('');
  const buttons=[...layer.querySelectorAll('.face-name')];
  const vertical=viewer.faceVertical!==false;
  const placed=[];
  const gap=4;
  const avgCx=items.reduce((s,it)=>s+it.cx,0)/Math.max(1,items.length);
  const side = avgCx < (ox + imgW/2) ? 'right' : 'left';
  buttons.forEach((btn,i)=>{
    const it=items[i];
    const w=Math.max(18, btn.offsetWidth);
    const h=Math.max(18, btn.offsetHeight);
    let x,y;
    if(vertical){
      const tuck=Math.min(8, Math.round(w*0.28));
      x = side==='left' ? it.fx - w + tuck : it.fx + it.fw - tuck;
      y = it.fy + it.fh*0.16;
    }else{
      x = it.cx - w/2;
      y = it.fy + it.fh*0.10;
    }
    const rect=clampFaceLabel({x,y,w,h,side,btn}, layerW, layerH);
    placed.push(rect);
    btn.classList.add(side);
  });
  placed.sort((a,b)=> (a.x-b.x) || (a.y-b.y));
  for(let i=1;i<placed.length;i++){
    const prev=placed[i-1], cur=placed[i];
    if(!rectsOverlap(prev,cur,gap)) continue;
    cur.y = Math.max(cur.y, prev.y + prev.h + gap);
    clampFaceLabel(cur, layerW, layerH);
  }
  placed.forEach(rect=>{
    rect.btn.style.left=Math.round(rect.x)+'px';
    rect.btn.style.top=Math.round(rect.y)+'px';
  });
}
function renderFaceNames(photo){
 const layer=$('#face-name-layer'); if(!layer)return;
 const show=viewer.faceNames!==false;
 const alias=viewer.faceAlias===true;
 const vertical=viewer.faceVertical!==false;
 layer.hidden=!show;
 layer.classList.toggle('horizontal',!vertical);
  if(!show){layer.innerHTML='';syncViewerTools();return;}
  const faces=visibleFaces(photo);
  if(!$('#detail-img')?.naturalWidth || !layer.clientWidth){
    layer.innerHTML='';
    requestAnimationFrame(()=>{ if(state.detail===photo) layoutFaceNameButtons(layer, faces, alias); });
  }else layoutFaceNameButtons(layer, faces, alias);
  syncViewerTools();
}
function zoomTo(scale){viewer.fit=false;viewer.scale=Math.max(.05,Math.min(4,scale));updateZoom();}
async function displayPhoto(id){
 if(viewer.loader){viewer.loader.onload=null;viewer.loader.onerror=null;viewer.loader.src='';}
 if(!await renderPhoto(id))return false;
 const root=(viewer.context?.directory||'').replace(/[\/]+$/,'').toLowerCase();
 const files=[...state.detail.files].sort((a,b)=>a.excluded-b.excluded||b.exists_now-a.exists_now||a.id-b.id);
 const scoped=files.find(f=>!root||f.path.toLowerCase().startsWith(root+String.fromCharCode(92))||f.path.toLowerCase()===root);
 renderSignature(state.detail,scoped||files[0]);
 if(scoped)$('#detail-name').textContent=basename(scoped.path);
 $('#detail-dialog').dataset.photoId=String(id);
 const draft=viewer.drafts.get(id);if(draft)for(const [key,value] of Object.entries(draft))$(key).value=value;
 const file=scoped||files[0];
 const src=viewerImageSrc(state.detail, file, id);
 const current=($('#detail-img').getAttribute('src')||'');
 const applySrc=(url)=>{
  if(!$('#detail-dialog').open||state.detail?.id!==id)return;
  viewer.fit=true;
  $('#detail-img').src=url;
  updateZoom(true);
  renderFaceNames(state.detail);
  viewerMessage(draft?'这张照片有尚未保存的补录，已暂存在本页。':!state.detail.in_library?'已排除 · 缓存已清理':'');
 };
 updatePosition();
 if(current===src || current.endsWith(src)){applySrc(current);return true;}
 const image=new Image();viewer.loader=image;
 await new Promise(resolve=>{
  image.onload=()=>{applySrc(image.src);resolve();};
  image.onerror=()=>{
   if(!src.includes('/api/preview/')){
    const fallback=new Image(); viewer.loader=fallback;
    fallback.onload=()=>{applySrc(fallback.src);resolve();};
    fallback.onerror=()=>{viewerMessage('这张图暂时无法显示');resolve();};
    fallback.src='/api/preview/'+id+'?v='+state.thumbRevision;
    return;
   }
   viewerMessage('这张图暂时无法显示');
   resolve();
  };
  image.src=src;
 });
 return true;
}
async function openPhoto(id,context=null){
 if(!$('#detail-dialog').open)viewer.returnScroll=scrollY;
 stopSlides();
 const alreadyOpen=$('#detail-dialog').open;
 const generation=++viewer.generation;
 viewer.goal=null; viewer.queued=null;
 if(!alreadyOpen){$('#detail-dialog').classList.add('hide-info');syncViewerTools();}
 const nextContext=context||currentBrowseContext();
 const sameContext=viewer.context && JSON.stringify(viewer.context)===JSON.stringify(nextContext);
 if(context || !sameContext || !viewer.ids.includes(id)){
  viewer.context=nextContext;viewer.ids=[];
  const params=new URLSearchParams({...viewer.context,sequence:true,limit:String(viewer.window),around:String(id)});
  const result=await api('/api/photos?'+params);
  if(generation!==viewer.generation)return;
  if(result.missing){viewerMessage('当前范围已变化，请刷新照片列表后再打开。');return;}
  viewer.ids=result.ids||[]; viewer.offset=result.offset||0; viewer.total=result.total||viewer.ids.length; viewer.maxId=result.max_id||viewer.context.max_id;
 }
 if(!viewer.ids.includes(id)){viewerMessage('当前范围已变化，请刷新照片列表后再打开。');return;}
 viewer.index=viewer.target=viewer.ids.indexOf(id);
 viewer.absolute=(viewer.offset||0)+viewer.index;
 viewer.goal=viewer.absolute;
 await displayPhoto(id);
 if(generation===viewer.generation)$('#image-viewport').focus({preventScroll:true});
}
async function ensureViewerWindow(absolute, generation=viewer.generation){
 const total=viewer.total||viewer.ids.length;
 const next=Math.max(0,Math.min(Math.max(total-1,0),absolute));
 const local=next-(viewer.offset||0);
 if(local>=0&&local<viewer.ids.length){
  viewer.absolute=next;
  viewer.target=local;
  return local;
 }
 const ctx={...viewer.context, max_id: viewer.maxId || viewer.context.max_id};
 const params=new URLSearchParams({...ctx,sequence:true,limit:String(viewer.window),offset:String(Math.max(0,next-Math.floor(viewer.window/2)))});
 const result=await api('/api/photos?'+params);
 if(generation!==viewer.generation)return null;
 viewer.ids=result.ids||[]; viewer.offset=result.offset||0; viewer.total=result.total||viewer.ids.length; viewer.maxId=result.max_id||viewer.maxId;
 const idx=Math.max(0, Math.min(viewer.ids.length-1, next-viewer.offset));
 viewer.absolute=next;
 viewer.index=viewer.target=idx;
 return idx;
}

async function movePhoto(delta,automatic=false){
 if(!automatic)stopSlides();
 const total=viewer.total||viewer.ids.length;
 const current=Number.isFinite(viewer.goal)?viewer.goal:(Number.isFinite(viewer.absolute)?viewer.absolute:(viewer.offset||0)+(viewer.target||0));
 const goal=Math.max(0,Math.min(Math.max(total-1,0),current+delta));
 viewer.goal=goal;
 if(goal===current && delta!==0){viewerMessage(goal===0?'已经是当前范围的第一张。':'已经是当前范围的最后一张。');stopSlides();return;}
 if(goal===current){stopSlides();return;}
 await consumeViewerGoal(automatic);
}

async function consumeViewerGoal(automatic=false){
 if(viewer.busy)return;
 viewer.busy=true;
 try{
  while($('#detail-dialog').open){
   const generation=viewer.generation;
   const want=Number.isFinite(viewer.goal)?viewer.goal:(Number.isFinite(viewer.absolute)?viewer.absolute:0);
   const local=await ensureViewerWindow(want, generation);
   if(!$('#detail-dialog').open)return;
   if(generation!==viewer.generation){
    continue;
   }
   if(local==null){
    if(generation!==viewer.generation) continue;
    return;
   }
   viewer.absolute=want;
   viewer.index=viewer.target=want-(viewer.offset||0);
   const id=viewer.ids[viewer.index];
   if(id==null){viewerMessage('当前范围已变化，请刷新照片列表后再打开。');return;}
   await displayPhoto(id);
   if(!$('#detail-dialog').open)return;
   if(generation!==viewer.generation) continue;
   if(!Number.isFinite(viewer.goal) || viewer.goal===want){
    if(automatic&&viewer.playing&&generation===viewer.generation&&$('#detail-dialog').open){
     if((viewer.absolute||0)>=(viewer.total||1)-1){stopSlides();viewerMessage('已经播放到当前范围的最后一张。');}
     else scheduleSlide();
    }
    break;
   }
  }
 }catch(e){stopSlides();throw e;}
 finally{
  viewer.busy=false;updatePosition();
  if($('#detail-dialog').open && Number.isFinite(viewer.goal) && viewer.goal!==viewer.absolute){
   consumeViewerGoal(automatic);
  }
 }
}

$('#viewer-prev').addEventListener('click',action(()=>movePhoto(-1)));
$('#viewer-next').addEventListener('click',action(()=>movePhoto(1)));
$('#zoom-in').addEventListener('click',()=>zoomTo(viewer.scale*1.25));
$('#zoom-out').addEventListener('click',()=>zoomTo(viewer.scale/1.25));
$('#zoom-fit').addEventListener('click',()=>{viewer.fit=true;updateZoom(true);});
$('#zoom-actual').addEventListener('click',()=>zoomTo(1));
$('#detail-img').addEventListener('load',()=>{updateZoom(true);renderFaceNames(state.detail);});
const faceLayer=$('#face-name-layer');
faceLayer&&faceLayer.addEventListener('pointerdown',e=>{if(e.target.closest('[data-face-person]')){e.stopPropagation();drag=null;}});
faceLayer&&faceLayer.addEventListener('click',e=>{
 const b=e.target.closest('[data-face-person]');
 if(!b)return;
 e.preventDefault();
 e.stopPropagation();
 outsidePhotoDown=false;
 const id=Number(b.dataset.facePerson);
 if(typeof openQuickName==='function') openQuickName(id);
});
$('#toggle-face-names').addEventListener('click',()=>{viewer.faceNames=!viewer.faceNames;renderFaceNames(state.detail);});
$('#toggle-face-alias').addEventListener('click',()=>{viewer.faceAlias=!viewer.faceAlias;renderFaceNames(state.detail);});
$('#toggle-face-dir').addEventListener('click',()=>{viewer.faceVertical=!viewer.faceVertical;renderFaceNames(state.detail);});
$('#detail-img').addEventListener('dblclick',()=>{if(viewer.fit)zoomTo(1);else{viewer.fit=true;updateZoom(true);}});
function toggleInfo(){$('#detail-dialog').classList.toggle('hide-info');syncViewerTools();}
$('#viewer-info').addEventListener('click',toggleInfo);
$('#close-info').addEventListener('click',toggleInfo);
$('#viewer-play').addEventListener('click',()=>{if(viewer.playing){stopSlides();syncViewerTools();return;}const current=Number.isFinite(viewer.absolute)?viewer.absolute:(viewer.offset||0)+(viewer.target||0);if(current>=(viewer.total||viewer.ids.length)-1){viewerMessage('已经是最后一张，请先返回前面的照片。');return;}viewer.playing=true;syncViewerTools();scheduleSlide();});
$('#slide-delay').addEventListener('change',()=>{if(viewer.timer)scheduleSlide();});
$('#detail-dialog').addEventListener('close',()=>{stopSlides();viewer.generation++;viewer.queued=null;viewer.goal=null;renderPhoto.ticket++;if(viewer.loader){viewer.loader.onload=null;viewer.loader.onerror=null;viewer.loader.src='';}if(document.fullscreenElement)document.exitFullscreen().catch(()=>{});});
$('#detail-dialog').addEventListener('cancel',()=>{viewer.generation++;renderPhoto.ticket++;});
document.addEventListener('visibilitychange',()=>{if(document.hidden)stopSlides();});
document.addEventListener('keydown',action(async e=>{
 if(!$('#detail-dialog').open||$$('dialog[open]').at(-1)?.id!=='detail-dialog'||e.target.closest('input,textarea,select,[contenteditable="true"]')||e.ctrlKey||e.altKey||e.metaKey)return;
 const directions={ArrowLeft:-1,ArrowUp:-1,ArrowRight:1,ArrowDown:1};
 if(e.key in directions){e.preventDefault();await movePhoto(directions[e.key]);}
 else if(e.key==='Home'||e.key==='End'){e.preventDefault();const total=viewer.total||viewer.ids.length;const current=Number.isFinite(viewer.absolute)?viewer.absolute:(viewer.offset||0)+(viewer.target||0);await movePhoto(e.key==='Home'?-current:total-1-current);}
}));
let drag=null;const viewport=$('#image-viewport');
viewport.addEventListener('pointerdown',e=>{if(e.button!==0||e.target.closest('.face-name,button,a,input,textarea,select'))return;drag={x:e.clientX,y:e.clientY,left:viewport.scrollLeft,top:viewport.scrollTop};viewport.setPointerCapture(e.pointerId);});
viewport.addEventListener('pointermove',e=>{if(drag){viewport.scrollLeft=drag.left+drag.x-e.clientX;viewport.scrollTop=drag.top+drag.y-e.clientY;}});
viewport.addEventListener('pointerup',()=>drag=null);viewport.addEventListener('pointercancel',()=>drag=null);
new ResizeObserver(()=>{if($('#detail-dialog').open)updateZoom();}).observe(viewport);
// Remember where a gesture began: dragging a zoomed photo onto the background
// must not close it. Buttons, links and the detail drawer keep their own actions.
let outsidePhotoDown=false;
const keepOpenSelector='#detail-img,button,a,input,textarea,select,.detail-info,.face-name-layer,.face-name,.viewer-tools,.viewer-arrow,.dialog-close,#photo-mat,#photo-signature';
$('#detail-dialog').addEventListener('pointerdown',e=>{
 outsidePhotoDown=e.button===0&&!e.target.closest(keepOpenSelector);
});
$('#detail-dialog').addEventListener('click',e=>{
 if(!outsidePhotoDown||e.target.closest(keepOpenSelector))return;
 outsidePhotoDown=false;$('#detail-dialog').close();
});
$('#detail-dialog').addEventListener('close',()=>outsidePhotoDown=false);
$('#detail-form').addEventListener('input',()=>{stopSlides();viewer.drafts.set(state.detail.id,Object.fromEntries(['#edit-date','#edit-precision','#edit-place','#edit-notes'].map(k=>[k,$(k).value])));viewerMessage('补录尚未保存；翻图时会暂存在本页，刷新页面会丢失。');});
$('#sort-order').addEventListener('change',action(async()=>{state.sort=$('#sort-order').value;state.offset=0;await loadPhotos();}));
$('#refresh-photos').addEventListener('click',action(()=>loadPhotos()));
$('#browse-directory').addEventListener('click',action(()=>{state.folderTarget='browse';return openFolder(state.directory);}));
