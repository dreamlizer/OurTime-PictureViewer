const $=s=>document.querySelector(s);
const $$=s=>[...document.querySelectorAll(s)];
const state={view:'timeline',q:'',person:'',place:'',dateFrom:'',dateTo:'',offset:0,total:0,items:[],people:[],passersby:[],selected:new Set(),peopleSelected:new Set(),peopleMerging:false,peoplePendingOnly:false,peopleSort:'photos',personLabels:{},selecting:false,detail:null,personId:null,status:null,folder:null,directory:'',sort:'date_desc',maxId:0,folderTarget:'scan',scanRoots:[],scanSubmitting:false,scanStartedThisVisit:false,scanShowCompletedResult:false,viewGeneration:0,viewAbort:null,folderNav:{generation:0,abort:null,parent:'',cache:{}},placeGeneration:0,placeAbort:null,photoMap:null,groupsGeneration:0,timelineGeneration:0,personOpenToken:0,exclusion:null,thumbRevision:0,peopleStream:{people:{items:[],offset:0,baseOffset:0,total:0,more:true,loading:false,q:'',generation:0},passersby:{items:[],offset:0,baseOffset:0,total:0,more:true,loading:false,q:'',generation:0}}};
const titles={all:['全部照片','全部照片'],folders:['文件夹','只看 I 盘。'],people:['人物档案','人物档案'],passersby:['路人','先不识别，以后还能找回来。'],timeline:['全部照片','全部照片'],years:['按年份查看','点某一年，只看那一年。'],places:['按地点','记得那是在哪里。'],objects:['物体','照片里有什么。'],groups:['合影','合影'],favorites:['收藏','收藏的照片'],uncertain:['待确认时间','给记忆一个时间。'],no_place:['待补充地点','记得那是在哪里吗？'],duplicates:['重复副本','一张照片，多个来处。'],screenshots:['截图与小图','日常的片段，也有位置。'],errors:['读取问题','把未完成的部分看清楚。'],missing:['原文件缺失','寻找照片现在的位置。'],scan:['添加照片','添加照片'],excluded:['已排除','留下值得保存的记忆。']};
const statuses={running:'正在扫描',pausing:'正在暂停',paused:'已暂停',cancelled:'已取消',completed:'已完成',completed_with_errors:'完成，有读取问题',failed:'扫描失败'};
function setText(sel,value){const el=$(sel); if(el) el.textContent=value;}
function setHtml(sel,value){const el=$(sel); if(el) el.innerHTML=value;}
function setHidden(sel,on){const el=$(sel); if(el) el.hidden=on;}
function setDisabled(sel,on){const el=$(sel); if(el) el.disabled=on;}
function setClass(sel,value){const el=$(sel); if(el) el.className=value;}
function setValue(sel,value){const el=$(sel); if(el) el.value=value;}
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function prettyPlace(value){return String(value||'').replace(/附近/g,'');}
function dateSource(v){return String(v||'').replace(/^EXIF /,'');}
function readableError(v){const t=String(v||'');if(/truncated|broken data stream/.test(t))return '图片数据不完整或已损坏';if(/cannot identify image/.test(t))return '无法识别图片格式，文件可能损坏';if(/PermissionError|Permission denied/.test(t))return '没有权限读取这个文件';if(/not found|No such file|不存在/.test(t))return '未找到原文件，请检查磁盘或重新扫描';if(/timeout|超时/i.test(t))return '读取超时，可以稍后重试';return '读取未完成，请查看原始诊断';}
function basename(p){return (p||'').split(/[\\/]/).pop();}
function fmt(n){return Number(n||0).toLocaleString('zh-CN');}
function toast(message,error=false){const el=$('#toast');if(!el)return;el.textContent=message;el.className='visible'+(error?' error':'');clearTimeout(toast.timer);toast.timer=setTimeout(()=>el.className='',error?9000:4500);}
function showNotice(message,error=false){const notice=$('#person-notice');if(notice&&$('#person-dialog')&&$('#person-dialog').open){notice.hidden=false;notice.textContent=message;notice.className='person-notice'+(error?' error':'');}toast(message,error);}
function hideNotice(){const notice=$('#person-notice');if(notice){notice.hidden=true;notice.textContent='';notice.className='person-notice';}}
function apiError(value,text,status){const detail=value&&value.detail;if(typeof detail==='string')return detail;if(Array.isArray(detail)){return detail.map(x=>{if(typeof x==='string')return x;const loc=Array.isArray(x&&x.loc)?x.loc:[];if(x&&x.msg==='Field required'&&(loc.includes('target_id')||loc.includes('body')))return '请先选择要合并到的人';return (x&&(x.msg||x.message))||JSON.stringify(x);}).filter(Boolean).join('；');}if(detail)return JSON.stringify(detail);return (text||'').slice(0,180)||('请求失败 '+status);}
async function api(url,options={}){const headers={'Accept':'application/json',...(options.headers||{})};if(options.body!=null&&!headers['Content-Type'])headers['Content-Type']='application/json';const timeoutMs=options.timeoutMs;const extra={...options};delete extra.timeoutMs;let timer=null;let timedOut=false;if(timeoutMs&&!extra.signal){const controller=new AbortController();extra.signal=controller.signal;timer=setTimeout(()=>{timedOut=true;controller.abort();},timeoutMs);}try{const res=await fetch(url,{...extra,headers});const text=await res.text();let value={};if(text){try{value=JSON.parse(text);}catch(err){throw new Error(res.ok?'服务器返回了无法解析的内容':((text||'').slice(0,180)||('请求失败 '+res.status)));}}if(!res.ok)throw new Error(apiError(value,text,res.status));return value;}catch(err){if(err&&err.name==='AbortError'){if(timedOut)throw new Error('后台响应超时，请稍后刷新');const abortErr=new Error('Aborted');abortErr.name='AbortError';throw abortErr;}throw err;}finally{if(timer)clearTimeout(timer);}}
function action(fn){return async(...args)=>{try{await fn(...args);}catch(e){if(e&&e.name==='AbortError')return;showNotice(e.message,true);}};}

