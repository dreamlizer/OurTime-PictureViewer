/* Tab-local browsing memory. No photos, faces or drafts are persisted here. */
(function(global){
 'use strict';
 const key='ourtime.browse.v1',views=new Map();
 const fields=['q','person','directory','place','dateFrom','dateTo','sort','folderPath','peopleSort','peoplePendingOnly','homeCategory','homeSnapshotId','homeGroupId','homeGroupTitle'];
 let active=null,pending=null,restoring=false,changing=false,scrollTimer=null,revision=0;
 const copy=value=>JSON.parse(JSON.stringify(value));
 const supported=view=>Boolean(titles[view]||/^(year|group|place):/.test(view));
 function snapshot(){
  if(!supported(state.view))return null;
  const item={version:1,view:state.view,scroll:scrollY,values:{},mapFilter:state.placeMapFilter?copy(state.placeMapFilter):null};
  for(const field of fields)item.values[field]=state[field]??'';
  if(state.view==='places')item.map=capturePlaceMapState();
  if(state.placeReturn)item.placeReturn=copy(state.placeReturn);
  if(typeof waterfall!=='undefined'&&waterfall.query&&!$('#library-view').hidden){
   item.total=waterfall.total;item.maxId=waterfall.maxId;
   // Geometry is compact; cache contents and original paths are not copied.
   if(waterfall.shapes.length<=2000)item.shapes=copy(waterfall.shapes);
   const cards=$$('#photo-grid [data-photo]');
   const anchor=cards.find(card=>card.getBoundingClientRect().bottom>180);
   if(anchor)item.anchor={id:Number(anchor.dataset.photo),position:Number(anchor.dataset.position),top:anchor.getBoundingClientRect().top};
  }
  return item;
 }
 function signature(item){return JSON.stringify([item.view,item.values,item.mapFilter]);}
function remember(item,push=false){
 if(!item)return;
 active=item;views.set(item.view,copy(item));
 if(views.size>12)views.delete(views.keys().next().value);
 try{
  const value={...(history.state||{}),ourtimeBrowse:copy(item)};
  history[push?'pushState':'replaceState'](value,'');
  try{sessionStorage.setItem(key,JSON.stringify(item));}catch(e){}
  if(push&&!updatingHash){const h=viewToHash(item);if(h!==location.hash)history.replaceState(null,'',h);}
 }catch(error){/* Quota/privacy modes must not block browsing. */}
}
 function capturePosition(){
  if(changing||restoring||!active||active.view!==state.view||document.querySelector('dialog[open]'))return;
  remember(snapshot());
 }
 function leaving(){
  if(!changing&&!restoring&&active&&active.view===state.view){
   // A click may already have changed state filters. Save the rendered query,
   // not those future inputs, together with the old scroll geometry.
   const item=snapshot();if(item){item.values=copy(active.values);item.mapFilter=copy(active.mapFilter);remember(item);}
  }
  changing=true;
 }
 function applyPending(view){
  if(!pending||pending.view!==view)return;
  for(const field of fields)if(Object.prototype.hasOwnProperty.call(pending.values,field))state[field]=pending.values[field];
  state.placeMapFilter=pending.mapFilter?copy(pending.mapFilter):null;
  state.placeReturn=pending.placeReturn?copy(pending.placeReturn):null;
  for(const [selector,field] of [['#search','q'],['#person-filter','person'],['#directory-filter','directory'],['#sort-order','sort']])setValue(selector,state[field]);
 }
 async function restorePosition(item){
  if(!item||state.view!==item.view)return;
  if(item.map&&state.placeMap&&item.view==='places'){
   state.placeMap.setView(item.map.center,item.map.zoom,{animate:false});updatePlaceZoomInput();
   await loadPlaceMap();return;
  }
  if(item.shapes&&item.anchor&&!$('#library-view').hidden){
   if(waterfall.maxId!==item.maxId||waterfall.total!==item.total)return;
   const generation=waterfall.generation;
   const page=await streamPage(Math.floor(item.anchor.position/waterfall.pageSize));
   if(generation!==waterfall.generation||!page)return;
   const offset=item.anchor.position%waterfall.pageSize;
   if(page.layout[offset]?.a.id!==item.anchor.id)return;
   waterfall.shapes=copy(item.shapes);streamReflow();streamPaint();
   const restored=waterfall.cache.get(Math.floor(item.anchor.position/waterfall.pageSize));
   const top=scrollY+$('#photo-grid').getBoundingClientRect().top+restored.layout[offset].y-item.anchor.top;
   restoreScrollInstant(top);streamPaint();await waitWaterfallFrames(2);streamPaint();
  }else if(!item.anchor){restoreScrollInstant(item.scroll||0);}
 }
 async function ready(){
  changing=false;
  if(restoring)return;
  const item=snapshot();if(!item)return;
  const push=active&&signature(active)!==signature(item);
  remember(item,Boolean(push));global.__ourTimeHomeSync?.();
 }
 async function photosLoaded(){if(!changing&&!restoring)await ready();}
 async function restore(item){
  if(!item||item.version!==1||!supported(item.view)||!item.values)return false;
  const token=++revision;restoring=true;pending=item;
  try{
    document.documentElement.classList.add('is-restoring-browse');
    document.querySelectorAll('dialog[open]').forEach(dialog=>dialog.close());
   await setView(item.view);if(token!==revision)return false;
   await restorePosition(item);if(token!==revision)return false;
   remember(snapshot());global.__ourTimeHomeSync?.();return true;
  }finally{
    document.documentElement.classList.remove('is-restoring-browse');
    if(token===revision){pending=null;restoring=false;changing=false;}
  }
 }
 async function navigate(view){
  if(restoring){++revision;pending=null;restoring=false;changing=false;}
  else capturePosition();
  const saved=views.get(view);
  if(saved){
   const previous=active;
   // Reserve the new entry before restore updates its scroll position.
   if(previous&&previous.view!==view)try{history.pushState({ourtimeBrowse:copy(saved)},'');}catch(error){}
   return restore(copy(saved));
  }
  return setView(view);
 }
async function start(view,force){
 history.scrollRestoration='manual';
 if(!force){
  const hashItem=parseHashToBrowse();
  if(hashItem){try{if(await restore(hashItem))return;}catch(error){pending=null;restoring=false;changing=false;}}
  try{const item=history.state?.ourtimeBrowse||JSON.parse(sessionStorage.getItem(key)||'null');if(await restore(item))return;}catch(error){pending=null;restoring=false;changing=false;}
 }
 await setView(view);
 if(location.search)try{history.replaceState(history.state||null,'',location.pathname+location.hash);}catch(e){}
}
 global.addEventListener('scroll',()=>{clearTimeout(scrollTimer);scrollTimer=setTimeout(capturePosition,250);},{passive:true});
 global.addEventListener('pagehide',capturePosition);
global.addEventListener('popstate',event=>{
 const item=event.state?.ourtimeBrowse;if(item)void restore(copy(item)).catch(error=>toast(error.message,true));
});
global.addEventListener('hashchange',()=>{
 if(updatingHash||restoring)return;
 const item=parseHashToBrowse();
 if(item)restore(copy(item)).catch(()=>{});
});
let updatingHash=false;
function viewToHash(item){
 if(!item||!item.view)return'#';
 const v=item.view,p=v.values||{};
 if(v==='home')return'#home';
 if(v==='timeline'||v==='years')return'#timeline';
 if(v==='people')return p.person?'#people/'+p.person:'#people';
 if(v==='places')return'#places';
 if(v==='groups')return'#groups';
 if(v==='favorites')return'#favorites';
 if(v==='folders')return'#folders';
 if(v==='scan')return'#scan';
 if(v==='passersby')return'#passersby';
 if(v==='uncertain')return'#uncertain';
 if(v==='no_place')return'#no_place';
 if(v==='duplicates')return'#duplicates';
 if(v==='screenshots')return'#screenshots';
 if(v==='errors')return'#errors';
 if(v==='missing')return'#missing';
 if(v==='excluded')return'#excluded';
 if(String(v).startsWith('year:'))return'#timeline/'+encodeURIComponent(String(v).slice(5));
 if(String(v).startsWith('group:'))return'#groups/'+encodeURIComponent(String(v).slice(6));
 if(String(v).startsWith('place:'))return'#places/'+encodeURIComponent(String(v).slice(6));
 return'#'+encodeURIComponent(v);
}
function parseHashToBrowse(){
 const h=location.hash.replace(/^#\/?'?/,'');
 if(!h)return null;
 const parts=h.split('/'),view=decodeURIComponent(parts[0]),id=parts[1]?decodeURIComponent(parts[1]):'';
 const item={version:1,view,scroll:0,values:{},mapFilter:null};
 if(view==='timeline'&&id)item.view='year:'+id;
 else if(view==='groups'&&id)item.view='group:'+id;
 else if(view==='places'&&id)item.view='place:'+id;
 else if(view==='people'&&id)item.values.person=id;
 return item;
}
 global.OurTimeContinuity={leaving,applyPending,ready,photosLoaded,navigate,start,capturePosition,isIdle:()=>!changing&&!restoring};
})(window);