function viewPanel(view=state.view){
  if(view==='people')return $('#people-view');
  if(view==='passersby')return $('#passersby-view');
  if(view==='folders')return $('#folders-view');
  if(view==='places'||isPhotoPlaceView(view))return $('#places-view');
  if(view==='groups')return $('#groups-view');
  if(view==='years')return $('#timeline-view');
  if(view==='scan')return $('#scan-view');
  if(view==='objects')return $('#objects-view');
  return $('#library-view');
}
function beginViewChange(view){
  state.viewGeneration=(state.viewGeneration||0)+1;
  if(state.viewAbort)state.viewAbort.abort();
  state.viewAbort=new AbortController();
  return {generation:state.viewGeneration,signal:state.viewAbort.signal,view};
}
function isCurrentView(token, view=state.view){
  return token && token.generation===state.viewGeneration && (!view || state.view===view);
}
function preparePanel(panel){
  if(!panel)return;
  panel.classList.add('is-switching');
  panel.classList.remove('is-ready','is-pending');
  if(panel._pendingTimer)clearTimeout(panel._pendingTimer);
  panel._pendingTimer=setTimeout(()=>{if(panel.classList.contains('is-switching'))panel.classList.add('is-pending');},120);
}
function revealPanel(panel, token){
  if(!panel||(token&&!isCurrentView(token)))return;
  if(panel._pendingTimer){clearTimeout(panel._pendingTimer);panel._pendingTimer=null;}
  panel.classList.remove('is-pending');
  requestAnimationFrame(()=>{
    panel.classList.remove('is-switching');
    panel.classList.add('is-ready');
  });
}
function showDialog(id){const el=$(id);if(!el||el.open)return;el.classList.add('dialog-enter');el.classList.remove('dialog-open');el.showModal();requestAnimationFrame(()=>{requestAnimationFrame(()=>el.classList.add('dialog-open'));});}
function isTimelineView(view=state.view){return view==='timeline'||String(view).startsWith('year:');}
function isGroupView(view=state.view){return view==='groups'||String(view).startsWith('group:');}
function isPhotoPlaceView(view=state.view){return String(view||'').startsWith('photo-place:');}
function groupFilterRange(key){key=String(key||'');if(key==='10plus')return {min:10,max:null};let match=key.match(/^(\d+)$/);if(match)return {min:Number(match[1]),max:Number(match[1])};match=key.match(/^(\d+)-(\d+)$/);if(match)return {min:Number(match[1]),max:Number(match[2])};match=key.match(/^(\d+)plus$/);if(match)return {min:Number(match[1]),max:null};match=key.match(/^upto(\d+)$/);if(match)return {min:null,max:Number(match[1])};return {min:null,max:null};}
function groupFilterLabel(key){const {min,max}=groupFilterRange(key);if(min&&max)return min===max?min+'人':min+'–'+max+'人';if(min)return min+'人以上';if(max)return max+'人以下';return '';}
function groupTitle(view=state.view){if(String(view).startsWith('group:')){const label=groupFilterLabel(view.slice(6));return ['合影',label?label+'合影':'合影'];}return ['合影','合影'];}
function timelineTitle(view=state.view){if(String(view).startsWith('year:')){const year=view.slice(5);return ['全部照片',year==='unknown'?'时间未记录':year+' 年'];}return ['全部照片','最新的照片在前面。'];}
function folderDrive(path){const raw=String(path||''); const m=raw.match(/^[A-Za-z]:/); return m?m[0].toUpperCase():'';}
const FOLDER_ONLY_DRIVE='I:';
function folderSep(){return String.fromCharCode(92);}
function isFolderSlash(ch){return ch==='/' || ch===folderSep();}
function folderNormalize(path){
  const raw=String(path||'').trim();
  if(!raw)return '';
  const sep=folderSep();
  let value='';
  for(let i=0;i<raw.length;i++){
    const ch=raw[i];
    if(isFolderSlash(ch)){
      if(!value.endsWith(sep)) value+=sep;
    }else value+=ch;
  }
  if(/^[A-Za-z]:$/.test(value) || (/^[A-Za-z]:$/.test(value.replace(sep,'')) && value.length<=3 && isFolderSlash(value.slice(-1)))){
    return value[0].toUpperCase()+':'+sep;
  }
  if(/^[A-Za-z]:/.test(value)) value=value[0].toUpperCase()+value.slice(1);
  if(!(/^[A-Za-z]:$/.test(value) || (value.length===3 && /^[A-Za-z]:/.test(value) && isFolderSlash(value[2])))){
    while(value.length>3 && isFolderSlash(value.slice(-1))) value=value.slice(0,-1);
  }
  return value;
}
function folderParts(path){
  const current=folderNormalize(path);
  if(!current)return [];
  const parts=[]; let buf='';
  for(let i=0;i<current.length;i++){
    const ch=current[i];
    if(isFolderSlash(ch)){
      if(buf){parts.push(buf); buf='';}
    }else buf+=ch;
  }
  if(buf) parts.push(buf);
  return parts;
}
function folderKey(path){return folderNormalize(path).toLowerCase();}
function folderParent(path){
  const current=folderNormalize(path);
  if(!current)return '';
  if(current.length<=3 && /^[A-Za-z]:/.test(current)) return '';
  const parts=folderParts(current);
  if(parts.length<=1) return parts[0] ? parts[0]+folderSep() : '';
  const drive=parts[0].endsWith(':')?parts[0]+folderSep():parts[0];
  return parts.length===2 ? drive : drive+parts.slice(1,-1).join(folderSep());
}
function resetPhotoStream(){waterfall.abort?.abort();waterfall.abort=new AbortController();waterfall.generation++;waterfall.pending.clear();waterfall.cache.clear();waterfall.heights=[];waterfall.total=0;waterfall.maxId=0;waterfall.error=false;waterfall.query=null;streamEntrance.disconnect();$('#photo-grid').replaceChildren();$('#photo-grid').style.height='0px';$('#stream-status').textContent='正在加载照片…';$('#stream-retry').hidden=true;$('#no-results').hidden=true;$('#empty').hidden=true;$('#result-count').textContent='加载中';syncGroupResultCount(state.view,{clear:true});}
function currentPlaceName(){return String(state.view||'').startsWith('place:')?state.view.slice(6):'';}
function updatePlaceRefine(){
  const box=$('#place-refine'); if(!box)return;
  const place=currentPlaceName();
  const on=Boolean(place) && place!=='unknown';
  box.hidden=!on;
  if(!on)return;
  $('#place-refine-title').textContent='地点名称';
  $('#place-refine-input').value=place.endsWith('附近')?place.replace(/附近$/,''):place;
  loadPlaceSuggestions($('#place-refine-input').value.trim());
}
async function loadPlaceSuggestions(q){
  const box=$('#place-refine-suggest'); if(!box)return;
  if(!q){box.innerHTML='';return;}
  const data=await api('/api/places/suggest?q='+encodeURIComponent(q));
  box.innerHTML=(data.items||[]).map(item=>'<button type="button" class="place-suggest '+(item.kind==='catalog'?'matched':'custom')+'" data-place-suggest="'+esc(item.name)+'">'+esc(item.name)+(item.kind==='catalog'?'<small>附近可匹配</small>':'<small>地图里没有，将手填保存</small>')+'</button>').join('');
}
async function savePlaceRefine(){
  const source=currentPlaceName();
  const target=($('#place-refine-input')&&$('#place-refine-input').value||'').trim();
  if(!source||source==='unknown')throw new Error('请先进入一个具体地点');
  if(!target)throw new Error('请填写更具体的地点');
  const data=await api('/api/places/rename',{method:'POST',body:JSON.stringify({from_place:source,to_place:target,remember:true})});
  toast(data.matched?('已保存为 '+data.name+'，并记住这个更细的地点'):('已保存为 '+data.name+'。附近地图没有这一条，先按手填记下'));
  await setView('place:'+data.name);
}
function updateTimelineTools(){const on=isTimelineView();$('#timeline-tools').hidden=!on;if(on){$$('#timeline-tools [data-time-sort]').forEach(b=>b.classList.toggle('active',b.dataset.timeSort===state.sort));$('#timeline-by-year').classList.toggle('active',state.view==='years');}document.body.classList.toggle('is-add-photos-view',state.view==='scan');window.__ourTimeHomeSync?.();}
function organizeViews(){return new Set(['passersby','uncertain','no_place','duplicates','screenshots','excluded']);}
function statsViews(){return new Set(['timeline','all']);}
function updateChrome(){
  const view=state.view;
  const organize=organizeViews().has(view);
  const showStats=view==='timeline';
  const stats=$('#stats'); if(stats) stats.hidden=!showStats;
  document.body.classList.toggle('is-subpage',!showStats);
  document.body.classList.toggle('is-people-view',view==='people');
  document.body.classList.toggle('is-photo-place-view',isPhotoPlaceView(view));
  document.body.classList.toggle('is-place-detail-view',String(view).startsWith('place:'));
  const stickyBrowserView=view==='people'||['timeline','folders','places','groups','favorites'].includes(view)||String(view).startsWith('place:')||String(view).startsWith('group:')||isPhotoPlaceView(view);
  document.body.classList.toggle('is-sticky-browser-view',stickyBrowserView);
  const namedCount=$('#people-named-count'); if(namedCount) namedCount.hidden=view!=='people';
  document.body.classList.toggle('is-group-query',String(view).startsWith('group:'));
  const groupDetail=String(view).startsWith('group:');
  const groupSort=$('#sort-order option[data-group-sort]');
  if(groupSort)groupSort.hidden=!groupDetail;
  if(!groupDetail&&state.sort==='recognized_desc'){
    state.sort='date_desc';
    if($('#sort-order'))$('#sort-order').value=state.sort;
  }
  const organizeNav=$('#organize-nav');
  if(organizeNav) organizeNav.open=organize;
  $$('.nav[data-view]').forEach(el=>{
    el.classList.toggle('active',el.dataset.view===view||(el.dataset.view==='timeline'&&isTimelineView(view))||(el.dataset.view==='places'&&(String(view).startsWith('place:')||isPhotoPlaceView(view)))||(el.dataset.view==='groups'&&isGroupView(view)));
  });
}
async function loadFolderBrowser(path, viewToken=null){
  const next=path||'';
  state.folderPath=next;
  const nav=state.folderNav||(state.folderNav={generation:0,abort:null,parent:'',cache:{}});
  nav.generation+=1; const ticket=nav.generation;
  if(nav.abort)nav.abort.abort();
  nav.abort=new AbortController();
  const list=$('#folders-list');
  if(list){list.classList.add('is-switching'); list.querySelectorAll('[data-folder-open]').forEach(el=>el.classList.toggle('is-active', el.dataset.folderOpen===next));}
  const cached=nav.cache[next];
  try{
    const [data,ex]=cached? [cached.data, cached.ex] : await Promise.all([
      api('/api/folders?path='+encodeURIComponent(next), {signal:nav.abort.signal}),
      api('/api/exclusions', {signal:nav.abort.signal})
    ]);
    if(ticket!==nav.generation)return;
    if(viewToken && !isCurrentView(viewToken, 'folders')) return;
    nav.cache[next]={data,ex};
    nav.parent=data.parent||'';
    const excluded=new Set((ex.roots||[]).map(r=>folderKey(r.path||'')));
    const current=folderNormalize(data.path||'');
    const parts=folderParts(current);
    const depth=parts.length;
    const up=$('#folders-up'); if(up) up.hidden=!data.path;
    const trail=$('#folders-trail');
    if(trail){
      trail.hidden=false;
      const crumbs=[];
      crumbs.push('<button type="button" class="trail-item'+(depth?'' :' current')+'" data-folder-open="'+esc(FOLDER_ONLY_DRIVE+'\\')+'">I 盘</button>');
      if(parts.length){
        const slash=String.fromCharCode(92); const drive=parts[0].endsWith(":")?parts[0]+slash:parts[0];
        crumbs.push('<button type="button" class="trail-item'+(parts.length===1?' current':'')+'" data-folder-open="'+esc(drive)+'">'+esc(parts[0])+'</button>');
        let acc=drive.endsWith(slash)?drive.slice(0,-1):drive;
        for(let i=1;i<parts.length;i++){
          acc+=slash+parts[i];
          crumbs.push('<button type="button" class="trail-item'+(i===parts.length-1?' current':'')+'" data-folder-open="'+esc(acc)+'">'+esc(parts[i])+'</button>');
        }
      }
      trail.innerHTML=crumbs.join('<span class="trail-sep">/</span>');
    }
    const total=$('#folders-total');
    if(!list)return;
    const slash=String.fromCharCode(92);
    const items=(data.items||[]).filter(item=>depth>0 || folderDrive(item.path)===FOLDER_ONLY_DRIVE);
    if(total) total.textContent=items.length+' 项';
    if(!state.folderPath && items.length===1){
      await loadFolderBrowser(items[0].path, viewToken);
      return;
    }
    list.dataset.depth=String(Math.min(depth,3));
    list.dataset.currentPath=current;
    list.innerHTML=items.map(item=>{
      const key=folderKey(item.path||'');
      const on=!excluded.has(key) && ![...excluded].some(r=>key===r || key.startsWith(r+slash) || key.startsWith(r+'/'));
      const kind=depth===0?'drive':(depth===1?'folder':(depth===2?'sub':'deep'));
      const label=depth===0?'I 盘':item.name;
      const add=depth===0?'<button type="button" class="text-button folder-add" data-folder-add="'+esc(item.path)+'">扫描文件夹</button>':'';
      return '<article class="folder-card '+kind+'" data-folder-open="'+esc(item.path)+'"><input class="folder-tick" type="checkbox" data-folder-toggle="'+esc(item.path)+'" '+(on?'checked':'')+' aria-label="包含这里"><span class="folder-name">'+esc(label)+'</span>'+add+'</article>';
    }).join('')||'<div class="no-results">I 盘还没有可看的文件夹。</div>';
    list.classList.remove('is-switching');
  }catch(err){
    if(err&&err.name==='AbortError')return;
    throw err;
  }
}
async function toggleFolderInclusion(path, include){
  if(include){
    const data=await api('/api/exclusions');
    const hit=(data.roots||[]).find(r=>folderKey(r.path||'')===folderKey(path||''));
    if(hit) await api('/api/exclusions/roots/'+hit.id,{method:'DELETE'});
    toast('已重新纳入浏览');
  }else{
    await api('/api/exclusions/roots',{method:'POST',body:JSON.stringify({path})});
    toast('已从工作台屏蔽，原文件还在');
  }
  await loadFolderBrowser(state.folderPath||'');
  refreshStatus().catch(()=>{});
}
function renderScanRoots(){const list=$('#scan-root-list');if(!list)return;const roots=state.scanRoots||[];list.hidden=!roots.length;list.innerHTML=roots.map((path,index)=>`<div class="scan-root"><span>${esc(path)}</span><button type="button" class="text-button" data-remove-scan-root="${index}" aria-label="移除 ${esc(path)}">×</button></div>`).join('');setHidden('#scan-folder-picker',Boolean(roots.length));setHidden('#scan-add-folder',!roots.length);const start=$('#start-scan');if(start){start.disabled=!roots.length||state.scanSubmitting||Boolean(state.status?.job&&['running','pausing'].includes(state.status.job.status));}}
function addScanRoot(path){const value=folderNormalize(path);if(!value)return;if(!(state.scanRoots||[]).some(item=>folderKey(item)===folderKey(value)))state.scanRoots.push(value);renderScanRoots();}
function removeScanRoot(index){state.scanRoots.splice(Number(index),1);renderScanRoots();}
const scanStageLabels={inventory:'正在清点照片',checking:'正在核对已有记录',fingerprint:'正在核对照片内容',metadata:'正在读取拍摄信息',thumbnail:'正在补建照片预览',faces:'正在识别人脸',complete:'扫描完成',failed:'扫描失败'};
function renderScanStatus(data){
  const j=data&&data.job;const active=Boolean(j&&['running','pausing'].includes(j.status));const paused=Boolean(j&&j.status==='paused');const terminal=Boolean(j&&['completed','completed_with_errors','failed'].includes(j.status));const showCompleted=Boolean(terminal&&state.scanStartedThisVisit&&state.scanShowCompletedResult);const showProgress=active||paused||showCompleted;const card=$('.add-photos-card');const progress=$('#scan-progress');if(!card||!progress)return;
  card.hidden=active;progress.hidden=!showProgress;
  const inventory=j?.phase==='inventory';const total=Number(j?.total||0);const processed=Number(j?.processed||0);const discovered=Number(j?.discovered||0);const remaining=total?Math.max(0,total-processed):null;const percent=total?Math.min(100,Math.round(processed/total*100)):0;
  setText('#scan-progress-title',paused?'上次扫描已暂停':showCompleted?'照片已经添加':inventory?'正在清点照片':active?'正在添加照片':'扫描状态');
  setText('#scan-stage',paused?'扫描已暂停':inventory?`正在清点文件，已发现 ${fmt(discovered)} 张照片`:(scanStageLabels[j?.current_stage]||(active?'正在处理照片':j?.message||'')));
  setText('#scan-fraction',inventory?`${fmt(discovered)} 张`:total?`${fmt(processed)} / ${fmt(total)}`:fmt(processed));
  setText('#scan-checked',fmt(processed));setText('#scan-remaining',remaining===null?'—':fmt(remaining));setText('#scan-added',fmt(j?.metadata_reads));setText('#scan-skipped',fmt(j?.skipped));setText('#scan-errors-count',fmt(j?.errors));
  const track=$('#scan-progress-track'),fill=$('#scan-progress-fill');track.classList.toggle('is-inventory',Boolean(active&&inventory));track.setAttribute('aria-valuenow',String(inventory?discovered:processed));if(total)track.setAttribute('aria-valuemax',String(total));else track.removeAttribute('aria-valuemax');fill.style.width=inventory?'':percent+'%';
  const withFaces=Boolean(j?.with_faces);setHidden('#scan-face-progress',!withFaces);setText('#scan-face-photos',fmt(j?.face_photos));setText('#scan-faces-found',fmt(j?.faces_found));const average=Number(data?.face_average_seconds||0);setText('#scan-face-average',average?average.toFixed(2)+' 秒/张':'—');
  const current=j?.current_path?basename(j.current_path):'';setText('#scan-current',paused?'':active&&current?`${scanStageLabels[j.current_stage]||'正在处理'}：${current}`:(active?'':j?.message||''));const currentEl=$('#scan-current');if(currentEl)currentEl.title=j?.current_path||'';
  setHidden('#scan-view-all',!j||!['completed','completed_with_errors'].includes(j.status));setHidden('#scan-view-errors',!showCompleted||!Number(j?.errors));renderScanRoots();if(j&&state.view==='scan'){setHtml('#job-actions',paused?'<button type="button" class="primary small" id="resume-scan">继续上次扫描</button><button type="button" class="text-button" id="cancel-scan">取消</button>':active?'<button type="button" class="secondary small" id="pause-scan">暂停</button>':'');}
}
function openAddPhotos(initialRoot=''){state.scanRoots=[];state.scanSubmitting=false;if(initialRoot)addScanRoot(initialRoot);else renderScanRoots();return setView('scan');}
function currentPageTitle(view, fallback){
  if(view==='people')return '人物档案';
  if(String(view).startsWith('group:'))return groupTitle(view)[1];
  if(isPhotoPlaceView(view))return '这张照片的位置';
  return fallback;
}
function syncGroupResultCount(view=state.view, {clear=false, total=null}={}){
  const el=$('#group-result-count');
  if(!el)return;
  const on=String(view||'').startsWith('group:');
  if(!on || clear || total==null){
    el.hidden=true;
    el.textContent='';
    return;
  }
  el.textContent=fmt(total)+' 张';
  el.hidden=false;
}
async function setView(view){if(view==='all')view='timeline';const previousView=state.view;if(previousView==='scan'&&view!=='scan'){state.scanStartedThisVisit=false;state.scanShowCompletedResult=false;}if(view==='scan'&&previousView!=='scan'){state.scanStartedThisVisit=false;state.scanShowCompletedResult=false;}const leavingHome=previousView==='timeline'||String(previousView).startsWith('group:');const enteringHome=view==='timeline'||String(view).startsWith('group:');if(leavingHome&&!enteringHome){state.q='';state.person='';state.directory='';state.place='';state.dateFrom='';state.dateTo='';}if(['people','passersby','scan','places','years','groups','objects'].includes(view)||String(view).startsWith('year:')||String(view).startsWith('place:')||String(view).startsWith('group:'))resetPhotoStream();state.view=view;const viewToken=beginViewChange(view);$('#exclusion-panel').hidden=view!=='excluded';if(view==='excluded')await loadExclusionRules(viewToken);if(!isCurrentView(viewToken, view))return;state.offset=0;state.selected.clear();state.selecting=false;if(view!=='people')setPeopleMerging(false);updateBatch();$$('.nav[data-view]').forEach(el=>el.classList.toggle('active',el.dataset.view===view||(el.dataset.view==='timeline'&&isTimelineView(view))||(el.dataset.view==='places'&&String(view).startsWith('place:'))||(el.dataset.view==='groups'&&isGroupView(view))));const title=titles[view]||(isTimelineView(view)?timelineTitle(view):isGroupView(view)?groupTitle(view):String(view).startsWith('place:')?['按地点',view.slice(6)]:[view,view]);const isGroupDetail=String(view).startsWith('group:');$('#breadcrumb').textContent=title[0];$('#page-title').textContent=currentPageTitle(view,title[1]);const groupsBack=$('#groups-back');if(groupsBack)groupsBack.hidden=!isGroupDetail;syncGroupResultCount(view,{clear:true});const hideLibrary=['people','passersby','scan','years','places','groups','folders'].includes(view) && !isGroupDetail;$('#library-view').hidden=hideLibrary;$('#people-view').hidden=view!=='people';$('#passersby-view').hidden=view!=='passersby';$('#timeline-view').hidden=view!=='years';$('#places-view').hidden=view!=='places';$('#groups-view').hidden=view!=='groups';$('#objects-view').hidden=view!=='objects';$('#scan-view').hidden=view!=='scan';const foldersView=$('#folders-view');if(foldersView)foldersView.hidden=view!=='folders';$('#collection-title').textContent=title[0];updateChrome();updateTimelineTools();updatePlaceRefine();const panel=viewPanel(view);preparePanel(panel);try{if(view==='people'){state.peopleSelected.clear();setPeopleMerging(false);await loadPeople(viewToken);}else if(view==='passersby')await loadPassersby(viewToken);else if(view==='years')await loadTimeline(viewToken);else if(view==='places')await loadPlaces(true,viewToken);else if(view==='groups')await loadGroups(viewToken);else if(view==='objects'){await setView('timeline');return;}else if(view==='folders')await loadFolderBrowser(state.folderPath||'',viewToken);else if(view==='scan'){renderScanRoots();await refreshStatus();}else {if((isTimelineView(view)||String(view).startsWith('group:'))&&!['date_asc','date_desc'].includes(state.sort)){state.sort='date_desc';$('#sort-order').value=state.sort;}await loadPhotos();refreshStatus();}if(isCurrentView(viewToken,view))revealPanel(panel,viewToken);}catch(err){if(!(err&&err.name==='AbortError'))throw err;}}

async function refreshStatus(){const previous=state.status;const data=await api('/api/status',{timeoutMs:15000});const previousActive=Boolean(previous?.job&&['running','pausing','paused'].includes(previous.job.status));const terminal=Boolean(data.job&&['completed','completed_with_errors','failed'].includes(data.job.status));if(state.view==='scan'&&state.scanStartedThisVisit&&previousActive&&terminal)state.scanShowCompletedResult=true;state.status=data;const s=data.stats;setText('#s-assets',fmt(s.assets));setText('#nav-count',fmt(s.assets));setText('#excluded-count',fmt(s.excluded_assets));if(typeof s.group_photos==='number'&&Number.isFinite(s.group_photos))setText('#groups-count',fmt(s.group_photos));state.connectionNotice=false;setText('#s-people',fmt(s.people));setText('#people-count',fmt(s.people));setText('#s-uncertain',fmt(s.uncertain_dates));setText('#s-duplicates',fmt(s.duplicates));const j=data.job;renderScanStatus(data);const active=j&&['running','pausing'].includes(j.status);const bannerProgress=j?.phase==='inventory'?`正在清点 · 已发现 ${fmt(j.discovered)} 张照片`:j?.total?`已检查 ${fmt(j.processed)} / ${fmt(j.total)}`:`本轮已检查 ${fmt(j?.processed)} 个文件`;const running=$('#running-dot'); if(running) running.className=active?'active':'';setDisabled('#start-scan',Boolean(active)||!state.scanRoots.length||state.scanSubmitting);setHidden('#job-banner',!active);setHtml('#job-banner',active?`<span>● ${statuses[j.status]} · ${bannerProgress}</span><button class="text-button" id="banner-details">查看进度 →</button>`:'');setText('#job-status',j?statuses[j.status]:'');if(j)setHtml('#job-details',`<div>${esc(JSON.parse(j.roots).join('；'))}</div><div class="job-metrics"><span><b>${fmt(j.discovered)}</b>本轮发现文件</span><span><b>${fmt(j.processed)}</b>本轮已检查</span><span><b>${fmt(j.metadata_reads)}</b>新读取元数据</span><span><b>${fmt(j.skipped)}</b>已完成直接跳过</span><span><b>${fmt(j.errors)}</b>读取问题</span></div><p>${esc(j.message||'正在扫描；已完成文件会快速跳过。')} · ${j.workers} 路处理 · 近一分钟新资料读取 ${data.metadata_per_second} 张/秒 · 略过 ${fmt(j.auxiliary)} 个辅助文件</p><div class="path-line">${esc(j.current_path)}</div>`);setHidden('#error-details',!data.errors.length);setText('#error-summary',`读取问题：共 ${fmt(j?.errors)} 项，显示最近 ${data.errors.length} 项`);setHtml('#scan-errors',data.errors.map(e=>`<div class="error-row"><b>${esc(e.stage)}</b><div>${esc(e.path)}</div><div>${esc(readableError(e.message))}</div><details><summary>原始诊断（技术信息）</summary><pre>${esc(e.message)}</pre></details></div>`).join(''));const cap=data.capabilities;setHtml('#capabilities',`人脸模型：${cap.face_model?'已找到 · '+(cap.face_runtime||'处理器')+' 运行':'未找到'}<br>HEIC / HEIF：${cap.heif?'可读取':'尚未安装解码组件，遇到时会记录问题'}<br>扩展元数据：${cap.exiftool?'已就绪 · 常驻进程':'仅基础读取器'}<br>离线地名：${cap.geo?'中文及海外地名数据已就绪':'未找到，保留 GPS 原始坐标'}<br>资料库：${esc(cap.data_dir)}`);if(previousActive&&!active&&terminal){toast(j.message||'扫描结束');if(state.view==='people')await loadPeople();else if(state.view==='passersby')await loadPassersby();else if(!['scan'].includes(state.view))await loadPhotos();}if(j&&!state.scanPrefilled){$('#scan-roots').value=JSON.parse(j.roots).join('\n');$('#scan-workers').value=String(j.workers);state.scanPrefilled=true;}return data;}
function personLabel(p){if(p.ignored)return p.name||'路人';return p.alias?(p.name||('待命名 '+p.id))+' / '+p.alias:(p.name||('待命名 '+p.id));}
function personStatus(p){if(p.ignored)return '路人 · 暂不识别';if(p.confirmed)return p.alias?'已命名 · '+p.alias:'已人工确认';return p.suggested_name?'可能是 '+p.suggested_name:'待核对分组';}
function updatePeopleMerge(){const n=state.peopleSelected.size;const merging=Boolean(state.peopleMerging);const bar=$('#people-merge-bar');if(!bar)return;bar.hidden=!merging;const count=$('#people-selected-count');if(count)count.textContent='已选 '+n+' 组';const merge=$('#merge-selected-people');if(merge)merge.disabled=n<2;const grid=$('#people-grid');if(grid)grid.classList.toggle('selecting',merging);const toggle=$('#people-merge-toggle');if(toggle){toggle.textContent=merging?'取消合并':'合并人物';toggle.setAttribute('aria-pressed',merging?'true':'false');}document.body.classList.toggle('is-people-merging',merging&&state.view==='people');}
function togglePersonSelection(id, selected){id=Number(id);if(!id)return;const on=selected==null?!state.peopleSelected.has(id):Boolean(selected);if(on)state.peopleSelected.add(id);else state.peopleSelected.delete(id);const card=$(`#people-grid [data-person="${id}"]`);if(card)card.classList.toggle('selected',on);const box=card&&card.querySelector('[data-select-person]');if(box)box.checked=on;updatePeopleMerge();}
function isPeopleSelecting(){return state.view==='people'&&Boolean(state.peopleMerging);}
function rememberPersonLabel(person){if(!person||person.id==null)return;const id=String(person.id);const label=personLabel(person);state.personLabels=state.personLabels||{};state.personLabels[id]=label;window.__ourTimeRememberPersonLabel?.(id,label);}
function setPeopleMerging(on){
  state.peopleMerging=Boolean(on);
  if(state.peopleMerging){
    $$('#people-grid .person-card').forEach(card=>{
      if(card.querySelector('.person-check'))return;
      const id=Number(card.dataset.person);
      card.insertAdjacentHTML('afterbegin',`<label class="person-check"><input type="checkbox" data-select-person="${id}" ${state.peopleSelected.has(id)?'checked':''} aria-label="选择这个人"></label>`);
    });
  }else{
    state.peopleSelected.clear();
    $$('#people-grid .person-card').forEach(card=>card.classList.remove('selected'));
    $$('#people-grid .person-check').forEach(check=>check.remove());
  }
  updatePeopleMerge();
}
function peopleQuery(extra={}){const q=new URLSearchParams({ignored:String(extra.ignored||0),offset:String(extra.offset||0),limit:String(extra.limit||48)});if(extra.q)q.set('q',extra.q);if(extra.ids)q.set('ids',extra.ids);if(extra.named)q.set('named','1');if(extra.sort)q.set('sort',extra.sort);return '/api/people?'+q.toString();}
function patchPersonInStream(id, patch){id=Number(id);const stream=state.peopleStream&&state.peopleStream.people;if(!stream||!stream.items)return null;const item=stream.items.find(p=>p.id===id);if(!item)return null;Object.assign(item,patch);return item;}
 function personStreamKind(kind='people'){return kind==='passersby'?'passersby':'people';}
 function personGridSelector(kind='people'){return personStreamKind(kind)==='passersby'?'#passersby-grid':'#people-grid';}
 function personTotalSelector(kind='people'){return personStreamKind(kind)==='passersby'?'#passersby-total':'#people-total';}
 let namedPeopleCountTimer=null;
 function setNamedPeopleCount(total){const el=$('#people-named-count');if(el)el.textContent='已命名 '+fmt(total)+' 人';}
 async function refreshNamedPeopleCount(){const data=await api(peopleQuery({limit:1,named:1}));setNamedPeopleCount(data.total);}
 function queueNamedPeopleCountRefresh(){clearTimeout(namedPeopleCountTimer);namedPeopleCountTimer=setTimeout(()=>refreshNamedPeopleCount().catch(()=>{}),120);}
 function personCardById(id,kind='people'){const numeric=Number(id);if(!Number.isInteger(numeric)||numeric<1)return null;return document.querySelector(`${personGridSelector(kind)} [data-person="${numeric}"]`);}
 function renderPersonCard(person,kind='people'){kind=personStreamKind(kind);const card=personCardById(person.id,kind);if(!card)return;const wrap=document.createElement('div');wrap.innerHTML=personCard(person,kind);const fresh=wrap.firstElementChild;if(!fresh)return;card.replaceWith(fresh);}
 const personNameCollator=new Intl.Collator('zh-CN-u-co-pinyin',{usage:'sort',sensitivity:'base'});
 function comparePeopleOrder(a,b){
  const namedOrder=Number(isNamedPerson(b))-Number(isNamedPerson(a));if(namedOrder)return namedOrder;
  if(isNamedPerson(a)&&isNamedPerson(b)&&state.peopleSort!=='photos'){
   const direction=state.peopleSort==='name_desc'?-1:1;
   const byName=personNameCollator.compare(a.name||a.alias||'',b.name||b.alias||'')*direction;
   if(byName)return byName;
  }
  return (Number(b.photo_count)||0)-(Number(a.photo_count)||0)||(Number(a.id)||0)-(Number(b.id)||0);
 }
 function syncPeopleSortButtons(){
  const photos=$('#people-sort-photos'),name=$('#people-sort-name');if(!photos||!name)return;
  const byPhotos=state.peopleSort==='photos';photos.classList.toggle('is-active',byPhotos);photos.setAttribute('aria-pressed',String(byPhotos));
  name.classList.toggle('is-active',!byPhotos);name.setAttribute('aria-pressed',String(!byPhotos));
  name.textContent=state.peopleSort==='name_desc'?'姓名 Z→A':'姓名 A→Z';
 }
 function syncPeoplePendingButton(){
  const button=$('#people-jump-pending');if(!button)return;
  button.textContent=state.peoplePendingOnly?'全部人物':'待命名';
  button.setAttribute('aria-pressed',String(Boolean(state.peoplePendingOnly)));
 }
 function placePersonCardInOrder(id,keepScroll=scrollY){
  id=Number(id);const stream=state.peopleStream&&state.peopleStream.people;const grid=$('#people-grid');
  if(!stream||!stream.items||!grid)return;
  const oldIndex=stream.items.findIndex(person=>Number(person.id)===id);const card=personCardById(id);
  if(oldIndex<0||!card)return;
  const person=stream.items.splice(oldIndex,1)[0];
  let newIndex=stream.items.findIndex(other=>comparePeopleOrder(person,other)<0);
  if(newIndex<0)newIndex=stream.items.length;
  stream.items.splice(newIndex,0,person);state.people=stream.items;
  const next=stream.items[newIndex+1];const divider=grid.querySelector('.people-section-divider');
  let before=next?personCardById(next.id):null;
  if(isNamedPerson(person)&&(!next||!isNamedPerson(next))&&divider)before=divider;
  grid.insertBefore(card,before);
  const firstUnnamed=stream.items.find(other=>!isNamedPerson(other));
  const firstUnnamedCard=firstUnnamed?personCardById(firstUnnamed.id):null;
  if(divider&&firstUnnamedCard)grid.insertBefore(divider,firstUnnamedCard);
  else if(divider&&!firstUnnamed&&!stream.more)divider.remove();
  scrollTo({top:keepScroll,behavior:'instant'});
  requestAnimationFrame(()=>scrollTo({top:keepScroll,behavior:'instant'}));
 }
 function bumpPersonCard(id){const card=personCardById(id);if(card)card.classList.add('named');}
 function removePersonFromLocalState(id,kind='people',preserveTotal=false){
  kind=personStreamKind(kind);id=Number(id);const stream=state.peopleStream&&state.peopleStream[kind];const card=personCardById(id,kind);
  if(card){card.classList.add('removing');card.remove();}
  if(!stream||!stream.items)return Boolean(card);
  const before=stream.items.length;stream.items=stream.items.filter(p=>Number(p.id)!==id);
  if(stream.items.length!==before){
   if(preserveTotal&&kind==='people'&&state.peoplePendingOnly)stream.baseOffset=(stream.baseOffset||0)+1;
   else if(!preserveTotal&&typeof stream.total==='number'&&stream.total>0)stream.total-=1;
   stream.offset=(stream.baseOffset||0)+stream.items.length;
   state[kind]=stream.items;
  }
  const total=$(personTotalSelector(kind));if(total)total.textContent=kind==='people'?(fmt(stream.total)+' 个分组'):(fmt(stream.total)+' 组路人');
  queueNamedPeopleCountRefresh();
  return Boolean(card)||stream.items.length!==before;
 }
 function appendPersonToLocalState(person,kind='people'){
  if(!person||person.id==null)return null;
  kind=personStreamKind(kind);
  const stream=state.peopleStream&&state.peopleStream[kind];
  if(!stream)return null;
  stream.items=stream.items||[];
  if(stream.items.some(p=>Number(p.id)===Number(person.id)))return person;
  stream.items.push(person);
  if(typeof stream.total==='number')stream.total+=1;
  stream.offset=(stream.baseOffset||0)+stream.items.length;
  state[kind]=stream.items;
  const grid=$(personGridSelector(kind));
  if(grid){
    const empty=grid.querySelector('.no-results');
    if(empty)empty.remove();
    grid.insertAdjacentHTML('beforeend', personCard(person,kind));
  }
  const total=$(personTotalSelector(kind));if(total)total.textContent=kind==='people'?(fmt(stream.total)+' 个分组'):(fmt(stream.total)+' 组路人');
  if(kind==='people')updatePeopleMerge();
  return person;
 }
 async function fetchPersonById(id,kind='people'){
  kind=personStreamKind(kind);
  const data=await api(peopleQuery({ignored:kind==='passersby'?1:0,limit:1,ids:String(id)}));
  return (data.items||[])[0]||null;
 }
 async function refreshPersonInLocalState(id,kind='people'){
  kind=personStreamKind(kind);
  const person=await fetchPersonById(id,kind);
  const stream=state.peopleStream&&state.peopleStream[kind];
  if(!stream||!stream.items)return person;
  if(!person){removePersonFromLocalState(id,kind);return null;}
  const index=stream.items.findIndex(item=>Number(item.id)===Number(id));
  if(index<0)return person;
  stream.items[index]=person;
  state[kind]=stream.items;
  renderPersonCard(person,kind);
  return person;
 }
 function normalizePersonText(value){return String(value??'').trim().replace(/ {2,}/g,' ');}
 function personNameMatchesUrl(name,alias,id){return '/api/people/matches?'+new URLSearchParams({name,alias,exclude_id:String(Number(id)||0)});}
 function ensurePersonNameConflictDialog(){
  let dialog=$('#person-name-conflict-dialog');if(dialog)return dialog;
  dialog=document.createElement('dialog');dialog.id='person-name-conflict-dialog';dialog.className='compact-dialog';dialog.setAttribute('aria-label','确认人物重名');
  dialog.innerHTML='<div class="eyebrow">人物重名提醒</div><h2>可能是同一个人</h2><div class="person-name-conflict-card"><img id="person-conflict-cover" alt="已有的人物头像"><div><strong id="person-conflict-name"></strong><p id="person-conflict-count" class="muted"></p></div></div><p id="person-conflict-copy" class="muted"></p><div class="quick-name-actions"><button type="button" class="text-button" id="person-conflict-cancel">取消</button><button type="button" class="secondary" id="person-conflict-separate">仍保存为不同人物</button><button type="button" class="primary" id="person-conflict-merge">合并到这个人物</button></div>';
  document.body.appendChild(dialog);return dialog;
 }
 function confirmPersonNameConflict(match){
  const dialog=ensurePersonNameConflictDialog();const cover=$('#person-conflict-cover');const label=personLabel(match);const count=fmt(match.photo_count||0);
  $('#person-conflict-name').textContent=label;$('#person-conflict-count').textContent=count+' 张照片';$('#person-conflict-copy').textContent=`已有“${label}”，${count} 张照片。这可能是同一个人。`;
  if(cover){cover.hidden=!match.cover;cover.src=match.cover?'/api/face/'+match.cover:'';}
  return new Promise(resolve=>{
   let settled=false;const finish=value=>{if(settled)return;settled=true;dialog.close();resolve(value);};
   $('#person-conflict-merge').onclick=()=>finish('merge');$('#person-conflict-separate').onclick=()=>finish('separate');$('#person-conflict-cancel').onclick=()=>finish('cancel');
   dialog.oncancel=e=>{e.preventDefault();finish('cancel');};dialog.onclose=()=>finish('cancel');showDialog('#person-name-conflict-dialog');
  });
 }
 async function mergePersonFromNaming(source,target,match){
  await api('/api/people/'+source+'/merge',{method:'POST',body:JSON.stringify({target_id:target})});
  try{
   state.peopleSelected.delete(Number(source));removePersonFromLocalState(source);
   await refreshPersonInLocalState(target);
   if(state.detail&&Array.isArray(state.detail.faces))state.detail.faces.forEach(face=>{if(Number(face.person_id)===Number(source)){face.person_id=Number(target);face.name=match.name;face.alias=match.alias||'';}});
   if(Number(state.personId)===Number(source)&&$('#person-dialog')?.open)await globalThis['openPerson'](Number(target));else applyNamedPersonToOpenPhoto(Number(target),match.name,match.alias||'');
   updatePeopleMerge();
  }catch(e){showNotice('人物已合并，界面刷新未完成，请刷新页面',true);}
  return {merged:true,target:Number(target)};
 }
function applyNamedPersonToOpenPhoto(id,name,alias){
 id=Number(id);
 if(!state.detail||!Array.isArray(state.detail.faces))return;
 let changed=false;
 state.detail.faces.forEach(face=>{
  if(Number(face.person_id)!==id)return;
  face.name=name;
  face.alias=alias||'';
  face.ignored=0;
  changed=true;
 });
 if(changed && typeof renderFaceNames==='function') renderFaceNames(state.detail);
 if(changed)renderOpenPhotoPeople();
}
function applyIgnoredPersonToOpenPhoto(id){
 id=Number(id);
 [...(state.people||[]),...(state.passersby||[])].forEach(person=>{if(Number(person.id)===id)person.ignored=1;});
 if(!state.detail||!Array.isArray(state.detail.faces))return;
 let changed=false;
 state.detail.faces.forEach(face=>{
  if(Number(face.person_id)!==id)return;
  face.ignored=1;
  changed=true;
 });
 if(changed && typeof renderFaceNames==='function')renderFaceNames(state.detail);
 if(changed)renderOpenPhotoPeople();
 if(state.personDetail&&Number(state.personDetail.id)===id)state.personDetail.ignored=1;
}
async function rememberPersonName(id,name,alias){
 const peopleScroll=scrollY;
 name=normalizePersonText(name);
 alias=normalizePersonText(alias);
 const matches=await api(personNameMatchesUrl(name,alias,id));
 const exact=(matches.exact||[])[0];
 if(exact){
  const choice=await confirmPersonNameConflict(exact);
  if(choice==='cancel')return {cancelled:true};
  if(choice==='merge')return mergePersonFromNaming(Number(id),Number(exact.id),exact);
 }else if((matches.same_name||[]).length){
  const same=(matches.same_name||[])[0];
  showNotice(`已有同名人物：${personLabel(same)} · ${fmt(same.photo_count||0)} 张照片`);
 }
 await api('/api/people/'+id,{method:'PATCH',body:JSON.stringify({name:name,alias:alias})});
 let item=patchPersonInStream(id,{name:name,alias:alias,confirmed:1,ignored:0});
 if(!item){
  const data=await api(peopleQuery({limit:1,ids:String(id)}));
  item=(data.items||[])[0];
  if(item&&state.peopleStream&&state.peopleStream.people&&state.peopleStream.people.items){
   const idx=state.peopleStream.people.items.findIndex(p=>p.id===item.id);
   if(idx>=0)state.peopleStream.people.items[idx]=item;
  }
 }
 if(item){
  if(state.peoplePendingOnly)removePersonFromLocalState(id,'people',true);
  else {renderPersonCard(item);placePersonCardInOrder(id,peopleScroll);}
 }
 bumpPersonCard(id);
 applyNamedPersonToOpenPhoto(id,name,alias);
 if(state.personDetail && Number(state.personDetail.id)===Number(id)){
  state.personDetail=Object.assign({},state.personDetail,{name,alias,confirmed:1,ignored:0});
  const title=$('#person-title'); if(title) title.textContent=personLabel(state.personDetail);
  const body=$('.person-body'),top=body?body.scrollTop:0;
  renderPersonFaces(true);
  if(body)body.scrollTop=top;
 }
 queueNamedPeopleCountRefresh();
}
function isNamedPerson(p){return Boolean(p&&p.name&&p.name!=='待核对'&&p.confirmed);}
function personCard(p,mode='people'){
  if(mode==='passersby')return `<article class="person-card" data-person="${p.id}"><button type="button" class="person-open" data-open-person="${p.id}"><img src="/api/face/${p.cover}" alt="路人缩略图" loading="lazy"><b>${esc(p.name||'路人')}</b><p>${p.photo_count} 张照片 · ${p.face_count} 张人脸</p><small>暂不参与人物识别</small></button><button type="button" class="text-button person-restore" data-restore-person="${p.id}">恢复到人物档案</button></article>`;
  const named=isNamedPerson(p);
  const merging=Boolean(state.peopleMerging);
  return `<article class="person-card ${named?'person-named':'person-unnamed'} ${state.peopleSelected.has(p.id)?'selected':''}" data-person="${p.id}">${merging?'<label class="person-check"><input type="checkbox" data-select-person="'+p.id+'" '+(state.peopleSelected.has(p.id)?'checked':'')+' aria-label="选择这个人"></label>':''}<button type="button" class="person-open" data-open-person="${p.id}"><img src="/api/face/${p.cover}" alt="人物候选缩略图" loading="lazy"><b>${esc(personLabel(p))}</b><p>${p.photo_count} 张</p></button>${named?'':'<button type="button" class="text-button person-quick-name" data-quick-name="'+p.id+'">命名</button><button type="button" class="text-button person-ignore" data-ignore-person="'+p.id+'">标为路人</button>'}</article>`;
}
function peopleCardsHtml(items,kind='people',includeDivider=true){
  if(kind!=='people')return items.map(p=>personCard(p,kind)).join('');
  let dividerAdded=!includeDivider;
  return items.map(p=>{
    const divider=!dividerAdded&&!isNamedPerson(p)
      ?'<div class="people-section-divider" role="separator"><span>待命名人物</span><small>还需要核对的面孔</small></div>'
      :'';
    if(divider)dividerAdded=true;
    return divider+personCard(p,kind);
  }).join('');
}
async function fetchPeoplePage(kind, reset=false, viewToken=null, startOffset=null){
  const stream=state.peopleStream[kind];
  if((stream.loading&&!reset)||(!reset&&!stream.more))return;
  if(reset){stream.baseOffset=Number.isInteger(startOffset)?Math.max(0,startOffset):0;stream.offset=stream.baseOffset;stream.items=[];stream.more=true;const grid=$(kind==='people'?'#people-grid':'#passersby-grid');if(grid){grid.classList.add('is-switching');grid.innerHTML='';}}
  stream.loading=true;stream.generation++; const ticket=stream.generation;
  const status=$(kind==='people'?'#people-stream-status':'#passersby-stream-status');
  if(status)status.textContent=reset?'正在加载…':'继续加载…';
  try{
    const data=await api(peopleQuery({ignored:kind==='passersby'?1:0,q:stream.q,offset:stream.offset,limit:48,named:kind==='people'&&Boolean(stream.q),sort:kind==='people'?state.peopleSort:'photos'}));
    if(ticket!==stream.generation||(viewToken&&!isCurrentView(viewToken)))return;
    stream.total=data.total; stream.items=reset?data.items:stream.items.concat(data.items); stream.offset=Number(data.offset||0)+(data.items||[]).length; stream.more=stream.offset<data.total;
    const grid=$(kind==='people'?'#people-grid':'#passersby-grid');
    const total=$(kind==='people'?'#people-total':'#passersby-total');
    if(kind==='people')state.people=stream.items; else state.passersby=stream.items;
    if(total)total.textContent=kind==='people'?'':(fmt(stream.total)+' 组路人');
    if(grid){
      grid.classList.remove('is-switching');
      const html=stream.items.length?peopleCardsHtml(stream.items,kind):`<div class="no-results" style="grid-column:1/-1">${kind==='people'?(state.peoplePendingOnly?'目前没有待命名人物。':'还没有人物分组。到“扫描与入库”勾选人脸检测，扫描照片后即可核对和命名。'):'还没有标为路人的面孔。'}</div>`;
      if(reset) grid.innerHTML=html;
      else {
        const existing=new Set([...grid.querySelectorAll('[data-person]')].map(el=>el.dataset.person));
        const added=stream.items.filter(p=>!existing.has(String(p.id)));
        if(added.length)grid.insertAdjacentHTML('beforeend',peopleCardsHtml(added,kind,!grid.querySelector('.people-section-divider')));
      }
    }
    if(status)status.textContent=stream.more?'向下滚动继续加载':(stream.items.length?'已显示全部':'');
    if(kind==='people')updatePeopleMerge();
  }catch(err){
    const failed=$(kind==='people'?'#people-grid':'#passersby-grid');
    if(failed && ticket===stream.generation)failed.classList.remove('is-switching');
    throw err;
  }finally{ if(ticket===stream.generation)stream.loading=false; }
}
async function loadPeopleOptions(){
  const current=state.person?await api(peopleQuery({limit:8,ids:state.person})): {items:[]};
  const named=await api(peopleQuery({limit:40,named:1}));
  const ignored=await api(peopleQuery({ignored:1,limit:1}));
  const options=new Map();
  options.set('','全部人物');
  for(const p of [...current.items,...named.items]) options.set(String(p.id), `${personLabel(p)} (${p.photo_count})`);
  $('#person-filter').innerHTML=[...options.entries()].map(([id,label])=>`<option value="${esc(id)}">${esc(label)}</option>`).join('');
  $('#person-filter').value=state.person;
  const peopleTotal=state.status?.stats?.people??state.peopleStream?.people?.total??0;
  $('#people-count').textContent=fmt(peopleTotal);
  setNamedPeopleCount(named.total);
  const pc=$('#passersby-count'); if(pc)pc.textContent=fmt(ignored.total);
}
async function loadPeople(viewToken=null){state.peoplePendingOnly=false;syncPeoplePendingButton();state.peopleStream.people.q=($('#people-search')?.value||'').trim();syncPeopleSortButtons();await fetchPeoplePage('people',true,viewToken);loadPeopleOptions();}
async function togglePendingPeople(){
 if(state.peoplePendingOnly){
  await loadPeople();
  $('#people-view')?.scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
  return;
 }
 setPeopleMerging(false);
 const search=$('#people-search');if(search)search.value='';
 const stream=state.peopleStream.people;stream.q='';
 const named=await api(peopleQuery({named:1,limit:1,sort:state.peopleSort}));
 state.peoplePendingOnly=true;syncPeoplePendingButton();
 await fetchPeoplePage('people',true,null,named.total);
 const divider=$('#people-grid .people-section-divider');
 if(!divider){toast('目前没有待命名人物');return;}
 divider.scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
 const first=divider.nextElementSibling?.querySelector?.('.person-open');
 if(first)requestAnimationFrame(()=>first.focus({preventScroll:true}));
}
async function loadPassersby(viewToken=null){state.peopleStream.passersby.q=($('#passersby-search')?.value||'').trim(); await fetchPeoplePage('passersby',true,viewToken); loadPeopleOptions();}
async function fillMergeTargets(query, selectedId){
  const ignored=state.personDetail?.ignored?1:0;
  const data=await api(peopleQuery({ignored,q:query||'',limit:40,ids:selectedId?String(selectedId):''}));
  const items=(data.items||[]).filter(p=>p.id!==state.personId);
  const select=$('#merge-target');
  if(!select)return;
  select.innerHTML='<option value="">选择已有的人物…</option>'+items.map(p=>`<option value="${p.id}" ${Number(selectedId)===p.id?'selected':''}>${esc(personLabel(p))} · ${p.photo_count} 张照片</option>`).join('');
  if(selectedId&&!items.some(p=>p.id===Number(selectedId)))select.value='';
}
async function loadTimeline(viewToken=null){
 const ticket=++state.timelineGeneration; const list=$('#timeline-list'); if(list){list.classList.add('is-switching');} const data=await api('/api/timeline'); if(ticket!==state.timelineGeneration||(viewToken&&!isCurrentView(viewToken)))return;
 const years=data.years||[];
 $('#timeline-total').textContent=years.length?fmt(data.dated)+' 张':'还没有可按年汇总的照片';
 $('#timeline-list').classList.remove('is-switching'); $('#timeline-list').innerHTML=years.length?years.map(y=>{
  const label=y.year==='unknown'?'时间未记录':y.year+' 年';
  return '<button class="facet-card" data-year="'+esc(y.year)+'"><b>'+esc(label)+'</b><span>'+fmt(y.count)+' 张</span></button>';
 }).join(''):'<div class="no-results">还没有可按年份汇总的照片。</div>';
}
function groupLabel(g){return g.key==='10plus'||g.people>=10?'10人及以上':g.people+'人合影';}
async function loadObjects(){const data=await api('/api/objects');const job=await api('/api/objects/job').catch(()=>({status:'idle'}));const count=$('#objects-count');if(count)count.textContent=fmt(data.tagged||0);$('#objects-total').textContent=fmt(data.tagged||0)+' tagged / '+fmt(data.pending||0)+' pending';$('#objects-job').textContent=job.status==='running'?('running '+fmt(job.processed||0)+' / '+fmt(job.limit||0)):(job.message||'idle');$('#objects-list').innerHTML=(data.items||[]).map(x=>'<button class="facet-card" data-object="'+esc(x.label)+'"><b>'+esc(x.label)+'</b><span>'+fmt(x.n)+'</span></button>').join('')||'<div class="no-results">no object tags yet</div>';}
async function loadGroups(viewToken=null){ const ticket=++state.groupsGeneration; const glist=$("#groups-list"); if(glist) glist.classList.add("is-switching");
 const data=await api('/api/groups'); if(ticket!==state.groupsGeneration||(viewToken&&!isCurrentView(viewToken)))return;
 const items=data.items||[];
 const count=$('#groups-count'); if(count) count.textContent=fmt(data.photos||0);
 const ready=items.some(g=>g.count);
 $('#groups-total').textContent=ready?fmt(data.photos)+' 张合影':'暂无合影';
 $('#groups-list').classList.remove('is-switching'); $('#groups-list').innerHTML=items.map(g=>{
  const empty=g.count? '':' empty';
  const coverKey=g.key==='10plus'?'10':g.key;
  const label=groupLabel(g);
  const cover='<img src="/assets/group-covers/'+encodeURIComponent(coverKey)+'.png" alt="" loading="lazy" decoding="async">';
  const arrow='<span class="group-arrow" aria-hidden="true"><svg viewBox="0 0 24 24" focusable="false"><path d="M5 12h13M13 6l6 6-6 6"/></svg></span>';
  return '<button class="group-card'+empty+'" data-group="'+esc(g.key)+'" '+(g.count?'':'disabled')+'><span class="group-cover">'+cover+'</span><span class="group-copy"><span class="group-copy-text"><b>'+esc(label)+'</b><em>'+(g.count?fmt(g.count)+' 张':'还没有')+'</em></span>'+arrow+'</span></button>';
 }).join('')||'<div class="no-results">还没有识别出两人及以上的合影。单人照和没认出脸的照片不进入这里。</div>';
}
const BEIJING_VIEW={lat:39.9042,lng:116.4074,zoom:11};
const WORLD_VIEW={lat:30,lng:20,zoom:2};
const PHOTO_PLACE_FACTOR=.15;
const PHOTO_PLACE_RADIUS_DEFAULT=100;
function hasPhotoCoordinates(photo){return Boolean(photo&&photo.latitude!==null&&photo.latitude!==''&&photo.longitude!==null&&photo.longitude!==''&&Number.isFinite(Number(photo.latitude))&&Number.isFinite(Number(photo.longitude)));}
const PLACE_BASEMAPS={
  gaode:{url:'https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}',options:{subdomains:'1234',maxZoom:18,attribution:'高德地图'}},
  osm:{url:'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',options:{maxZoom:19,attribution:'&copy; OpenStreetMap'}}
};
function setPlaceBasemap(name){
  const spec=PLACE_BASEMAPS[name]||PLACE_BASEMAPS.gaode;
  if(!state.placeMap)return;
  if(state.placeTiles)state.placeMap.removeLayer(state.placeTiles);
  state.placeTiles=L.tileLayer(spec.url,spec.options).addTo(state.placeMap);
  try{localStorage.setItem('shiguang-basemap', name);}catch(e){}
}
function placeZoomFactor(zoom){
  const z=Number(zoom);
  if(!Number.isFinite(z))return 1;
  return Math.pow(2, BEIJING_VIEW.zoom-z);
}
function placeZoomFromFactor(factor){
  const n=Number(factor);
  if(!Number.isFinite(n)||n<=0)return BEIJING_VIEW.zoom;
  return Math.max(2, Math.min(19, BEIJING_VIEW.zoom-Math.log2(n)));
}
function updatePlaceZoomInput(){
  const input=$('#places-zoom'); if(!input||!state.placeMap||document.activeElement===input)return;
  const factor=placeZoomFactor(state.placeMap.getZoom());
  input.value=factor>=10?factor.toFixed(0):(factor>=2?factor.toFixed(1):factor.toFixed(2));
}
function applyPlaceZoomInput(){
  const map=ensurePlaceMap();
  const factor=Number($('#places-zoom').value);
  map.setZoom(placeZoomFromFactor(factor));
  updatePlaceZoomInput();
}
function nudgePlaceZoom(delta){
  const map=ensurePlaceMap();
  map.setZoom(map.getZoom()+delta);
  updatePlaceZoomInput();
}
function ensurePlaceMap(){
  if(state.placeMap)return state.placeMap;
  if(typeof L==='undefined'){toast('地图组件还没准备好，照片仍可浏览',true);return null;}
  const map=L.map('places-map',{zoomControl:true,attributionControl:true,zoomSnap:.1}).setView([BEIJING_VIEW.lat,BEIJING_VIEW.lng],BEIJING_VIEW.zoom);
  state.placeMap=map;
  let preferred='gaode'; try{preferred=localStorage.getItem('shiguang-basemap')||'gaode'; if(preferred==='carto'||preferred==='paper')preferred='gaode';}catch(e){}
  const select=$('#places-basemap'); if(select)select.value=PLACE_BASEMAPS[preferred]?preferred:'gaode';
  setPlaceBasemap(select&&select.value||'gaode');
  const cluster=L.markerClusterGroup({showCoverageOnHover:false,maxClusterRadius:48,disableClusteringAtZoom:16});
  map.addLayer(cluster);
  state.placeCluster=cluster; state.placeMapTimer=null;
  map.on('moveend zoomend',()=>{updatePlaceZoomInput();clearTimeout(state.placeMapTimer);state.placeMapTimer=setTimeout(()=>{if(state.view==='places')loadPlaceMap();},180);}); updatePlaceZoomInput();
  return map;
}
function syncPlaceModeChrome(photo=null){
  const single=Boolean(photo);
  const heading=$('#places-heading'),help=$('#places-help'),back=$('#places-photo-back'),edit=$('#places-photo-edit'),actions=$('.place-map-actions'),list=$('#places-list'),editor=$('#photo-place-editor'),status=$('#places-stream-status');
  if(heading)heading.textContent=single?(prettyPlace(photo.effective_place)||'照片定位'):'按地点看';
  if(help){help.textContent='';help.hidden=true;}
  if(back)back.hidden=!single;
  if(edit)edit.hidden=!single;
  if(actions)actions.hidden=single;
  if(status)status.hidden=single;
  if(!single&&editor)editor.hidden=true;
  if(list){list.hidden=true;if(single)list.replaceChildren();}
}
async function loadPlaceMap(){
  const map=ensurePlaceMap();
  map.invalidateSize();
  const b=map.getBounds();
  const params=new URLSearchParams({west:b.getWest(),south:b.getSouth(),east:b.getEast(),north:b.getNorth(),zoom:String(map.getZoom())});
  const data=await api('/api/places?'+params);
  $('#places-total').textContent=(data.clusters&&data.clusters.length?fmt(data.clusters.reduce(function(n,x){return n+(x.count||0);},0))+' 张在当前视野':'当前视野还没有坐标点');
  state.placeCluster.clearLayers();
  for(const item of data.clusters||[]){
    if(item.latitude==null||item.longitude==null)continue;
    const marker=L.marker([item.latitude,item.longitude]);
    marker.bindPopup('<button type="button" class="place-popup" data-place="'+esc(item.place||'')+'"><span class="place-popup-kicker">拍摄地点</span><strong>'+esc(item.place||'未命名地点')+'</strong><em>'+fmt(item.count)+' 张照片</em><span class="place-popup-go">查看这些照片</span></button>', {className:'place-popup-wrap', closeButton:true, maxWidth:280});
    state.placeCluster.addLayer(marker);
  }
  const status=$('#places-stream-status'); if(status)status.textContent=data.clusters&&data.clusters.length?'点击圆点查看该处照片':'这一层视野里还没有坐标点，可缩小地图或回到北京';
}
function photoPlaceMarker(photo){
  const label=prettyPlace(photo.effective_place)||'这张照片的拍摄位置';
  const html=`<button type="button" class="photo-place-marker" data-map-photo="${Number(photo.id)}" aria-label="返回查看这张照片"><span class="photo-place-pin" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 22s7-7.1 7-13A7 7 0 1 0 5 9c0 5.9 7 13 7 13Z"/><circle cx="12" cy="9" r="2.4"/></svg></span><span class="photo-place-thumb"><img src="/api/thumb/${Number(photo.id)}" alt="${esc(label)}" decoding="async"></span></button>`;
  const icon=L.divIcon({className:'photo-place-marker-shell',html,iconSize:[126,70],iconAnchor:[18,59]});
  return L.marker([Number(photo.latitude),Number(photo.longitude)],{icon,zIndexOffset:1000,title:'点击照片返回大图'});
}
function clearPhotoPlaceRangeLayers(){
  const map=state.placeMap,layers=state.photoMap&&state.photoMap.rangeLayers||[];
  if(map)for(const layer of layers)if(map.hasLayer(layer))map.removeLayer(layer);
  if(state.photoMap)state.photoMap.rangeLayers=[];
}
function nearbySampleMarkup(item){
  const distance=Math.round(Number(item.distance_m)||0);
  return `<button type="button" class="photo-place-sample" data-nearby-photo="${Number(item.id)}" data-distance="${distance} 米" aria-label="打开这张照片，距基准照片 ${distance} 米"><img src="/api/thumb/${Number(item.id)}" alt="" decoding="async"></button>`;
}
function renderPhotoPlaceSamples(data,firstId=0){
  const box=$('#photo-place-samples');if(!box)return;
  const points=data&&data.points||[],picked=[];
  if(firstId){const first=points.find(item=>Number(item.id)===Number(firstId));if(first)picked.push(first);}
  for(const item of (data&&data.samples||points))if(!picked.some(hit=>Number(hit.id)===Number(item.id))&&picked.length<9)picked.push(item);
  box.innerHTML=picked.map(nearbySampleMarkup).join('');
}
function renderPhotoPlaceRange(data,{fit=true}={}){
  const saved=state.photoMap,map=ensurePlaceMap();if(!saved||!map)return;
  clearPhotoPlaceRangeLayers();state.placeCluster.clearLayers();
  const center=[Number(data.anchor.latitude),Number(data.anchor.longitude)];
  const circle=L.circle(center,{radius:Number(data.radius_m),className:'photo-place-range-circle',color:'#396c50',weight:1.4,opacity:.8,fillColor:'#6d9a7f',fillOpacity:.12,interactive:false}).addTo(map);
  saved.rangeLayers.push(circle);
  for(const item of data.points||[]){
    const anchor=Number(item.id)===Number(saved.id);
    const icon=L.divIcon({className:'photo-place-map-dot'+(anchor?' is-anchor':''),html:'<div></div>',iconSize:[13,13],iconAnchor:[6,6]});
    const marker=L.marker([Number(item.latitude),Number(item.longitude)],{icon,title:(anchor?'定位基准 · ':'')+Math.round(Number(item.distance_m)||0)+' 米'}).addTo(map);
    marker.on('click',()=>{const editor=$('#photo-place-editor');if(editor)editor.hidden=false;renderPhotoPlaceSamples(saved.nearby,Number(item.id));const sample=$(`[data-nearby-photo="${Number(item.id)}"]`);if(sample)sample.focus({preventScroll:true});});
    saved.rangeLayers.push(marker);
  }
  if(fit){
    const compact=innerWidth<=620;
    map.fitBounds(circle.getBounds(),{animate:false,maxZoom:19,paddingTopLeft:compact?[32,32]:[380,55],paddingBottomRight:[45,45]});
  }
  updatePlaceZoomInput();
}
async function refreshPhotoPlaceRadiusPreview({fit=true}={}){
  const saved=state.photoMap;if(!saved||!saved.id)return;
  const radius=Number($('#photo-place-radius').value),summary=$('#photo-place-range-summary'),save=$('#photo-place-save');
  if(!Number.isInteger(radius)||radius<1||radius>500){summary.textContent='请输入 1 到 500 米';save.disabled=true;return;}
  const ticket=(saved.previewTicket||0)+1;saved.previewTicket=ticket;saved.radiusM=radius;
  summary.textContent='正在查看范围…';save.disabled=true;
  const data=await api(`/api/photos/${Number(saved.id)}/nearby?radius_m=${radius}`);
  if(state.photoMap!==saved||saved.previewTicket!==ticket)return;
  saved.nearby=data;
  summary.textContent=`${radius} 米内共 ${fmt(data.total)} 张照片${data.total>9?'，这里先看 9 张':''}`;
  save.textContent=`确认修改 ${fmt(data.total)} 张`;
  save.disabled=!data.total||!$('#photo-place-name').value.trim();
  renderPhotoPlaceSamples(data);
  renderPhotoPlaceRange(data,{fit});
}
async function openPhotoPlaceEditor(){
  const saved=state.photoMap,photo=saved&&saved.photo;if(!saved||!photo)throw new Error('没有可修改的定位照片');
  const editor=$('#photo-place-editor');editor.hidden=false;saved.editorOpen=true;
  $('#photo-place-source-thumb').src='/api/thumb/'+Number(photo.id);
  $('#photo-place-source-name').textContent=basename(photo.files&&photo.files[0]&&photo.files[0].path)||('照片 '+photo.id);
  $('#photo-place-name').value=prettyPlace(photo.effective_place)||'';
  $('#photo-place-radius').value=String(saved.radiusM||PHOTO_PLACE_RADIUS_DEFAULT);
  await refreshPhotoPlaceRadiusPreview({fit:true});
  $('#photo-place-name').focus({preventScroll:true});
}
async function closePhotoPlaceEditor(){
  const editor=$('#photo-place-editor');if(editor)editor.hidden=true;
  const samples=$('#photo-place-samples');if(samples)samples.replaceChildren();
  setText('#photo-place-range-summary','');
  if(state.photoMap)state.photoMap.editorOpen=false;
  if(isPhotoPlaceView())await loadPhotoPlace();
}
async function openNearbyPhoto(id){
  const saved=state.photoMap,data=saved&&saved.nearby;if(!saved||!data)return;
  await openPhoto(Number(id),{q:'',filter:'all',person:'',directory:'',sort:'date_desc',date_from:'',date_to:'',place:'',nearby:String(saved.id),radius_m:String(saved.radiusM||PHOTO_PLACE_RADIUS_DEFAULT),max_id:state.maxId});
}
async function savePhotoPlaceRange(){
  const saved=state.photoMap;if(!saved||!saved.id)throw new Error('没有可修改的定位照片');
  const place=$('#photo-place-name').value.trim(),radius=Number($('#photo-place-radius').value),button=$('#photo-place-save');
  if(!place)throw new Error('请填写地点名称');
  if(!Number.isInteger(radius)||radius<1||radius>500)throw new Error('地点范围必须在 1 到 500 米之间');
  button.disabled=true;
  try{
    const result=await api(`/api/photos/${Number(saved.id)}/nearby-place`,{method:'POST',body:JSON.stringify({place,radius_m:radius})});
    saved.photo={...saved.photo,manual_place:result.name,effective_place:result.name};saved.nearby=null;saved.editorOpen=false;
    $('#photo-place-editor').hidden=true;syncPlaceModeChrome(saved.photo);await loadPhotoPlace();
    toast(result.message+'；原图 GPS 未改变');
  }finally{button.disabled=false;}
}
async function loadPhotoPlace(viewToken=null){
  const id=Number(String(state.view).slice('photo-place:'.length));
  let photo=state.photoMap&&Number(state.photoMap.id)===id?state.photoMap.photo:null;
  if(!photo)photo=await api('/api/photos/'+id);
  if(viewToken&&!isCurrentView(viewToken))return;
  if(!hasPhotoCoordinates(photo))throw new Error('这张照片没有定位坐标');
  state.photoMap={...(state.photoMap||{}),id,photo};
  syncPlaceModeChrome(photo);
  const map=ensurePlaceMap();if(!map)return;
  await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
  if(viewToken&&!isCurrentView(viewToken))return;
  map.invalidateSize();map.closePopup();clearPhotoPlaceRangeLayers();state.placeCluster.clearLayers();
  map.setView([Number(photo.latitude),Number(photo.longitude)],placeZoomFromFactor(PHOTO_PLACE_FACTOR),{animate:false});
  state.placeCluster.addLayer(photoPlaceMarker(photo));
  updatePlaceZoomInput();
  setText('#places-total','1 张照片');
}
async function showPhotoPlaceMap(photo,options={}){
  const id=Number(photo&&photo.id);
  if(!id||!hasPhotoCoordinates(photo))throw new Error('这张照片没有定位坐标');
  state.photoMap={id,photo:{...photo},context:options.context?{...options.context}:null,returnView:options.returnView||state.view,absolute:Number(options.absolute),radiusM:PHOTO_PLACE_RADIUS_DEFAULT,nearby:null,rangeLayers:[],editorOpen:false};
  resetPhotoStream();state.view='photo-place:'+id;const viewToken=beginViewChange(state.view);
  state.offset=0;state.selected.clear();state.selecting=false;setPeopleMerging(false);updateBatch();
  $('#exclusion-panel').hidden=true;$('#breadcrumb').textContent='按地点';$('#page-title').textContent='这张照片的位置';$('#groups-back').hidden=true;syncGroupResultCount(state.view,{clear:true});
  $('#library-view').hidden=true;$('#people-view').hidden=true;$('#passersby-view').hidden=true;$('#timeline-view').hidden=true;$('#places-view').hidden=false;$('#groups-view').hidden=true;$('#objects-view').hidden=true;$('#scan-view').hidden=true;$('#folders-view').hidden=true;
  updateChrome();updateTimelineTools();updatePlaceRefine();const panel=viewPanel(state.view);preparePanel(panel);
  await loadPhotoPlace(viewToken);if(isCurrentView(viewToken))revealPanel(panel,viewToken);
}
async function reopenPhotoFromPlaceMap(){
  const saved=state.photoMap;if(!saved||!saved.id)throw new Error('没有可返回的照片');
  const id=Number(saved.id),context=saved.context?{...saved.context}:null;
  const returnView=saved.returnView&&String(saved.returnView).startsWith('photo-place:')?'timeline':(saved.returnView||'timeline');
  clearPhotoPlaceRangeLayers();
  const editor=$('#photo-place-editor');if(editor)editor.hidden=true;
  state.photoMap=null;
  await setView(returnView);
  await openPhoto(id,context);
}
globalThis.showPhotoPlaceMap=showPhotoPlaceMap;
async function loadPlaces(reset=true, viewToken=null){
  syncPlaceModeChrome(null);
  const q=($('#places-search')&&$('#places-search').value||'').trim();
  const list=$('#places-list'),search=$('#places-search'),status=$('#places-stream-status');
  if(reset){state.placeStream={items:[],offset:0,total:0,more:true,loading:false,q:q,unknown:0,dated:0}; state.placeGeneration=(state.placeGeneration||0)+1; if(state.placeAbort)state.placeAbort.abort(); state.placeAbort=new AbortController(); if(list){list.classList.add('is-switching');list.innerHTML=q?'<div class="place-search-empty">正在搜索已记录地点…</div>':'';list.hidden=!q;}if(search)search.setAttribute('aria-expanded',String(Boolean(q)));}
  const stream=state.placeStream; if(stream.loading||(!reset&&!stream.more))return; stream.loading=true;
  if(status)status.hidden=Boolean(q);
  try{
    if(!q){
      if(list){list.hidden=true;list.classList.remove('is-switching');}
      if(search)search.setAttribute('aria-expanded','false');
      await loadPlaceMap();
      return;
    }
    const ticket=state.placeGeneration; const data=await api('/api/places?'+new URLSearchParams({q:stream.q,offset:String(stream.offset),limit:'24'}), {signal:state.placeAbort&&state.placeAbort.signal}); if(ticket!==state.placeGeneration||(viewToken&&!isCurrentView(viewToken)))return;
    stream.total=data.total; stream.unknown=data.unknown||0; stream.dated=data.dated||0;
    stream.items=reset?data.places:stream.items.concat(data.places||[]);
    stream.offset=stream.items.length; stream.more=stream.items.length<data.total;
    const unknownCard=stream.unknown?'<button class="facet-card" data-place="unknown"><b>地点未记录</b><span>'+fmt(stream.unknown)+' 张</span></button>':'';
    list.classList.remove('is-switching');list.hidden=false;list.innerHTML=(stream.items.map(p=>'<button class="facet-card" data-place="'+esc(p.place)+'"><b>'+esc(prettyPlace(p.place))+'</b><span>'+fmt(p.count)+' 张</span></button>').join('')+unknownCard)||'<div class="place-search-empty">没有找到已记录的地点</div>';
    if(stream.more)list.insertAdjacentHTML('beforeend','<div class="place-search-more">继续输入，缩小结果范围</div>');
  }finally{stream.loading=false;}
}
function pickMergeTarget(ids,items){const selected=new Set((ids||[]).map(Number));const pool=(items||[]).filter(p=>selected.has(Number(p.id)));if(!pool.length)return null;const rank=(a,b)=>(b.photo_count-a.photo_count)||(a.id-b.id);const named=pool.filter(p=>p.confirmed&&p.name).slice().sort(rank);const rest=pool.slice().sort(rank);const target=named[0]||rest[0];if(target&&target.ignored&&rest.some(p=>!p.ignored))return rest.find(p=>!p.ignored)||null;return target||null;}
async function resolveMergeTarget(ids){if(!ids.length)throw new Error('请先选择要合并的人物');if(ids.length>100)throw new Error('一次最多选择 100 个人物');const data=await api(peopleQuery({limit:1,ids:ids.join(',')}));const pool=(data.items||[]).filter(p=>ids.includes(p.id));if(!pool.length)throw new Error('没有找到所选人物，请刷新后再试');const target=pickMergeTarget(ids,pool);if(!target)throw new Error('没有可合并的目标');if(!ids.includes(target.id))throw new Error('合并目标必须属于当前选择');return target;}
function faceItemHtml(f,person){
 const canChooseCover=isNamedPerson(person)&&!person.ignored;
 const selected=Number(person.cover_face_id)>0&&Number(person.cover_face_id)===Number(f.id);
 const coverButton=canChooseCover?`<button type="button" class="person-cover-button ${selected?'is-cover':''}" data-set-cover="${f.id}" aria-label="${selected?'当前首页头像':'设为首页头像'}" aria-pressed="${selected?'true':'false'}" title="${selected?'当前首页头像':'设为首页头像'}"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="3.2"/><path d="M5.5 19c.7-4 3-6 6.5-6s5.8 2 6.5 6"/><path d="M4 4h16v16H4z"/></svg></button>`:'';
 return `<div class="face-item" data-face-id="${f.id}"><img src="/api/face/${f.id}" data-face-photo="${f.asset_id}" alt="点击查看原照片" tabindex="0">${coverButton}<button type="button" class="text-button" data-split="${f.id}">这不是同一个人 · 移出</button><button type="button" class="text-button" data-ignore-face="${f.id}">${person.ignored?'只恢复这张':'这张是路人'}</button></div>`;
}
function updatePersonFaceStatus(){const p=state.personDetail;const status=$('#person-faces-status');if(!status||!p)return;const n=(p.faces||[]).length;const total=p.face_count||n;if(state.personFaces&&state.personFaces.loading){status.hidden=false;status.textContent='正在加载人脸';return;}if(p.more){status.hidden=false;status.textContent='已显示 '+fmt(n)+' / '+fmt(total)+' · 继续下滚加载';return;}status.hidden=!n;status.textContent=n?('已显示全部 '+fmt(total)+' 张人脸'):'';}
function renderPersonFaces(reset=false, appended=[]){const p=state.personDetail;if(!p)return;const faces=p.faces||[];const grid=$('#person-faces');if(!grid)return;if(reset)grid.innerHTML=faces.map(f=>faceItemHtml(f,p)).join('');else (appended||[]).forEach(f=>{if(!grid.querySelector('[data-face-id="'+f.id+'"]'))grid.insertAdjacentHTML('beforeend',faceItemHtml(f,p));});updatePersonFaceStatus();}
function maybeLoadMorePersonFaces(){const p=state.personDetail;const body=$('.person-body');if(!p||!p.more||!body)return;if(state.personFaces&&state.personFaces.loading)return;if(body.scrollTop+body.clientHeight>body.scrollHeight-160)loadPersonFaces(false);}
async function removePersonFaceLocally(fid){const p=state.personDetail;if(!p)return;const id=Number(fid);const body=$('.person-body');const top=body?body.scrollTop:0;p.faces=(p.faces||[]).filter(f=>Number(f.id)!==id);if(typeof p.face_count==='number'&&p.face_count>0)p.face_count-=1;const item=$('#person-faces [data-face-id="'+id+'"]');if(!item){if(body)body.scrollTop=top;updatePersonFaceStatus();return;}const height=item.getBoundingClientRect().height;item.style.height=height+'px';item.classList.add('removing');await new Promise(resolve=>setTimeout(resolve,180));if(item.parentNode)item.remove();if(body)body.scrollTop=top;updatePersonFaceStatus();}
function bindPersonFaceScroll(){const body=$('.person-body');if(!body||body.dataset.scrollBound)return;body.dataset.scrollBound='1';body.addEventListener('scroll',()=>maybeLoadMorePersonFaces(),{passive:true});}
async function loadPersonFaces(reset=false){const id=state.personId;if(!id)return;const stream=state.personFaces||(state.personFaces={generation:0,offset:0,loading:false});if(stream.loading&&!reset)return;if(reset){stream.offset=0;}const ticket=++stream.generation;const offset=reset?0:stream.offset;stream.loading=true;try{const p=await api('/api/people/'+id+'?offset='+offset+'&limit=48');if(ticket!==stream.generation||Number(state.personId)!==Number(id))return;const incoming=p.faces||[];const existing=reset?[]:((state.personDetail&&state.personDetail.faces)||[]);const seen=new Set(existing.map(f=>f.id));const appended=incoming.filter(f=>!seen.has(f.id));p.faces=existing.concat(appended);state.personDetail=p;stream.offset=(typeof p.offset==='number'?p.offset+incoming.length:p.faces.length);renderPersonFaces(reset, appended);}finally{if(ticket===stream.generation)stream.loading=false;}}
function updateBatch(){$('#batch-bar').hidden=!state.selecting;$('#selected-count').textContent=`已选 ${state.selected.size} 张`;$('#select-mode').textContent=state.selecting?'退出批量':'批量补录';$('#batch-edit').disabled=!state.selected.size;$('#batch-exclude').disabled=!state.selected.size;$('#batch-restore').disabled=!state.selected.size;$('#batch-exclude').hidden=state.view==='excluded';$('#batch-restore').hidden=state.view!=='excluded';window.__ourTimeHomeSync?.();}
function renderOpenPhotoPeople(){const people=$('#detail-people');if(!people||!state.detail||!Array.isArray(state.detail.faces))return;people.innerHTML=state.detail.faces.map(p=>{const label=p.ignored?'路人':(p.alias?((p.name||'待命名 '+p.person_id)+' / '+p.alias):(p.name||'待命名 '+p.person_id));return `<button type="button" data-person="${p.person_id}"><img src="/api/face/${p.id}" alt="">${esc(label)}</button>`;}).join('');}
async function renderPhoto(id){const ticket=++renderPhoto.ticket;const a=await api('/api/photos/'+id);if(ticket!==renderPhoto.ticket)return false;state.detail=a;$('#exclude-photo').hidden=!a.in_library;$('#restore-photo').hidden=a.in_library||!a.excluded;$('#exclusion-detail-note').textContent=a.in_library?'':a.excluded?'排除原因：'+a.exclude_reason:'此图片受目录排除规则影响，请到“已排除”恢复对应目录。';$('#detail-name').textContent=basename(a.files[0]?.path)||'照片 '+id;$('#original-link').href='/api/original/'+id;$('#detail-facts').innerHTML=`<div class="fact"><span>当前时间</span><p>${esc(a.effective_date||'未知')}<small>${esc(dateSource(a.effective_source)||'无来源')} · ${esc(a.effective_precision||'未知精度')}</small></p></div><div class="fact"><span>原始时间</span><p>${esc(a.captured_at||'未读取到')}<small>${esc(dateSource(a.date_source))}</small></p></div><div class="fact"><span>地点</span><p>${esc(prettyPlace(a.effective_place)||'待补充')}<small>${esc(a.manual_place?'人工补录':a.place_source||'未记录地点')}</small></p></div>${a.latitude!==null?`<div class="fact"><span>定位坐标</span><p>${a.latitude.toFixed(6)}, ${a.longitude.toFixed(6)}</p></div>`:''}<div class="fact"><span>照片信息</span><p>${esc(a.format)} · ${a.width||'?'} × ${a.height||'?'}<small>${esc(a.camera||'未记录设备')} · ${esc(a.category)}</small></p></div>${a.error||a.face_error?`<div class="fact"><span>读取问题</span><p class="tag error">${esc(readableError(a.error||a.face_error))}</p></div><details><summary>原始诊断（技术信息）</summary><pre>${esc(a.error||a.face_error)}</pre></details>`:''}`;renderOpenPhotoPeople();$('#edit-date').value=a.manual_date||'';$('#edit-precision').value=a.manual_precision||'年';$('#edit-place').value=a.manual_place||'';$('#edit-notes').value=a.notes||'';$('#raw-metadata').textContent=JSON.stringify(a.metadata,null,2);$('#file-paths').innerHTML=a.files.map(f=>`<div class="file-row">${esc(f.path)}<br>${fmt(f.size)} 字节 · 修改于 ${esc(f.modified_at)} · ${f.exists_now?'上次扫描存在':'原文件缺失'}${f.excluded?' · 所在目录已排除':''}</div>`).join('');$('#edit-history').innerHTML=a.history.map(e=>`<div class="file-row">${esc(e.created_at)}<br>${esc(e.after_json)}</div>`).join('');showDialog('#detail-dialog');return true;}
function setPersonSaveState(message='',stateName='saved'){const status=$('#person-save-state');if(!status)return;status.textContent=message;status.hidden=!message;if(message)status.dataset.state=stateName;else delete status.dataset.state;}
async function openPerson(id){state.personId=id;hideNotice();setPersonSaveState();state.personFaces={generation:0,offset:0,loading:false};const p=await api('/api/people/'+id+'?limit=48');state.personDetail=p;state.personFaces.offset=(p.faces||[]).length;$('#person-eyebrow').textContent=p.ignored?'路人':'熟悉的面孔';$('#person-title').textContent=personLabel(p);const count=p.face_count||(p.faces||[]).length;$('#person-help').hidden=true;$('#person-title').dataset.count=fmt(count);$('#person-name').value=p.ignored?'':((p.name&&p.name!=='待核对')?p.name:'');$('#person-alias').value=p.alias||'';if(!p.ignored&&p.confirmed&&p.name&&p.name!=='待核对')setPersonSaveState('已标记为：'+p.name);$('#ignore-person').textContent=p.ignored?'恢复到人物档案':'标为路人';await fillMergeTargets('', p.suggested_person_id);renderPersonFaces(true);showDialog('#person-dialog');bindPersonFaceScroll();maybeLoadMorePersonFaces();}
async function openFolder(path=''){const scope=state.folderTarget==='scan'?'scan':'browse';let data;if(scope==='scan'&&!path){const drives=await api('/api/drives');data={path:'',parent:null,scope,items:(drives.roots||[]).map(root=>({name:root,path:root}))};}else data=await api('/api/folders?scope='+scope+'&path='+encodeURIComponent(path));state.folder=data.path;$('#folder-current').textContent=data.path||'本机磁盘';$('#folder-list').innerHTML=(data.parent!==null?`<button data-folder="${esc(data.parent)}">↑ 返回上一级</button>`:'')+data.items.map(x=>`<button data-folder="${esc(x.path)}">▱ ${esc(x.name)}</button>`).join('');$('#use-folder').disabled=!data.path;showDialog('#folder-dialog');}
document.addEventListener('click',action(async e=>{
 if(e.target.closest('[data-folder-toggle]'))return;
 const add=e.target.closest('[data-folder-add]');
 if(add){e.preventDefault();e.stopPropagation();await openAddPhotos(add.dataset.folderAdd||'I:\\');return;}
 const open=e.target.closest('[data-folder-open]');
 if(open){e.preventDefault();await loadFolderBrowser(open.dataset.folderOpen);return;}
}));
$('#folders-up')&&$('#folders-up').addEventListener('click',action(async()=>{const slash=String.fromCharCode(92); const parent=(state.folderNav&&state.folderNav.parent)||''; await loadFolderBrowser(parent && folderDrive(parent)===FOLDER_ONLY_DRIVE ? parent : (FOLDER_ONLY_DRIVE+slash));}));
$('#folders-scan-new')&&$('#folders-scan-new').addEventListener('click',action(async()=>{const path=state.folderPath;if(!path)throw new Error('先进入一个盘符或文件夹，再添加这里的照片');await openAddPhotos(path);toast('已选择当前文件夹，可以继续添加其他文件夹');}));
document.addEventListener('change',action(async e=>{const box=e.target.closest('[data-folder-toggle]');if(!box)return;await toggleFolderInclusion(box.dataset.folderToggle, box.checked);}));
$$('[data-view]').forEach(b=>b.addEventListener('click',action(()=>setView(b.dataset.view))));$$('[data-jump]').forEach(b=>b.addEventListener('click',action(()=>setView(b.dataset.jump))));['#add-folder','#empty-add'].forEach(s=>$(s).addEventListener('click',action(()=>openAddPhotos())));$$('[data-close]').forEach(b=>b.addEventListener('click',()=>$('#'+b.dataset.close).close()));

async function findCachedPerson(id){
 id=Number(id);
 return (state.people||[]).find(p=>Number(p.id)===id)
  ||(state.passersby||[]).find(p=>Number(p.id)===id)
  ||((state.peopleStream&&state.peopleStream.people&&state.peopleStream.people.items)||[]).find(p=>Number(p.id)===id)
  ||(state.personDetail&&Number(state.personDetail.id)===id?state.personDetail:null)
  ||null;
}
function personStubFromOpenPhoto(id){
 id=Number(id);
 const face=(state.detail&&state.detail.faces||[]).find(f=>Number(f.person_id)===id);
 if(!face) return null;
 return {id, name:face.name||'', alias:face.alias||'', ignored:face.ignored||0};
}
function displayNameValue(name){return (name&&name!=='待核对'&&name!=='命名')?name:'';}
let quickMergeTargets=[];let quickMergeTimer=null;
function resetQuickMerge(){
  quickMergeTargets=[];clearTimeout(quickMergeTimer);quickMergeTimer=null;
  const panel=$('#quick-merge-panel'),search=$('#quick-merge-search'),select=$('#quick-merge-target');
  if(panel)panel.hidden=false;
  if(search)search.value='';
  if(select)select.replaceChildren(new Option('选择已有的人物…',''));
}
async function loadQuickMergeTargets(){
  const source=Number(state.quickNameId);if(!source)return;
  const query=$('#quick-merge-search')?.value.trim()||'';
  const select=$('#quick-merge-target');if(!select)return;
  select.replaceChildren(new Option('正在读取人物…',''));select.disabled=true;
  try{
    const data=await api(peopleQuery({ignored:0,named:1,q:query,limit:40}));
    if(source!==Number(state.quickNameId))return;
    quickMergeTargets=(data.items||[]).filter(p=>Number(p.id)!==source);
    select.replaceChildren(new Option('选择已有的人物…',''),...quickMergeTargets.map(p=>new Option(`${personLabel(p)} · ${p.photo_count||0} 张`,String(p.id))));
  }finally{if(source===Number(state.quickNameId))select.disabled=false;}
}
async function openQuickName(id){
 id=Number(id);
 if(!id) throw new Error('没有要命名的人');
 let person=await findCachedPerson(id);
 if(!person){
  try{
   const data=await api(peopleQuery({limit:1,ids:String(id)}));
   person=(data.items||[]).find(p=>Number(p.id)===Number(id));
  }catch(e){ person=null; }
 }
 if(!person) person=personStubFromOpenPhoto(id);
 if(!person) throw new Error('人物不存在');
 state.quickNameId=id;
 state.quickNameIgnored=Boolean(person.ignored);
 resetQuickMerge();
 $('#quick-name-title').textContent=state.quickNameIgnored?'给这位路人命名':(person.name?personLabel(person):'给这个人一个名字');
 $('#quick-name-input').value=state.quickNameIgnored?'':displayNameValue(person.name);
 $('#quick-alias-input').value=person.alias||'';
 const ignoreButton=$('#quick-ignore-person');if(ignoreButton)ignoreButton.hidden=state.quickNameIgnored;
 showDialog('#quick-name-dialog');
 loadQuickMergeTargets().catch(err=>toast(err.message||'人物列表读取失败',true));
 requestAnimationFrame(()=>{$('#quick-name-input').focus();$('#quick-name-input').select();});
}
document.addEventListener('click',action(async e=>{const quick=e.target.closest('[data-quick-name]');if(!quick)return;e.preventDefault();e.stopPropagation();await openQuickName(Number(quick.dataset.quickName));}));
$('#quick-name-dialog')&&$('#quick-name-dialog').addEventListener('click',e=>{if(e.target===$('#quick-name-dialog'))$('#quick-name-dialog').close();});
$('#quick-name-dialog')&&$('#quick-name-dialog').addEventListener('close',resetQuickMerge);
$('#quick-merge-search')&&$('#quick-merge-search').addEventListener('input',()=>{clearTimeout(quickMergeTimer);quickMergeTimer=setTimeout(()=>loadQuickMergeTargets().catch(err=>toast(err.message||'人物列表读取失败',true)),250);});
$('#quick-merge-submit')&&$('#quick-merge-submit').addEventListener('click',action(async()=>{const source=Number(state.quickNameId),target=Number($('#quick-merge-target')?.value);const match=quickMergeTargets.find(p=>Number(p.id)===target);if(!source||!target||!match){toast('请先选择要合并到的人物',true);return;}const button=$('#quick-merge-submit');if(button.disabled)return;button.disabled=true;try{await mergePersonFromNaming(source,target,match);$('#quick-name-dialog').close();}catch(err){toast(err.message||'人物合并失败',true);}finally{button.disabled=false;}}));
$('#quick-ignore-person')&&$('#quick-ignore-person').addEventListener('click',action(async()=>{const id=Number(state.quickNameId);if(!id)throw new Error('没有要标为路人的人物');const button=$('#quick-ignore-person');if(button.disabled||state.quickNameIgnored)return;button.disabled=true;try{await api('/api/people/'+id+'/ignore',{method:'POST',body:JSON.stringify({ignored:true})});state.quickNameIgnored=true;state.peopleSelected.delete(id);removePersonFromLocalState(id,'people');updatePeopleMerge();applyIgnoredPersonToOpenPhoto(id);$('#quick-name-dialog').close();toast('已标为路人；照片中只保留淡色加号');loadPeopleOptions().catch(()=>{});}finally{button.disabled=false;}}));
$('#quick-name-form')&&$('#quick-name-form').addEventListener('submit',async e=>{e.preventDefault();const id=state.quickNameId;if(!id){toast('没有要命名的人',true);return;}const submit=e.submitter||$('#quick-name-form button[type="submit"]');if(submit)submit.disabled=true;try{const result=await rememberPersonName(id,$('#quick-name-input').value,$('#quick-alias-input').value);if(result?.cancelled)return;$('#quick-name-dialog').close();if(!result?.merged)toast('人物名字已保存');}catch(err){toast(err.message||'人物名字保存失败',true);}finally{if(submit)submit.disabled=false;}});

$('#person-dialog').addEventListener('click',async e=>{
  if(e.target===$('#person-dialog')){$('#person-dialog').close();return;}
  const ignoreFace=e.target.closest('[data-ignore-face]');
  if(!ignoreFace)return;
  e.preventDefault();e.stopPropagation();
  const p=state.personDetail; if(!p)return;
  const ignored=!(p.ignored);
  const fid=ignoreFace.dataset.ignoreFace;
  const sourceId=Number(p.id);
  const sourceKind=p.ignored?'passersby':'people';
  ignoreFace.disabled=true;
  try{
    await api('/api/faces/'+fid+'/ignore',{method:'POST',body:JSON.stringify({ignored})});
    await removePersonFaceLocally(fid);
    const remaining=await refreshPersonInLocalState(sourceId,sourceKind);
    if(!remaining || !(state.personDetail&&state.personDetail.face_count)) $('#person-dialog').close();
  }catch(err){ toast(err.message||'这张没能标为路人', true); }
  finally{ ignoreFace.disabled=false; }
});
$('#search').addEventListener('input',()=>{clearTimeout(state.searchTimer);state.searchTimer=setTimeout(action(async()=>{state.q=$('#search').value.trim();state.offset=0;await loadPhotos();}),250);});
$('#place-refine-input').addEventListener('input',()=>{clearTimeout(state.placeSuggestTimer);state.placeSuggestTimer=setTimeout(action(()=>loadPlaceSuggestions($('#place-refine-input').value.trim())),200);});
$('#place-refine-save').addEventListener('click',action(()=>savePlaceRefine()));
$('#place-refine-suggest').addEventListener('click',e=>{const b=e.target.closest('[data-place-suggest]');if(!b)return;$('#place-refine-input').value=b.dataset.placeSuggest;});
$('#person-filter').addEventListener('change',action(async()=>{state.person=$('#person-filter').value;state.offset=0;await loadPhotos();}));
const personFilterSearch=$('#person-filter-search'); personFilterSearch&&personFilterSearch.addEventListener('input',()=>{clearTimeout(state.personFilterTimer);state.personFilterTimer=setTimeout(action(async()=>{const data=await api(peopleQuery({q:$('#person-filter-search').value.trim(),limit:40,ids:state.person}));const options=new Map([['','全部人物']]);for(const p of data.items)options.set(String(p.id), personLabel(p)+' ('+p.photo_count+')');$('#person-filter').innerHTML=[...options.entries()].map(([id,label])=>'<option value="'+esc(id)+'">'+esc(label)+'</option>').join('');$('#person-filter').value=state.person;}),250);});
$('#people-search').addEventListener('input',()=>{clearTimeout(state.peopleSearchTimer);state.peopleSearchTimer=setTimeout(action(()=>loadPeople()),250);});
$('#people-sort-photos').addEventListener('click',action(async()=>{if(state.peopleSort==='photos')return;state.peopleSort='photos';syncPeopleSortButtons();await loadPeople();}));
$('#people-sort-name').addEventListener('click',action(async()=>{state.peopleSort=state.peopleSort==='name_asc'?'name_desc':'name_asc';syncPeopleSortButtons();await loadPeople();}));
$('#people-jump-pending').addEventListener('click',action(()=>togglePendingPeople()));
$('#places-search').addEventListener('input',()=>{clearTimeout(state.placesSearchTimer);state.placesSearchTimer=setTimeout(action(()=>loadPlaces(true)),250);});
document.addEventListener('click',action(async e=>{const photo=e.target.closest&&e.target.closest('[data-map-photo]');if(!photo)return;e.preventDefault();e.stopPropagation();await reopenPhotoFromPlaceMap();}));
$('#places-photo-back').addEventListener('click',action(()=>reopenPhotoFromPlaceMap()));
$('#places-photo-edit').addEventListener('click',action(()=>openPhotoPlaceEditor()));
$('#photo-place-editor-close').addEventListener('click',action(()=>closePhotoPlaceEditor()));
$('#photo-place-editor-cancel').addEventListener('click',action(()=>closePhotoPlaceEditor()));
$('#photo-place-radius').addEventListener('input',()=>{clearTimeout(state.photoPlaceRadiusTimer);state.photoPlaceRadiusTimer=setTimeout(action(()=>refreshPhotoPlaceRadiusPreview({fit:true})),180);});
$('#photo-place-name').addEventListener('input',()=>{const save=$('#photo-place-save'),total=state.photoMap&&state.photoMap.nearby&&state.photoMap.nearby.total||0;save.disabled=!total||!$('#photo-place-name').value.trim();});
$('#photo-place-form').addEventListener('submit',action(async e=>{e.preventDefault();await savePhotoPlaceRange();}));
$('#photo-place-samples').addEventListener('click',action(async e=>{const sample=e.target.closest('[data-nearby-photo]');if(!sample)return;await openNearbyPhoto(Number(sample.dataset.nearbyPhoto));}));
$('#places-beijing').addEventListener('click',action(async()=>{ensurePlaceMap().setView([BEIJING_VIEW.lat,BEIJING_VIEW.lng],BEIJING_VIEW.zoom);await loadPlaceMap();}));
$('#places-world').addEventListener('click',action(async()=>{ensurePlaceMap().setView([WORLD_VIEW.lat,WORLD_VIEW.lng],WORLD_VIEW.zoom);await loadPlaceMap();}));
$('#places-unknown').addEventListener('click',action(()=>setView('place:unknown')));
$('#places-basemap').addEventListener('change',()=>setPlaceBasemap($('#places-basemap').value));
$('#places-zoom-out').addEventListener('click',()=>nudgePlaceZoom(-1));
$('#places-zoom-in').addEventListener('click',()=>nudgePlaceZoom(1));
$('#places-zoom-reset').addEventListener('click',action(async()=>{ensurePlaceMap().setView([BEIJING_VIEW.lat,BEIJING_VIEW.lng],BEIJING_VIEW.zoom);updatePlaceZoomInput();await loadPlaceMap();}));
$('#places-zoom').addEventListener('change',()=>applyPlaceZoomInput());
$('#places-zoom').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();applyPlaceZoomInput();}});
$('#passersby-search').addEventListener('input',()=>{clearTimeout(state.passersbySearchTimer);state.passersbySearchTimer=setTimeout(action(()=>loadPassersby()),250);});
$('#merge-search').addEventListener('input',()=>{clearTimeout(state.mergeSearchTimer);state.mergeSearchTimer=setTimeout(action(()=>fillMergeTargets($('#merge-search').value.trim(), Number($('#merge-target').value)||state.personDetail&&state.personDetail.suggested_person_id)),250);});
window.addEventListener('scroll',()=>{if(state.view==='places'){const status=$('#places-stream-status');if(status&&status.getBoundingClientRect().top<window.innerHeight+200)loadPlaces(false);return;}const kind=state.view==='people'?'people':state.view==='passersby'?'passersby':null;if(!kind)return;const status=$(kind==='people'?'#people-stream-status':'#passersby-stream-status');if(!status)return;const rect=status.getBoundingClientRect();if(rect.top<window.innerHeight+200)fetchPeoplePage(kind);},{passive:true});
$('#timeline-tools').addEventListener('click',action(async e=>{const sort=e.target.closest('[data-time-sort]');if(sort){state.sort=sort.dataset.timeSort;$('#sort-order').value=state.sort;if(state.view==='years')await setView('timeline');else await setView(isTimelineView()?state.view:'timeline');return;}if(e.target.id==='timeline-by-year')await setView('years');}));
$('#home-years-link').addEventListener('click',action(()=>setView('years')));
$('#groups-back').addEventListener('click',action(()=>setView('groups')));
$('#timeline-back').addEventListener('click',action(()=>setView('timeline')));
$('#sort-order').addEventListener('change',action(async()=>{state.sort=$('#sort-order').value;updateTimelineTools();await loadPhotos();}));
$('#refresh-photos').addEventListener('click',action(()=>loadPhotos()));
$('#select-mode').addEventListener('click',action(async()=>{state.selecting=!state.selecting;state.selected.clear();streamSelection();}));
$('#select-page').addEventListener('click',action(async()=>{for(const id of streamVisibleIds()){if(state.selected.size>=1000)break;state.selected.add(id);}streamSelection();}));
$('#clear-selection').addEventListener('click',action(async()=>{state.selected.clear();streamSelection();}));
$('#batch-edit').addEventListener('click',()=>{$('#batch-description').textContent=`将补录 ${state.selected.size} 张照片。原始元数据会完整保留。`;$('#batch-form').reset();showDialog('#batch-dialog');});
$('#batch-form').addEventListener('submit',action(async e=>{e.preventDefault();const body={ids:[...state.selected]};if($('#batch-date-enabled').checked){body.manual_date=$('#batch-date').value;body.manual_precision=$('#batch-precision').value;}if($('#batch-place-enabled').checked)body.manual_place=$('#batch-place').value;if($('#batch-notes-enabled').checked)body.notes=$('#batch-notes').value;await api('/api/photos',{method:'PATCH',body:JSON.stringify(body)});$('#batch-dialog').close();toast('补录已保存，原照片未修改');await refreshStatus();await loadPhotos();}));

$('#detail-form').addEventListener('submit',action(async e=>{e.preventDefault();await api('/api/photos',{method:'PATCH',body:JSON.stringify({ids:[state.detail.id],manual_date:$('#edit-date').value,manual_precision:$('#edit-precision').value,manual_place:$('#edit-place').value,notes:$('#edit-notes').value})});viewer.drafts.delete(state.detail.id);toast('补录已保存，原始信息仍然保留');await openPhoto(state.detail.id);await refreshStatus();await loadPhotos();}));
$('#reveal-button').addEventListener('click',action(async()=>{await api('/api/reveal/'+state.detail.id,{method:'POST'});}));
$('#all-drives').addEventListener('click',action(async()=>{const d=await api('/api/drives');d.roots.filter(p=>!/^c:/i.test(p)).forEach(addScanRoot);toast('已添加本机磁盘；点击“开始扫描”后才会运行');}));
$('#choose-folder').addEventListener('click',action(()=>{state.folderTarget='scan';return openFolder();}));$$('#scan-folder-picker,#scan-add-folder').forEach(button=>button?.addEventListener('click',action(()=>{state.folderTarget='scan';return openFolder();})));$('#use-folder').addEventListener('click',()=>{if(state.folderTarget==='home-query'){const path=state.folder||'';window.__ourTimeHomeFolderResolve?.(path);$('#folder-dialog').close();return;}if(state.folderTarget==='browse'){$('#directory-filter').value=state.folder;$('#folder-dialog').close();$('#apply-directory').click();return;}if(state.folderTarget==='exclude'){$('#exclude-root').value=state.folder;$('#folder-dialog').close();return;}addScanRoot(state.folder);$('#folder-dialog').close();});document.addEventListener('click',e=>{const remove=e.target.closest('[data-remove-scan-root]');if(remove){e.preventDefault();removeScanRoot(remove.dataset.removeScanRoot);}});

$('#probe-objects').addEventListener('click',action(async()=>{const status=$('#object-probe-status'); if(status) status.textContent='正在试扫物体标签…'; const data=await api('/api/objects/probe',{method:'POST',body:JSON.stringify({limit:24})}); const ok=(data.items||[]).filter(x=>x.tags&&x.tags.length); if(status) status.textContent='试扫 '+data.count+' 张，约 '+data.per_image+' 秒/张 · '+data.runtime; toast('物体试扫完成：'+ok.length+' 张打上标签');}));

$('#start-objects')&&$('#start-objects').addEventListener('click',action(async()=>{await api('/api/objects/scan',{method:'POST',body:JSON.stringify({limit:200})});toast('object scan started');await loadObjects();}));
$('#start-scan').addEventListener('click',action(async()=>{const roots=[...(state.scanRoots||[])];if(!roots.length)throw new Error('请先选择照片文件夹');if(state.scanSubmitting)return;const previousJobId=state.status?.job?.id;state.scanStartedThisVisit=true;state.scanShowCompletedResult=false;state.scanSubmitting=true;renderScanRoots();try{await api('/api/scan',{method:'POST',body:JSON.stringify({roots,with_faces:true,include_system:false,workers:1})});state.scanRoots=[];toast('开始添加照片，已处理过的会自动跳过');const status=await refreshStatus();if(status.job&&status.job.id!==previousJobId&&['completed','completed_with_errors','failed'].includes(status.job.status)){state.scanShowCompletedResult=true;renderScanStatus(status);} }finally{state.scanSubmitting=false;renderScanRoots();}}));$('#scan-view-all').addEventListener('click',action(()=>setView('timeline')));$('#scan-view-errors')?.addEventListener('click',action(()=>setView('errors')));
['#person-name','#person-alias'].forEach(selector=>$(selector)?.addEventListener('input',()=>setPersonSaveState()));
$('#person-form').addEventListener('submit',async e=>{e.preventDefault();const pid=state.personId;const submit=e.submitter||$('#person-form button[type="submit"]');const name=normalizePersonText($('#person-name').value);if(submit)submit.disabled=true;setPersonSaveState('正在保存…','saving');try{const result=await rememberPersonName(pid,name,$('#person-alias').value);if(result?.cancelled){setPersonSaveState();return;}if(!result?.merged){setPersonSaveState('已标记为：'+name);toast('人物名字已保存');if($('#person-dialog').open){const item=((state.peopleStream.people||{}).items||[]).find(p=>p.id===Number(pid));if(item){$('#person-title').textContent=personLabel(item);state.personDetail=Object.assign({},state.personDetail,item);}}}}catch(err){setPersonSaveState('未保存','error');showNotice(err.message||'人物名字保存失败',true);}finally{if(submit)submit.disabled=false;}});
$('#merge-person').addEventListener('click',async e=>{e.preventDefault();e.stopPropagation();const button=$('#merge-person');const target=Number($('#merge-target').value);if(!target){showNotice('请先在下拉框里选择要合并到的人',true);return;}if(target===Number(state.personId)){showNotice('不能合并到自己',true);return;}const source=Number(state.personId);if(button?.disabled)return;button.disabled=true;try{await api('/api/people/'+source+'/merge',{method:'POST',body:JSON.stringify({target_id:target})});try{state.peopleSelected.delete(source);removePersonFromLocalState(source);await refreshPersonInLocalState(target);await openPerson(target);showNotice('人物分组已合并');}catch(uiError){showNotice('人物已合并，界面刷新未完成，请刷新页面',true);}}catch(err){showNotice(err.message||'人物合并失败',true);}finally{button.disabled=false;}});
$('#show-person-photos').addEventListener('click',action(async()=>{$('#person-dialog').close();const person=state.personDetail||{id:state.personId};rememberPersonLabel(person);state.person=String(state.personId);$('#person-filter').value=state.person;await setView('timeline');}));
$('#ignore-person').addEventListener('click',action(async()=>{const ignored=!(state.personDetail&&state.personDetail.ignored);const id=Number(state.personId);const sourceKind=state.personDetail&&state.personDetail.ignored?'passersby':'people';await api('/api/people/'+id+'/ignore',{method:'POST',body:JSON.stringify({ignored})});toast(ignored?'这组已标为路人，不再参与识别':'已恢复到人物档案');$('#person-dialog').close();removePersonFromLocalState(id,sourceKind);const targetKind=ignored?'passersby':'people';const moved=await fetchPersonById(id,targetKind);if(moved)appendPersonToLocalState(moved,targetKind);state.peopleSelected.delete(id);updatePeopleMerge();}));
$('#people-merge-toggle')?.addEventListener('click',()=>setPeopleMerging(!state.peopleMerging));
$('#clear-people-selection').addEventListener('click',()=>setPeopleMerging(false));
$('#merge-selected-people').addEventListener('click',async()=>{const button=$('#merge-selected-people');if(button?.disabled)return;const ids=[...state.peopleSelected].map(Number);if(ids.length<2){showNotice('请至少选择两组同一人',true);return;}if(ids.length>100){showNotice('一次最多选择 100 个人物',true);return;}button.disabled=true;const done=[];const pending=ids.slice();let backendError=null;let target=null;try{target=await resolveMergeTarget(ids);if(!ids.includes(Number(target.id)))throw new Error('合并目标必须属于当前选择');for(const id of ids.filter(x=>x!==Number(target.id))){await api('/api/people/'+id+'/merge',{method:'POST',body:JSON.stringify({target_id:target.id})});done.push(id);pending.splice(pending.indexOf(id),1);}}catch(err){backendError=err;}if(backendError){const leftover=pending.filter(id=>!done.includes(id));const msg=done.length?('已合并 '+done.length+' 组，未完成：'+leftover.join('、')+'。'+backendError.message):backendError.message;showNotice(msg,true);toast(msg,true);}else if(done.length){toast('已合并到 '+personLabel(target));}if(done.length){try{for(const id of done){state.peopleSelected.delete(id);removePersonFromLocalState(id);}await refreshPersonInLocalState(target.id);setPeopleMerging(false);}catch(uiError){showNotice('人物已合并，界面刷新未完成，请刷新页面',true);}}button.disabled=false;});
$('#backup-button').addEventListener('click',action(async()=>{const data=await api('/api/backup',{method:'POST'});toast('备份已保存：'+data.path);}));
document.addEventListener('click',action(async e=>{
 const groupExcludeAction=e.target.closest('[data-group-exclude-arm],[data-group-exclude-cancel],[data-group-exclude-confirm]');
 if(groupExcludeAction){
  e.preventDefault();e.stopPropagation();
  const card=groupExcludeAction.closest('[data-photo]');
  if(!card)return;
  if(groupExcludeAction.matches('[data-group-exclude-arm]')){
   $$('#photo-grid .group-exclude-confirming').forEach(item=>item.classList.remove('group-exclude-confirming'));
   card.classList.add('group-exclude-confirming');
   card.querySelector('[data-group-exclude-confirm]')?.focus();
   return;
  }
  if(groupExcludeAction.matches('[data-group-exclude-cancel]')){
   card.classList.remove('group-exclude-confirming');
   card.querySelector('[data-group-exclude-arm]')?.focus();
   return;
  }
  const id=Number(card.dataset.photo);if(!id)return;
  groupExcludeAction.disabled=true;
  try{
   const reason=String(state.view||'').startsWith('group:')?'在合影页排除显示':'在全部照片页排除显示';
   await api('/api/exclusions/assets',{method:'POST',body:JSON.stringify({ids:[id],excluded:true,reason,display_only:true})});
   card.classList.add('group-exclude-removing');
   toast('已排除显示，原照片和识别信息均保留');
   await streamRemovePhoto(id,Number(card.dataset.position));
   refreshStatus().catch(()=>{});
  }finally{groupExcludeAction.disabled=false;}
  return;
 }
 const photo=e.target.closest('[data-photo]');
 if(photo){
  const id=Number(photo.dataset.photo);
  if(state.selecting){
   if(state.selected.has(id))state.selected.delete(id);
   else if(state.selected.size<1000)state.selected.add(id);
   else toast('一次最多选择 1000 张照片');
   streamSelection();
  }else {
   const context=currentBrowseContext();
   const position=Number(photo.dataset.position);
   if(Number.isInteger(position)&&position>=0)context.position=position;
   await openPhoto(id,context);
  }
 }
 const ignorePersonCard=e.target.closest('[data-ignore-person]');
  if(ignorePersonCard){
  e.preventDefault();e.stopPropagation();
  const id=Number(ignorePersonCard.dataset.ignorePerson);
  ignorePersonCard.disabled=true;
  try{
   await api('/api/people/'+id+'/ignore',{method:'POST',body:JSON.stringify({ignored:true})});
   state.peopleSelected.delete(id);
   removePersonFromLocalState(id);
   updatePeopleMerge();
  }catch(err){toast(err.message||'没能标为路人',true);}
  finally{ignorePersonCard.disabled=false;}
  return;
 }
 const restorePersonCard=e.target.closest('[data-restore-person]');
 if(restorePersonCard){
  e.preventDefault();e.stopPropagation();
  const id=Number(restorePersonCard.dataset.restorePerson);
  restorePersonCard.disabled=true;
  try{
   await api('/api/people/'+id+'/ignore',{method:'POST',body:JSON.stringify({ignored:false})});
   removePersonFromLocalState(id,'passersby');
   const restored=await fetchPersonById(id,'people');
   if(restored)appendPersonToLocalState(restored,'people');
   toast('已恢复到人物档案');
  }catch(err){toast(err.message||'没能恢复到人物档案',true);}
  finally{restorePersonCard.disabled=false;}
  return;
 }
 const peopleCard=e.target.closest('#people-grid .person-card');
 if(peopleCard){
  if(e.target.closest('[data-quick-name],[data-ignore-person]'))return;
  const hitCheck=e.target.closest('.person-check,[data-select-person]');
  if(hitCheck||isPeopleSelecting()){
   e.preventDefault();
   togglePersonSelection(peopleCard.dataset.person);
   return;
  }
  await openPerson(Number(peopleCard.dataset.person));
  return;
 }
 const openPersonBtn=e.target.closest('[data-open-person]');
 if(openPersonBtn){await openPerson(Number(openPersonBtn.dataset.openPerson));return;}
 const coverButton=e.target.closest('[data-set-cover]');
 if(coverButton){
  e.preventDefault();e.stopPropagation();
  const faceId=Number(coverButton.dataset.setCover),personId=Number(state.personId);
  if(!faceId||!personId)return;
  coverButton.disabled=true;
  try{
   await api('/api/people/'+personId+'/cover',{method:'PUT',body:JSON.stringify({face_id:faceId})});
   if(state.personDetail&&Number(state.personDetail.id)===personId){
    state.personDetail.cover=faceId;
    state.personDetail.cover_face_id=faceId;
   }
   const item=patchPersonInStream(personId,{cover:faceId,cover_face_id:faceId});
   if(item)renderPersonCard(item);
   $$('#person-faces [data-set-cover]').forEach(button=>{
    const selected=Number(button.dataset.setCover)===faceId;
    button.classList.toggle('is-cover',selected);
    button.setAttribute('aria-pressed',String(selected));
    button.setAttribute('aria-label',selected?'当前首页头像':'设为首页头像');
    button.title=selected?'当前首页头像':'设为首页头像';
   });
   toast('首页头像已选定');
  }finally{coverButton.disabled=false;}
  return;
 }
 const person=e.target.closest('[data-person]');
 if(person)await openPerson(Number(person.dataset.person));
 const facePhoto=e.target.closest('[data-face-photo]');
 if(facePhoto){
  const opened=await openPhoto(Number(facePhoto.dataset.facePhoto),{q:'',filter:'all',person:String(state.personId),directory:'',sort:'date_desc'});
  if(opened)$('#person-dialog').close();
  else showNotice('这张照片已不在当前有效范围，人物窗口已为你保留。',true);
 }
 const split=e.target.closest('[data-split]');
 if(split){
  const sourceId=Number(state.personId);
  const result=await api('/api/faces/'+split.dataset.split+'/split',{method:'POST'});
  toast('已移到新的待命名分组');
  await removePersonFaceLocally(split.dataset.split);
  const remaining=await refreshPersonInLocalState(sourceId);
  if(!remaining || !(state.personDetail&&state.personDetail.face_count))$('#person-dialog').close();
  if(result&&result.person_id&&state.view==='people'){
   const created=await fetchPersonById(result.person_id);
   if(created)appendPersonToLocalState(created);
  }
 }
 const year=e.target.closest('[data-year]');
 if(year){state.sort='date_desc';$('#sort-order').value=state.sort;await setView('year:'+year.dataset.year);return;}
 const group=e.target.closest('[data-group]');
 if(group){state.sort='date_desc';$('#sort-order').value=state.sort;await setView('group:'+group.dataset.group);return;}
 const place=e.target.closest('[data-place]');
 if(place){await setView('place:'+place.dataset.place);return;}
 const folder=e.target.closest('[data-folder]');
 if(folder)await openFolder(folder.dataset.folder);
 if(e.target.id==='banner-details')await setView('scan');
 if(e.target.id==='pause-scan'){await api('/api/scan/pause',{method:'POST'});await refreshStatus();}
 if(e.target.id==='resume-scan'){await api('/api/scan/'+state.status.job.id+'/resume',{method:'POST'});await refreshStatus();}
 if(e.target.id==='cancel-scan'){const job=state.status&&state.status.job;if(!job||job.status!=='paused')return;const button=e.target;button.disabled=true;try{await api('/api/scan/'+job.id+'/cancel',{method:'POST'});state.scanShowCompletedResult=false;toast('已取消这次扫描；已经添加的照片仍然保留');await refreshStatus();}finally{if(button.isConnected)button.disabled=false;}}
}));
document.addEventListener('keydown',e=>{if((e.key==='Enter'||e.key===' ')&&e.target.matches('[data-photo], [data-face-photo]')){e.preventDefault();e.target.click();}});


function bytes(n){return n>=1024*1024?(n/1024/1024).toFixed(1)+' MB':Math.round(n/1024)+' KB';}
async function loadExclusionRules(viewToken=null){
 const data=await api('/api/exclusions', viewToken && viewToken.signal ? {signal:viewToken.signal} : {});
 if(viewToken && !isCurrentView(viewToken, 'excluded')) return;
 $('#excluded-rules').innerHTML=data.roots.length?data.roots.map(r=>`<div class="exclusion-rule"><span>${esc(r.path)}<small>含全部子目录 · ${esc(r.created_at)}</small></span><button class="secondary small" data-restore-root="${r.id}">恢复目录</button></div>`).join(''):'<p class="footnote">还没有目录排除规则。下方显示已排除的档案；缩略图已释放。</p>';
}
async function exclusionDialog(ids=null,path=null){
 if(ids){state.exclusion={ids,excluded:true};$('#exclude-impact').textContent=`将排除 ${ids.length} 份图片档案，以及这些图片的全部重复副本。`;}
 else {const impact=await api('/api/exclusions/preview?path='+encodeURIComponent(path));state.exclusion={path:impact.path};$('#exclude-impact').textContent=`目录：${impact.path}\n已登记 ${fmt(impact.files)} 个文件，对应 ${fmt(impact.assets)} 份档案。\n${impact.scope}`;}
 showDialog('#exclude-dialog');
}
async function restoreAssets(ids){
 const result=await api('/api/exclusions/assets',{method:'POST',body:JSON.stringify({ids,excluded:false})});
 toast(result.message);state.thumbRevision++;state.selected.clear();updateBatch();await refreshStatus();await loadPhotos();
 if($('#detail-dialog').open)await openPhoto(state.detail.id);
}
$('#batch-exclude').addEventListener('click',action(()=>exclusionDialog([...state.selected])));
$('#batch-restore').addEventListener('click',action(()=>restoreAssets([...state.selected])));
$('#exclude-photo').addEventListener('click',action(()=>exclusionDialog([state.detail.id])));
$('#restore-photo').addEventListener('click',action(()=>restoreAssets([state.detail.id])));
$('#choose-exclude-folder').addEventListener('click',action(()=>{state.folderTarget='exclude';return openFolder();}));
$('#preview-root-exclusion').addEventListener('click',action(()=>exclusionDialog(null,$('#exclude-root').value)));
$('#exclude-current-directory').addEventListener('click',action(()=>{const path=$('#directory-filter').value.trim();if(!path)throw new Error('请先填写要筛选的目录');return exclusionDialog(null,path);}));
$('#apply-directory').addEventListener('click',action(async()=>{state.directory=$('#directory-filter').value.trim();state.sort=state.directory?'name_asc':'date_desc';$('#sort-order').value=state.sort;state.offset=0;await loadPhotos();}));
$('#clear-directory').addEventListener('click',action(async()=>{state.directory='';state.sort='date_desc';$('#sort-order').value=state.sort;$('#directory-filter').value='';state.offset=0;await loadPhotos();}));
$('#with-faces').addEventListener('change',()=>{$('#scan-workers').disabled=$('#with-faces').checked;$('#worker-hint').textContent=$('#with-faces').checked?'本轮包含人脸识别，使用 1 路处理，保持人物分组顺序稳定。':'元数据工具常驻运行；并发数越高，CPU 与磁盘负载越高。';});
$('#confirm-exclusion').addEventListener('click',action(async()=>{
 const button=$('#confirm-exclusion');button.disabled=true;
 try {const body={...state.exclusion};if(body.ids)body.reason=$('#exclude-reason').value||'手工排除';
  const result=await api(body.ids?'/api/exclusions/assets':'/api/exclusions/roots',{method:'POST',body:JSON.stringify(body)});
  $('#exclude-dialog').close();if($('#detail-dialog').open)$('#detail-dialog').close();
  state.thumbRevision++;let locallyRemoved=false;
  if(body.ids&&body.ids.length){try{locallyRemoved=await streamRemovePhotos(body.ids);}catch(error){locallyRemoved=false;}}
  state.selected.clear();updateBatch();toast('已排除，释放缓存 '+bytes(result.released_bytes)+'；原文件保留');
  if(!locallyRemoved)await loadPhotos();await refreshStatus();await loadExclusionRules();
 } finally {button.disabled=false;}
}));
document.addEventListener('click',action(async e=>{const button=e.target.closest('[data-restore-root]');if(!button)return;const result=await api('/api/exclusions/roots/'+button.dataset.restoreRoot,{method:'DELETE'});state.thumbRevision++;toast(result.message);await loadExclusionRules();await refreshStatus();await loadPhotos();}));

window.__ourTimeApp={state,api,peopleQuery,personLabel,prettyPlace,showDialog,openFolder,exclusionDialog,restoreAssets,refreshStatus,setView,openAddPhotos,addScanRoot,renderScanRoots};

setInterval(()=>{fetch('/',{cache:'no-store'}).then(res=>{if(res.ok)state.connectionNotice=false;else throw new Error('offline');}).catch(()=>{if(!state.connectionNotice){toast('后台暂时繁忙，已加载的照片仍可继续看；完整状态请稍后刷新',true);state.connectionNotice=true;}});refreshStatus().catch(()=>{});},8000);

document.querySelectorAll("dialog").forEach(el=>{if(el.open)el.close();});
renderPhoto.ticket=0;
