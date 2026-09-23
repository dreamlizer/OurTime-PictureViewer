/* Homepage discovery. Does not replace timeline, organize, or viewer contracts. */
(function(global){
  'use strict';
  const PREF_KEY='ourtime.home.prefs.v1';
  const HIDE_DAYS=30;
  const RECENT_DAYS=7;
  const TABS=[
    {id:'all', label:'回忆'},
    {id:'on_this_day', label:'往年今日'},
    {id:'place_revisit', label:'重访一个地方'},
    {id:'people_years', label:'人物这些年'}
  ];
  const KIND_LABEL={on_this_day:'往年今日', on_this_month:'那年这个月', place_span:'重访一个地方', person_years:'人物这些年', person_fragment:'人物片段', memory_day:'这一天', memory_trip:'一次出行', memory_person:'这些人'};
  let generation=0, payload=null, lastRevision='';
  const cache=new Map();
  const inflight=new Map();
  let homeAbort=null, waitTimer=null;
  let player={
    open:false,
    ids:[],
    index:0,
    timer:null,
    generation:0,
    snapshot:'',
    sourceId:'',
    title:'',
    status:'idle',
    introShown:false,
    returnFocus:null
  };
  const HOLD_MS=6000;
  const KEN_MS=6400;
  const BURST_SEC=20;
  const BURST_ID_GAP=8;
  const OVERTURES=[
    '把这一天再看一遍',
    '有些风景，隔了很多年依然清晰',
    '从一张照片开始',
    '那一年的光还在',
    '旧日未远',
    '光还在原来的地方',
    '先看这一程',
    '时光还在'
  ];

  const $=s=>document.querySelector(s);
  const $$=s=>document.querySelectorAll(s);
  const esc=global.esc || (v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])));
  const thumb=id=>'/api/thumb/'+id+'?w=960';

  function prefs(){
    try{return JSON.parse(localStorage.getItem(PREF_KEY)||'{}');}catch{return {};}
  }
  function savePrefs(next){
    try{localStorage.setItem(PREF_KEY, JSON.stringify(next));}catch{/* private mode still browses */}
  }
  function readPrefs(){
    const data=prefs();
    const now=Date.now();
    const hidden=(data.hidden||[]).filter(item=>item.until>now);
    const recent=(data.recent||[]).filter(item=>now-item.at<RECENT_DAYS*86400000);
    if(hidden.length!==(data.hidden||[]).length || recent.length!==(data.recent||[]).length){
      savePrefs({version:1, hidden, recent, cursor:data.cursor||{}});
    }
    return {version:1, hidden, recent, cursor:data.cursor||{}};
  }
  function isHidden(id){return readPrefs().hidden.some(item=>item.group_id===id);}
  function isRecent(id){return readPrefs().recent.some(item=>item.group_id===id);}
  function hideGroup(id){
    const data=readPrefs();
    data.hidden=data.hidden.filter(item=>item.group_id!==id);
    data.hidden.push({group_id:id, until:Date.now()+HIDE_DAYS*86400000});
    savePrefs(data);
  }
  function restoreHidden(){
    const data=readPrefs();
    data.hidden=[];
    savePrefs(data);
  }
  function rememberOpen(id){
    const data=readPrefs();
    data.recent=data.recent.filter(item=>item.group_id!==id);
    data.recent.push({group_id:id, at:Date.now()});
    savePrefs(data);
  }
  function ranked(list){
    const visible=(list||[]).filter(card=>!isHidden(card.group_id));
    const fresh=visible.filter(card=>!isRecent(card.group_id));
    const old=visible.filter(card=>isRecent(card.group_id));
    return fresh.concat(old);
  }
  function nextCursor(category){
    const data=readPrefs();
    return Number(data.cursor[category]||0);
  }
  function setCursor(category, value){
    const data=readPrefs();
    data.cursor[category]=value;
    savePrefs(data);
  }
  function pickCard(category){
    const list=ranked((payload&&payload.candidates&&payload.candidates[category])||[]);
    if(!list.length) return null;
    const index=nextCursor(category)%list.length;
    return list[index];
  }
  function kindLabel(card){return KIND_LABEL[card.kind]||'推荐';}
  function coversOf(card){
    if(Array.isArray(card.covers)&&card.covers.length){
      return card.covers.map(item=>({
        asset_id:item.asset_id,
        year:item.year||'',
        target_face_id:item.target_face_id||null,
        object_position:item.object_position||'50% 28%',
        preview:item.preview||'thumb'
      }));
    }
    const years=card.years||[];
    return (card.cover_asset_ids||[]).map((id,i)=>({
      asset_id:id, year:years[i]||'', target_face_id:null, object_position:'50% 28%', preview:'thumb'
    }));
  }
  function coverSrc(cover){
    if(cover.preview==='face' && cover.target_face_id) return '/api/face/'+cover.target_face_id;
    return thumb(cover.asset_id);
  }
  function photoTag(id, extra, toolsHtml){
    const cls=extra||'home-card-photo';
    const img=id?'<img src="'+esc(thumb(id))+'" alt="">':'';
    return '<div class="home-media '+cls+'">'+img+(toolsHtml||'')+'</div>';
  }
  function tools(card, slot, options){
    const withSwap=!(options && options.swap===false);
    return '<div class="home-card-tools">'
      +'<button type="button" class="home-card-more" data-home-actions-toggle aria-expanded="false">更多</button>'
      +'<div class="home-card-menu" hidden>'
      +(withSwap?'<button type="button" data-home-swap="'+esc(slot)+'">换一组</button>':'')
      +'<button type="button" data-home-hide="'+esc(card.group_id)+'">暂时不推荐</button>'
      +'</div></div>';
  }
  function heroCopy(card){
    const n=Number(card.photo_count||0);
    if(card.kind==='on_this_day') return '这一天留下了 '+n+' 张照片';
    if(card.kind==='on_this_month') return '这个月留下了 '+n+' 张照片';
    return card.subtitle||'';
  }
  function hero(card, slot){
    const cover=coversOf(card)[0];
    const coverId=cover?cover.asset_id:(card.cover_asset_ids||[])[0];
    const place=card.place_label?(' · '+card.place_label):'';
    return '<article class="home-hero" data-home-slot="'+esc(slot||'on_this_day')+'" data-home-open="'+esc(card.group_id)+'">'
      +photoTag(coverId,'home-hero-photo',tools(card, slot||'on_this_day'))
      +'<div class="home-hero-text"><p class="home-kicker">'+esc(kindLabel(card))+'</p>'
      +'<h3>'+esc(card.title)+esc(place)+'</h3>'
      +'<p class="home-copy">'+esc(heroCopy(card))+'</p>'
      +'<button type="button" class="primary" data-home-open="'+esc(card.group_id)+'">'+esc(card.cta||'往年今日')+'</button>'
      +'</div></article>';
  }
  function placeCard(card, options){
    const title=card.title||'';
    const full=card.title_full||card.place||title;
    return '<article class="home-card" data-home-slot="place_revisit" data-home-open="'+esc(card.group_id)+'">'
      +photoTag((coversOf(card)[0]||{}).asset_id||(card.cover_asset_ids||[])[0],'home-card-photo',tools(card,'place_revisit', options))
      +'<p class="home-kicker">重访一个地方</p>'
      +'<h3 title="'+esc(full)+'">'+esc(title)+'</h3>'
      +'<p class="home-copy">'+esc(heroCopy(card))+'</p>'
      +'</article>';
  }
  function peopleCard(card, options){
    const covers=coversOf(card).slice(0,4);
    let mosaic='';
    if(covers.length>1){
      mosaic='<div class="home-media home-years-wrap"><div class="home-years" data-count="'+covers.length+'" style="--year-cols:'+covers.length+'">'
        +covers.map(item=>{
          const year=item.year?('<b>'+esc(item.year)+'</b>'):'';
          return '<div class="home-year-photo"><img src="'+esc(coverSrc(item))+'" alt="" style="object-position:'+esc(item.object_position)+'" data-fallback="'+esc(thumb(item.asset_id))+'">'+year+'</div>';
        }).join('')
        +'</div>'+tools(card,'people_years', options)+'</div>';
    }else mosaic=photoTag(covers[0]&&covers[0].asset_id,'home-card-photo',tools(card,'people_years', options));
    return '<article class="home-card" data-home-slot="people_years" data-home-open="'+esc(card.group_id)+'">'
      +mosaic
      +'<p class="home-kicker">'+esc(kindLabel(card))+'</p>'
      +'<h3>'+esc(card.title)+'</h3>'
      +'<p class="home-copy">'+esc(card.subtitle||'')+'</p>'
      +'</article>';
  }
  function memoryCard(card, slot, options){
    const covers=coversOf(card).slice(0,3);
    const mosaic='<div class="home-media home-memory-mosaic" data-count="'+covers.length+'">'
      +covers.map(item=>'<img src="'+esc(coverSrc(item))+'" alt="" data-fallback="'+esc(thumb(item.asset_id))+'" style="object-position:'+esc(item.object_position||'50% 28%')+'">').join('')
      +tools(card, slot||card.kind, options)
      +'</div>';
    return '<article class="home-memory" data-home-slot="'+esc(slot||card.kind)+'" data-home-open="'+esc(card.group_id)+'" data-home-play="1">'
      +mosaic
      +'<h3>'+esc(card.title||'')+'</h3>'
      +(card.subtitle?('<p class="home-memory-meta">'+esc(card.subtitle)+'</p>'):'')
      +'</article>';
  }
  function gridCard(card){
    const listTools={swap:false};
    if(String(card.kind||'').startsWith('memory')) return memoryCard(card, card.kind, listTools);
    if(card.kind==='place_span') return placeCard(card, listTools);
    if(String(card.kind).startsWith('person')) return peopleCard(card, listTools);
    return '<article class="home-card" data-home-open="'+esc(card.group_id)+'">'
      +photoTag((coversOf(card)[0]||{}).asset_id,'home-card-photo',tools(card, state.homeCategory||'on_this_day', listTools))
      +'<p class="home-kicker">'+esc(kindLabel(card))+'</p>'
      +'<h3>'+esc(card.title)+'</h3>'
      +'<p class="home-copy">'+esc(heroCopy(card))+'</p>'
      +'</article>';
  }
  function emptyHtml(kind){
    if(kind==='loading') return '<div class="home-status home-status-plain"><h3>加载中</h3></div>';
    if(kind==='updating') return '<div class="home-status home-status-plain"><h3>更新中</h3></div>';
    const messages={
      empty:['暂时没有合适的推荐','照片少、日期不准确或人物都还没命名时，会先空着这一页。'],
      error:['推荐暂时不可用','可以稍后再试，或者去全部照片里继续看。']
    };
    const pair=messages[kind]||messages.error;
    return '<div class="home-status"><h3>'+pair[0]+'</h3><p>'+pair[1]+'</p><div class="home-actions">'
      +(kind==='error'?'<button type="button" class="secondary" id="home-retry">重试</button>':'')
      +'<button type="button" class="primary" data-view-all>全部照片</button></div></div>';
  }

  function skeletonHtml(){
    return emptyHtml('loading');
  }
  function syncTabs(){
    $$('#home-tabs button').forEach(btn=>btn.setAttribute('aria-selected', btn.dataset.homeTab===(state.homeCategory||'all')?'true':'false'));
  }
  function setBoardBusy(on){
    const board=$('#home-board');
    if(!board) return;
    if(waitTimer){clearTimeout(waitTimer); waitTimer=null;}
    if(!on){ board.classList.remove('is-busy','is-wait'); board.removeAttribute('aria-busy'); return; }
    board.classList.add('is-busy');
    board.setAttribute('aria-busy','true');
    waitTimer=null;
  }
  function boardHasContent(board){
    return !!(board && board.querySelector('.home-hero, .home-card, .home-grid, .home-memory, .home-memories'));
  }
  function applyPayload(data, category){
    payload=data;
    state.homeCategory=category;
    syncTabs();
    setBoardBusy(false);
    renderDate(payload);
    render();
    revealCatalogButton(!!(payload && payload.catalog));
  }
  function rememberPayload(data){
    if(data.revision && lastRevision && data.revision!==lastRevision) cache.clear();
    if(data.revision) lastRevision=data.revision;
    cache.set(data.category, data);
  }
  function cachedPayload(category){
    const hit=cache.get(category);
    if(hit && (!lastRevision || !hit.revision || hit.revision===lastRevision)) return hit;
    const all=cache.get('all');
    const list=all && all.candidates && all.candidates[category];
    if(category!=='all' && all && Array.isArray(list) && list.length && (!lastRevision || !all.revision || all.revision===lastRevision)){
      return Object.assign({}, all, {category:category, items:list.slice(0,6), next_cursor:list.length>6?'o:6':null});
    }
    return null;
  }
  function slotHtml(card, slot){
    if(String(slot||'').startsWith('memory') || String(card.kind||'').startsWith('memory')) return memoryCard(card, slot);
    if(slot==='people_years' || String(card.kind||'').startsWith('person')) return peopleCard(card);
    if(slot==='place_revisit' || card.kind==='place_span') return placeCard(card);
    return hero(card, slot||'on_this_day');
  }
  function replaceSlot(slot){
    const card=pickCard(slot);
    const node=document.querySelector('[data-home-slot="'+slot+'"]');
    if(!card){
      if(node) node.remove();
      const pair=document.querySelector('.home-pair');
      if(pair){
        const n=pair.querySelectorAll('[data-home-slot]').length;
        pair.classList.toggle('is-single', n===1);
        if(!n) pair.remove();
      }
      return;
    }
    const wrap=document.createElement('div');
    wrap.innerHTML=slotHtml(card, slot);
    const next=wrap.firstElementChild;
    if(node) node.replaceWith(next);
    else render();
  }
  function appendGrid(items){
    const grid=document.querySelector('#home-board .home-grid');
    if(!grid){ render(); return; }
    grid.insertAdjacentHTML('beforeend', items.map(gridCard).join(''));
    const more=$('#home-more');
    if(!payload.next_cursor && more){
      const host=more.closest('.home-more');
      if(host) host.remove();
    }
  }

  function renderDate(info){
    const el=$('#home-date');
    if(!el||!info) return;
    const day=info.as_of_date||'';
    const parts=day.split('-');
    const month=parts[1]?Number(parts[1])+'月':'';
    const d=parts[2]?Number(parts[2])+'日':'';
    const year=parts[0]||'';
    const weekday=info.weekday||'';
    const second=esc(year?(year+'年'):'')+(weekday?(' · '+esc(weekday)):'');
    el.innerHTML='<strong>'+esc(month+d)+'</strong><span>'+second+'</span>';
  }
  function render(){
    const board=$('#home-board');
    if(!board||!payload) return;
    const category=state.homeCategory||'all';
    if(category==='all'){
      const slots=['memory_day','memory_trip','memory_person'];
      const used=new Set();
      const picked=[];
      (payload.items||[]).forEach(card=>{
        if(picked.length>=3 || !card || !card.group_id || isHidden(card.group_id)) return;
        const kind=String(card.kind||'');
        const slot=slots.find(name=>kind.indexOf(name.replace('memory_',''))>=0)||kind||'memory_trip';
        picked.push({card, slot});
        used.add(card.group_id);
      });
      slots.forEach(slot=>{
        const card=pickCard(slot);
        if(card && !used.has(card.group_id) && picked.length<3){ picked.push({card, slot}); used.add(card.group_id); }
      });
      if(picked.length<3){
        const extras=[].concat(ranked((payload.candidates||{}).memory_day||[]), ranked((payload.candidates||{}).memory_trip||[]), ranked((payload.candidates||{}).memory_person||[]));
        extras.forEach(card=>{
          if(picked.length>=3 || used.has(card.group_id)) return;
          picked.push({card, slot:card.kind||'memory_trip'});
          used.add(card.group_id);
        });
      }
      if(!picked.length){
        const day=pickCard('on_this_day');
        const place=pickCard('place_revisit');
        const person=pickCard('people_years');
        if(!day && !place && !person){board.innerHTML=emptyHtml('empty');return;}
        let html='', rest=[];
        if(day){ html+=hero(day,'on_this_day'); rest=[place,person].filter(Boolean); }
        else if(place){ html+=hero(place,'place_revisit'); rest=[person].filter(Boolean); }
        else { html+=hero(person,'people_years'); }
        if(rest.length){
          html+='<div class="home-pair'+(rest.length===1?' is-single':'')+'">';
          html+=rest.map(card=>String(card.kind||'').startsWith('person')?peopleCard(card):placeCard(card)).join('');
          html+='</div>';
        }
        if(readPrefs().hidden.length) html+='<p class="home-more"><button type="button" class="text-button" id="home-restore-hidden">恢复暂时隐藏的推荐</button></p>';
        board.innerHTML=html;
        return;
      }
      let html='<div class="home-memories">'+picked.map(item=>memoryCard(item.card, item.slot)).join('')+'</div>';
      if(readPrefs().hidden.length) html+='<p class="home-more"><button type="button" class="text-button" id="home-restore-hidden">恢复暂时隐藏的推荐</button></p>';
      board.innerHTML=html;
      return;
    }
    const items=payload.items||[];
    if(!items.length){board.innerHTML=emptyHtml('empty');return;}
    board.innerHTML='<div class="home-grid">'+items.map(gridCard).join('')+'</div>'
      +(payload.next_cursor?'<p class="home-more"><button type="button" class="secondary" id="home-more">查看更多</button></p>':'')
      +(readPrefs().hidden.length?'<p class="home-more"><button type="button" class="text-button" id="home-restore-hidden">恢复暂时隐藏的推荐</button></p>':'');
  }
  async function loadHomeDiscovery(options={}){
    const category=options.category||state.homeCategory||'all';
    const board=$('#home-board');
    const canUseCache=!options.cursor && !options.force && !options.append;
    state.homeCategory=category;
    syncTabs();
    if(canUseCache){
      const hit=cachedPayload(category);
      if(hit){
        applyPayload(hit, category);
        return;
      }
      const pending=inflight.get(category) || (category!=='all' ? inflight.get('all') : null);
      if(pending){
        const ticket=++generation;
        if(board && !boardHasContent(board)) board.innerHTML=emptyHtml('loading');
        try{ await pending; }catch(error){ if(ticket!==generation) return; }
        if(ticket!==generation) return;
        const again=cachedPayload(category);
        if(again){
          applyPayload(again, category);
          return;
        }
      }
    }
    if(options.force || options.append || options.cursor){
      if(homeAbort) homeAbort.abort();
    }
    const ac=new AbortController();
    homeAbort=ac;
    if(options.signal){
      if(options.signal.aborted) ac.abort();
      else options.signal.addEventListener('abort', ()=>ac.abort(), {once:true});
    }
    const ticket=++generation;
    if(board && !options.append && (options.force || !boardHasContent(board))){
      board.innerHTML=emptyHtml(options.force?'updating':'loading');
      board.classList.remove('is-busy','is-wait');
      board.removeAttribute('aria-busy');
    }
    const run=(async()=>{
      const query=new URLSearchParams({category:category});
      if(options.cursor) query.set('cursor', options.cursor);
      const data=await api('/api/home/recommendations?'+query.toString(), {signal:ac.signal});
      rememberPayload(data);
      return data;
    })();
    inflight.set(category, run);
    try{
      const data=await run;
      if(ticket!==generation) return;
      if(options.append && payload && payload.category===data.category){
        const seen=new Set((payload.items||[]).map(card=>card.group_id));
        const extra=(data.items||[]).filter(card=>!seen.has(card.group_id));
        payload=Object.assign({}, data, {items:[...(payload.items||[]), ...extra]});
        rememberPayload(payload);
        state.homeCategory=category;
        syncTabs(); setBoardBusy(false); renderDate(payload); appendGrid(extra);
        revealCatalogButton(!!payload.catalog);
      }else{
        applyPayload(data, category);
      }
    }catch(error){
      if(error && error.name==='AbortError') return;
      if(ticket!==generation) return;
      setBoardBusy(false);
      if(board && !boardHasContent(board)) board.innerHTML=emptyHtml('error');
      else if(typeof toast==='function') toast((error && error.message) || '推荐暂时读不到', true);
    }finally{
      if(inflight.get(category)===run) inflight.delete(category);
    }
  }
  function revealCatalogButton(on){
    const btn=$('#home-refresh-catalog');
    if(btn) btn.hidden=!on;
  }
  async function refreshCatalog(){
    const btn=$('#home-refresh-catalog');
    if(btn){ btn.disabled=true; btn.textContent='更新中'; }
    const board=$('#home-board');
    const hadCards=boardHasContent(board);
    if(board && !hadCards) board.innerHTML=emptyHtml('updating');
    try{
      await api('/api/home/catalog/rebuild', {method:'POST', timeoutMs:180000});
      cache.clear();
      lastRevision='';
      await loadHomeDiscovery({force:true});
    }catch(error){
      if(error && error.name==='AbortError') return;
      if(typeof toast==='function') toast((error && error.message) || '推荐没有更新完', true);
      if(board && !hadCards && !boardHasContent(board)) board.innerHTML=emptyHtml('error');
    }finally{
      if(btn){ btn.disabled=false; btn.textContent='更新推荐'; }
      setBoardBusy(false);
    }
  }
  function playerEl(){ return document.getElementById('memory-player'); }
  function frameSrc(id){ return '/api/preview/'+id; }
  function stopPlayerTimer(){ if(player.timer){ clearTimeout(player.timer); player.timer=null; } }
  function kenWrap(img){ return img && (img.closest('.memory-ken') || img.parentElement); }
  function memoryKenLayers(){ return [kenWrap($('#memory-frame-a')), kenWrap($('#memory-frame-b'))].filter(Boolean); }
  function setKenPaused(paused){
    memoryKenLayers().forEach(layer=>{
      const animation=layer._memoryKenAnimation;
      if(!animation) return;
      try{ if(paused) animation.pause(); else animation.play(); }catch{/* animation may already be replaced */}
    });
  }
  function currentKenLayer(){
    const img=player.index%2===1?$('#memory-frame-b'):$('#memory-frame-a');
    return kenWrap(img);
  }
  function setMemoryStatus(message=''){
    const status=$('#memory-status');
    if(!status) return;
    status.textContent=message;
    status.hidden=!message;
  }
  function syncMemoryPlayerUI(){
    const root=playerEl();
    if(root) root.dataset.state=player.status;
    const toggle=$('#memory-toggle');
    if(toggle){
      const paused=player.status==='paused';
      toggle.hidden=player.status==='ended';
      toggle.disabled=!['playing','paused'].includes(player.status);
      toggle.textContent=paused?'继续':'暂停';
      toggle.setAttribute('aria-pressed',String(paused));
      toggle.setAttribute('aria-label',paused?'继续播放回忆':'暂停播放回忆');
    }
    const ended=player.status==='ended';
    const endBox=$('#memory-end');
    if(endBox) endBox.hidden=!ended;
    const actions=$('#memory-actions');
    if(actions) actions.hidden=ended;
  }
  function schedulePlayerAdvance(last, ticket, token, index){
    stopPlayerTimer();
    if(player.status!=='playing') return;
    player.timer=setTimeout(()=>{
      if(player.generation!==ticket || player.frameToken!==token || player.index!==index || !player.open || player.status!=='playing') return;
      if(last) finishMemoryPlayer();
      else showPlayerFrame(player.index+1, true);
    }, HOLD_MS);
  }
  function pauseMemoryPlayer({announce=true}={}){
    if(player.status!=='playing') return;
    player.status='paused';
    stopPlayerTimer();
    setKenPaused(true);
    setMemoryStatus(announce?'已暂停':'');
    syncMemoryPlayerUI();
  }
  function resumeMemoryPlayer(){
    if(player.status!=='paused') return;
    player.status='playing';
    setMemoryStatus('');
    const layer=currentKenLayer();
    const img=layer&&layer.querySelector('img');
    if(layer&&img){
      if(layer._memoryKenAnimation) layer._memoryKenAnimation.cancel();
      applyKen(layer, img, player.meta&&player.meta[player.ids[player.index]], player.index);
    }
    const last=player.index>=player.ids.length-1;
    schedulePlayerAdvance(last, player.generation, player.frameToken, player.index);
    syncMemoryPlayerUI();
  }
  function toggleMemoryPlayer(){
    if(player.status==='playing') pauseMemoryPlayer();
    else if(player.status==='paused') resumeMemoryPlayer();
  }
  function memoryFocusable(){
    const root=playerEl();
    if(!root) return [];
    return [...root.querySelectorAll('button:not([hidden]):not(:disabled), [href], [tabindex]:not([tabindex="-1"])')]
      .filter(item=>!item.closest('[hidden]') && item.offsetParent!==null);
  }
  function focusMemoryControl(){
    const target=$('#memory-toggle');
    if(target&&!target.hidden&&!target.disabled) target.focus();
    else playerEl()?.focus();
  }
  function trapMemoryFocus(event){
    const items=memoryFocusable();
    if(!items.length){ event.preventDefault(); playerEl()?.focus(); return; }
    const first=items[0],last=items[items.length-1],active=document.activeElement;
    if(event.shiftKey && (active===first || !playerEl()?.contains(active))){ event.preventDefault(); last.focus(); }
    else if(!event.shiftKey && active===last){ event.preventDefault(); first.focus(); }
  }
  function isTypingTarget(target){
    return Boolean(target&&target.closest&&target.closest('input,textarea,select,[contenteditable="true"]'));
  }
  function isInteractiveTarget(target){
    return Boolean(target&&target.closest&&target.closest('button,a,input,textarea,select,[contenteditable="true"]'));
  }
  function parseStamp(value){
    if(!value) return 0;
    const t=Date.parse(String(value).replace(' ','T'));
    return Number.isFinite(t)?t:0;
  }
  function parseFaceBox(face){
    let box=face && face.bbox;
    if(typeof box==='string'){ try{ box=JSON.parse(box); }catch{ box=null; } }
    if(!Array.isArray(box) || box.length<4) return null;
    const imgW=Number(box[4])||0, imgH=Number(box[5])||0;
    if(imgW<=1 || imgH<=1) return null;
    const x1=Number(box[0])/imgW, y1=Number(box[1])/imgH, x2=Number(box[2])/imgW, y2=Number(box[3])/imgH;
    if(![x1,y1,x2,y2].every(Number.isFinite)) return null;
    return {x1,y1,x2,y2, imgW, imgH};
  }
  function faceCluster(faces){
    const boxes=(faces||[]).map(parseFaceBox).filter(Boolean);
    if(!boxes.length) return null;
    let x1=1,y1=1,x2=0,y2=0;
    boxes.forEach(b=>{ x1=Math.min(x1,b.x1); y1=Math.min(y1,b.y1); x2=Math.max(x2,b.x2); y2=Math.max(y2,b.y2); });
    const padX=Math.max(0.04,(x2-x1)*0.18);
    const padYTop=Math.max(0.06,(y2-y1)*0.28);
    const padYBot=Math.max(0.04,(y2-y1)*0.16);
    x1=Math.max(0,x1-padX); y1=Math.max(0,y1-padYTop);
    x2=Math.min(1,x2+padX); y2=Math.min(1,y2+padYBot);
    return {count:boxes.length, x1,y1,x2,y2, cx:(x1+x2)/2, cy:(y1+y2)/2, span:Math.max(x2-x1,y2-y1)};
  }
  function imageSize(meta, img){
    const w=Number((img && img.naturalWidth) || (meta && meta.width) || 0);
    const h=Number((img && img.naturalHeight) || (meta && meta.height) || 0);
    if(w>1 && h>1) return {w,h};
    const box=(meta && meta.faces || []).map(parseFaceBox).find(Boolean);
    if(box && box.imgW>1 && box.imgH>1) return {w:box.imgW, h:box.imgH};
    return null;
  }
  function coverFocus(imgW, imgH, viewW, viewH, cx, cy){
    const s=Math.max(viewW/imgW, viewH/imgH);
    const extraX=imgW*s-viewW;
    const extraY=imgH*s-viewH;
    let px=50, py=50;
    if(extraX>1) px=Math.max(0, Math.min(100, ((cx*imgW*s-viewW/2)/extraX)*100));
    if(extraY>1) py=Math.max(0, Math.min(100, ((cy*imgH*s-viewH/2)/extraY)*100));
    const offsetX=-extraX*(px/100);
    const offsetY=-extraY*(py/100);
    return {
      px, py, s,
      left: -offsetX/s,
      top: -offsetY/s,
      visW: viewW/s,
      visH: viewH/s,
      focusX: (-offsetX+viewW/2)/s,
      focusY: (-offsetY+viewH/2)/s
    };
  }
  function maxScaleForBox(cluster, focus, imgW, imgH){
    const x1=cluster.x1*imgW, y1=cluster.y1*imgH, x2=cluster.x2*imgW, y2=cluster.y2*imgH;
    const needW=Math.max(focus.focusX-x1, x2-focus.focusX)*2;
    const needH=Math.max(focus.focusY-y1, y2-focus.focusY)*2;
    return Math.min(focus.visW/Math.max(needW,1), focus.visH/Math.max(needH,1));
  }
  function kenPlan(meta, index, viewW, viewH, img){
    const cluster=faceCluster(meta && meta.faces);
    const size=imageSize(meta, img);
    if(cluster && size && viewW>1 && viewH>1){
      const focus=coverFocus(size.w, size.h, viewW, viewH, cluster.cx, cluster.cy);
      let maxS=maxScaleForBox(cluster, focus, size.w, size.h);
      const coverClips=maxS<1.001;
      const small=cluster.count<5;
      if(coverClips){
        return {fit:'contain', objectX:cluster.cx*100, objectY:cluster.cy*100, ox:50, oy:50, from:1, to:1, tx0:0, ty0:0, tx1:0, ty1:0};
      }
      const cap=small?1.12:1.08;
      maxS=Math.max(1.0, Math.min(cap, maxS*0.92));
      const minS=Math.max(1.0, Math.min(maxS, small?1.02:1.01));
      if(maxS-minS<0.015){
        return {fit:'cover', objectX:focus.px, objectY:focus.py, ox:50, oy:50, from:1, to:Math.min(1.03, maxS||1.02), tx0:0, ty0:0, tx1:0, ty1:0};
      }
      const zoomIn=index%2===0;
      return {fit:'cover', objectX:focus.px, objectY:focus.py, ox:50, oy:50, from:zoomIn?minS:maxS, to:zoomIn?maxS:minS, tx0:0, ty0:0, tx1:0, ty1:0};
    }
    const scenic=[
      {from:1.08,to:1.18,tx0:3,ty0:2,tx1:-3,ty1:-2},
      {from:1.08,to:1.18,tx0:-3,ty0:2,tx1:3,ty1:-2},
      {from:1.16,to:1.08,tx0:3,ty0:-1.4,tx1:-2,ty1:1.4},
      {from:1.16,to:1.08,tx0:-3,ty0:1.4,tx1:2,ty1:-1.4},
      {from:1.12,to:1.16,tx0:4.6,ty0:1,tx1:-4.6,ty1:-1},
      {from:1.12,to:1.16,tx0:-4.6,ty0:-1,tx1:4.6,ty1:1},
      {from:1.10,to:1.16,tx0:1,ty0:4.2,tx1:-1,ty1:-3.8},
      {from:1.10,to:1.16,tx0:-1,ty0:-3.8,tx1:1,ty1:4}
    ];
    return Object.assign({fit:'cover', objectX:50, objectY:50, ox:50, oy:50}, scenic[index%scenic.length]);
  }
  function resetKen(el){
    if(!el) return;
    el.getAnimations().forEach(anim=>anim.cancel());
    el._memoryKenAnimation=null;
    el.classList.remove('is-in');
    el.style.transformOrigin='';
    el.style.transform='';
    const img=el.querySelector('img');
    if(img){ img.style.objectPosition=''; img.style.objectFit=''; }
  }
  function applyKen(el, img, meta, index){
    if(!el || window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const stage=el.closest('.memory-stage') || playerEl();
    const viewW=(stage && stage.clientWidth) || window.innerWidth;
    const viewH=(stage && stage.clientHeight) || window.innerHeight;
    const plan=kenPlan(meta, index, viewW, viewH, img);
    if(img){
      img.style.objectFit=plan.fit||'cover';
      img.style.objectPosition=plan.objectX+'% '+plan.objectY+'%';
    }
    el.style.transformOrigin=plan.ox+'% '+plan.oy+'%';
    el.style.transform='scale('+plan.from+') translate('+plan.tx0+'%, '+plan.ty0+'%)';
    el._memoryKenAnimation=el.animate(
      [
        {transform:'scale('+plan.from+') translate('+plan.tx0+'%, '+plan.ty0+'%)'},
        {transform:'scale('+plan.to+') translate('+plan.tx1+'%, '+plan.ty1+'%)'}
      ],
      {duration:KEN_MS, easing:'cubic-bezier(.18,0,.18,1)', fill:'forwards'}
    );
  }
  function isBurstPair(a, b){
    if(!a || !b) return false;
    if(a.id===b.id) return true;
    const dt=Math.abs((a.captured||0)-(b.captured||0))/1000;
    if(a.captured && b.captured) return dt<=BURST_SEC;
    if(!a.captured || !b.captured) return Math.abs(a.id-b.id)<=3;
    return false;
  }
  function dedupePlaylist(ids, meta){
    const kept=[];
    (ids||[]).forEach(id=>{
      const row=meta[id]||{id:id, captured:0};
      if(kept.some(prev=>isBurstPair(meta[prev]||{id:prev,captured:0}, row))) return;
      kept.push(id);
    });
    return kept.length?kept:(ids||[]).slice();
  }
  function isLowRes(meta){
    const w=Number(meta&&meta.width||0), h=Number(meta&&meta.height||0);
    return w<=0 || h<=0 || Math.max(w,h)<1000 || (w*h)<600000;
  }
  function dropLowRes(ids, map){
    const sharp=(ids||[]).filter(id=>map[id] && !isLowRes(map[id]));
    return sharp.length>=5?sharp:ids;
  }
  function peopleIds(meta){
    const ids=new Set();
    (meta && meta.faces || []).forEach(face=>{
      const id=Number(face.person_id);
      if(id>0) ids.add(id);
    });
    return ids;
  }
  function clusterDayMetas(metas){
    const dated=[], undated=[];
    (metas||[]).forEach(item=>{
      if(item.captured) dated.push(item);
      else undated.push(item);
    });
    dated.sort((a,b)=>a.captured-b.captured || a.id-b.id);
    const clusters=[];
    dated.forEach(row=>{
      const people=peopleIds(row);
      if(!clusters.length){
        clusters.push({rows:[row], end:row.captured, people});
        return;
      }
      const last=clusters[clusters.length-1];
      const gap=(row.captured-last.end)/1000;
      let shared=false;
      people.forEach(id=>{ if(last.people.has(id)) shared=true; });
      const both=people.size>0 && last.people.size>0;
      if(both && !shared && gap>20*60){
        clusters.push({rows:[row], end:row.captured, people});
        return;
      }
      if(gap<=40*60 || (shared && gap<=12*3600)){
        last.rows.push(row);
        last.end=row.captured;
        people.forEach(id=>last.people.add(id));
      }else{
        clusters.push({rows:[row], end:row.captured, people});
      }
    });
    undated.forEach(row=>{
      const people=peopleIds(row);
      const match=clusters.find(cl=>{
        let hit=false;
        people.forEach(id=>{ if(cl.people.has(id)) hit=true; });
        return hit;
      });
      if(match){
        match.rows.push(row);
        people.forEach(id=>match.people.add(id));
      }
    });
    clusters.sort((a,b)=>b.rows.length-a.rows.length || b.people.size-a.people.size);
    return clusters;
  }
  function pickDayPlaylist(metas, map){
    const clusters=clusterDayMetas(metas);
    for(let i=0;i<clusters.length;i+=1){
      const ids=dedupePlaylist(clusters[i].rows.map(item=>item.id), map);
      const usable=dropLowRes(ids, map);
      if(usable.length>=5) return usable.slice(0,10);
    }
    return [];
  }
  function preloadPreview(id){
    if(!id) return Promise.reject(new Error('missing'));
    if(!player.preloads) player.preloads=new Map();
    if(player.preloads.has(id)) return player.preloads.get(id);
    const pending=new Promise((resolve, reject)=>{
      const img=new Image();
      img.onload=()=>resolve(img);
      img.onerror=()=>reject(new Error('preview'));
      img.src=frameSrc(id);
    });
    player.preloads.set(id, pending);
    return pending;
  }
  function clearMemoryLayers(){
    const a=$('#memory-frame-a'); const b=$('#memory-frame-b');
    [a,b].forEach(img=>{
      if(!img) return;
      img.onload=null;
      img.removeAttribute('src');
      resetKen(kenWrap(img));
    });
    player.preloads=new Map();
  }
  function pickOverture(){
    let i=Math.floor(Math.random()*OVERTURES.length);
    if(player.lastOverture===i && OVERTURES.length>1) i=(i+1)%OVERTURES.length;
    player.lastOverture=i;
    return OVERTURES[i];
  }
  function hideOverture(){
    player.overtureActive=false;
    if(player.overtureTimer){ clearTimeout(player.overtureTimer); player.overtureTimer=null; }
    const done=player.overtureResolve;
    player.overtureResolve=null;
    if(done) done();
    const root=playerEl();
    if(root) root.classList.remove('is-overture');
    const el=$('#memory-overture');
    if(!el) return;
    el.classList.remove('is-on');
    setTimeout(()=>{ if(el && !el.classList.contains('is-on')) el.hidden=true; }, 1100);
  }
  function playOverture(){
    const el=$('#memory-overture');
    const line=$('#memory-overture-line');
    const root=playerEl();
    if(!el || !root) return Promise.resolve();
    if(line) line.textContent=pickOverture();
    el.hidden=false;
    void el.offsetWidth;
    el.classList.add('is-on');
    root.classList.add('is-overture');
    player.overtureActive=true;
    const hold=2800+Math.floor(Math.random()*900);
    return new Promise(resolve=>{
      player.overtureResolve=resolve;
      player.overtureTimer=setTimeout(()=>{
        player.overtureTimer=null;
        const done=player.overtureResolve;
        player.overtureResolve=null;
        if(done) done();
      }, hold);
    });
  }
  function closeMemoryPlayer(){
    const returnFocus=player.returnFocus;
    player.open=false; player.generation++; player.status='idle'; stopPlayerTimer();
    hideOverture();
    const el=playerEl();
    if(el){ el.hidden=true; el.classList.remove('is-on','is-ending'); delete el.dataset.state; }
    document.body.classList.remove('is-memory-player');
    setMemoryStatus('');
    clearMemoryLayers();
    player.returnFocus=null;
    if(returnFocus&&returnFocus.isConnected&&typeof returnFocus.focus==='function') returnFocus.focus();
  }
  function finishMemoryPlayer(message=''){
    player.status='ended';
    stopPlayerTimer();
    setKenPaused(true);
    setMemoryStatus(message);
    updatePlayerCaption();
    syncMemoryPlayerUI();
    $('#memory-again')?.focus();
  }
  function updatePlayerCaption(){
    const title=$('#memory-title'); const progress=$('#memory-progress'); const dots=$('#memory-dots');
    const year=$('#memory-year');
    if(title) title.textContent=player.title||'';
    if(year){
      const current=player.ids[player.index];
      const meta=current && player.meta ? player.meta[current] : null;
      const label=meta && meta.captured ? String(new Date(meta.captured).getFullYear()) : '';
      year.hidden=!label;
      year.textContent=label?label+'年':'';
    }
    if(progress) progress.textContent=player.ids.length?((player.index+1)+' / '+player.ids.length):'';
    if(dots){
      dots.innerHTML=player.ids.map((_,i)=>'<button type="button" data-memory-index="'+i+'" aria-label="第 '+(i+1)+' 张" aria-current="'+(i===player.index?'true':'false')+'"><span aria-hidden="true"></span></button>').join('');
    }
    syncMemoryPlayerUI();
  }
  function showPlayerFrame(index, animate){
    const ids=player.ids;
    if(!ids.length) return;
    const token=++player.frameToken;
    player.index=Math.max(0, Math.min(index, ids.length-1));
    const a=$('#memory-frame-a'); const b=$('#memory-frame-b');
    if(!a||!b) return;
    const next=ids[player.index];
    const incomingImg=player.index%2===1?b:a;
    const outgoingImg=player.index%2===1?a:b;
    const incoming=kenWrap(incomingImg);
    const outgoing=kenWrap(outgoingImg);
    if(!incoming||!outgoing) return;
    const last=player.index>=ids.length-1;
    const ticket=player.generation;
    updatePlayerCaption();
    stopPlayerTimer();
    Promise.resolve(preloadPreview(next)).then(ready=>{
      if(token!==player.frameToken || player.generation!==ticket || !player.open) return null;
      incomingImg.onload=null;
      incomingImg.src=ready.src;
      const decoded=incomingImg.decode?incomingImg.decode():Promise.resolve();
      return decoded.then(()=>ready, ()=>ready);
    }).then(ready=>{
      if(!ready || token!==player.frameToken || player.generation!==ticket || !player.open) return;
      resetKen(incoming);
      applyKen(incoming, incomingImg, player.meta && player.meta[next], player.index);
      incoming.classList.add('is-in');
      if(animate || outgoing.classList.contains('is-in')) outgoing.classList.remove('is-in');
      if(player.index+1<ids.length) preloadPreview(ids[player.index+1]);
      if(player.index+2<ids.length) preloadPreview(ids[player.index+2]);
      if(player.status==='paused') setKenPaused(true);
      schedulePlayerAdvance(last, ticket, token, player.index);
    }).catch(()=>{
      if(token!==player.frameToken || player.generation!==ticket || !player.open) return;
      if(!last) showPlayerFrame(player.index+1, animate);
      else{
        finishMemoryPlayer('最后一张暂时无法显示');
      }
    });
  }
  async function startMemoryPlayer(opened, returnFocus=null){
    player.generation++;
    player.snapshot=opened.snapshot_id;
    player.sourceId=opened.source_group_id||'';
    player.title=opened.title||'';
    player.useB=false;
    player.status='loading';
    player.frameToken=0;
    player.meta={};
    player.preloads=new Map();
    player.returnFocus=returnFocus||document.activeElement;
    const seq=await api('/api/photos?recommendation_snapshot='+encodeURIComponent(opened.snapshot_id)+'&sequence=true&limit=20');
    let raw=opened.highlight_ids||seq.ids||opened.cover_asset_ids||[];
    const isDay=String(opened.kind||'').indexOf('day')>=0 || String(opened.source_group_id||opened.group_id||'').indexOf('day:')>=0;
    if(isDay && opened.date_from && opened.date_to){
      try{
        const day=await api('/api/photos?date_from='+encodeURIComponent(opened.date_from)+'&date_to='+encodeURIComponent(opened.date_to)+'&limit=80');
        const ids=(day.items||[]).map(item=>item.id).filter(Boolean);
        if(ids.length>=7) raw=ids;
      }catch{ /* keep highlight list */ }
    }
    const generation=player.generation;
    const metas=raw.map(id=>({id, captured:0, faces:[], width:0, height:0}));
    metas.forEach(item=>{ player.meta[item.id]=item; });
    const details=Promise.all(raw.map(async id=>{
      try{
        const data=await api('/api/photos/'+id);
        const item={id, captured:parseStamp(data.effective_date||data.captured_at), faces:data.faces||[], width:data.width, height:data.height};
        if(player.generation===generation && player.meta) player.meta[id]=item;
        return item;
      }catch{
        return {id, captured:0, faces:[], width:0, height:0};
      }
    }));
    if(isDay){
      const detailed=await details;
      if(player.generation!==generation) return;
      player.ids=pickDayPlaylist(detailed, player.meta);
      if(player.ids.length<5){
        closeMemoryPlayer();
        if(typeof toast==='function') toast('这一天没有完整的一段回忆');
        return;
      }
    }else{
      player.ids=raw.slice();
      details.then(()=>{
        if(player.generation!==generation || !player.open) return;
        const next=dropLowRes(dedupePlaylist(raw, player.meta), player.meta);
        if(!next.length || next.join(',')===player.ids.join(',')) return;
        const current=player.ids[player.index];
        const anchor=player.index;
        const keptBefore=player.ids.slice(0, anchor).filter(id=>next.indexOf(id)>=0);
        const keptAfter=player.ids.slice(anchor+1).filter(id=>next.indexOf(id)>=0 && id!==current);
        const merged=[];
        keptBefore.concat(current?[current]:[], keptAfter).forEach(id=>{
          if(merged.indexOf(id)<0) merged.push(id);
        });
        if(!merged.length || merged.join(',')===player.ids.join(',')) return;
        player.ids=merged;
        if(player.index!==anchor) return;
        updatePlayerCaption();
      }).catch(()=>{});
    }
    if(!player.ids.length) throw new Error('这条回忆没有照片');
    const el=playerEl();
    if(!el){ state.homeSnapshotId=opened.snapshot_id; state.homeGroupId=opened.group_id; state.homeGroupTitle=opened.title; await setView('home-group'); return; }
    player.open=true;
    document.body.classList.add('is-memory-player');
    el.hidden=false; el.classList.add('is-on');
    el.classList.remove('is-ending');
    el.focus();
    syncMemoryPlayerUI();
    clearMemoryLayers();
    player.preloads=new Map();
    const first=preloadPreview(player.ids[0]).catch(()=>null);
    if(player.ids[1]) preloadPreview(player.ids[1]);
    const intro=player.introShown?Promise.resolve():playOverture();
    player.introShown=true;
    await Promise.all([intro, first]);
    if(!player.open) return;
    hideOverture();
    player.status='playing';
    setMemoryStatus('');
    showPlayerFrame(0, false);
    focusMemoryControl();
  }
  async function openMemorySource(){
    const source=player.sourceId;
    closeMemoryPlayer();
    if(!source) return;
    await openGroup(source);
  }
  async function openCurrentMemoryPhoto(){
    const id=player.ids[player.index];
    if(!id || typeof openPhoto!=='function') return;
    if(player.status==='playing') pauseMemoryPlayer({announce:false});
    else if(player.status==='ended') player.status='paused';
    setMemoryStatus('已暂停');
    syncMemoryPlayerUI();
    state.homeSnapshotId=player.snapshot;
    await openPhoto(id, {q:'', filter:'all', person:'', directory:'', sort:'date_desc', date_from:'', date_to:'', place:'', recommendation_snapshot:player.snapshot});
  }
  async function openGroup(groupId, options={}){
    rememberOpen(groupId);
    const opened=await api('/api/home/groups/open', {method:'POST', body:JSON.stringify({group_id:groupId})});
    if(opened.playable || String(groupId).startsWith('memory:')){
      await startMemoryPlayer(opened, options.returnFocus||null);
      return;
    }
    closeMemoryPlayer();
    state.homeSnapshotId=opened.snapshot_id;
    state.homeGroupId=opened.group_id;
    state.homeGroupTitle=opened.title;
    await setView('home-group');
  }
  function bind(){
    const tabs=$('#home-tabs');
    if(tabs && !tabs.dataset.ready){
      tabs.dataset.ready='1';
      tabs.innerHTML=TABS.map(tab=>'<button type="button" data-home-tab="'+tab.id+'" aria-selected="'+(tab.id==='all'?'true':'false')+'">'+tab.label+'</button>').join('');
      tabs.addEventListener('click', async e=>{
        const tab=e.target.closest('[data-home-tab]');
        if(!tab) return;
        await loadHomeDiscovery({category:tab.dataset.homeTab});
      });
    }
    if(!document.body.dataset.homeImgFallback){
      document.body.dataset.homeImgFallback='1';
      document.addEventListener('error', e=>{
        const img=e.target;
        if(!img || img.tagName!=='IMG' || !img.dataset || !img.dataset.fallback) return;
        if(img.src && img.src.indexOf(img.dataset.fallback)>=0) return;
        img.src=img.dataset.fallback;
      }, true);
    }
    if(!document.body.dataset.memoryKeys){
      document.body.dataset.memoryKeys='1';
      document.addEventListener('keydown', e=>{
        if(!player.open) return;
        if($('#detail-dialog')?.open) return;
        if(e.key==='Tab'){ trapMemoryFocus(e); return; }
        if(e.key==='Escape'){ e.preventDefault(); closeMemoryPlayer(); return; }
        if(player.overtureActive){
          if(e.key==='ArrowRight' || e.key===' ' || e.key==='Enter'){ e.preventDefault(); hideOverture(); }
          return;
        }
        if(isTypingTarget(e.target)) return;
        if((e.key===' ' || e.key==='Enter') && isInteractiveTarget(e.target)) return;
        if(e.key===' ' && player.status!=='ended'){ e.preventDefault(); toggleMemoryPlayer(); return; }
        if(player.status==='ended') return;
        if(e.key==='ArrowRight'){ e.preventDefault(); showPlayerFrame(player.index+1, true); return; }
        if(e.key==='ArrowLeft'){ e.preventDefault(); showPlayerFrame(player.index-1, true); }
      });
    }
    const closeBtn=$('#memory-close');
    if(closeBtn && !closeBtn.dataset.bound){
      closeBtn.dataset.bound='1';
      closeBtn.addEventListener('click', e=>{ e.preventDefault(); e.stopPropagation(); closeMemoryPlayer(); });
    }
    if(!document.body.dataset.homeClicks){
      document.body.dataset.homeClicks='1';
      const onHomeClick=async e=>{
        const open=e.target.closest('[data-home-open]');
        const swap=e.target.closest('[data-home-swap]');
        const hide=e.target.closest('[data-home-hide]');
        const actionToggle=e.target.closest('[data-home-actions-toggle]');
        if(e.target.closest('#memory-close')){ e.preventDefault(); closeMemoryPlayer(); return; }
        if(player.overtureActive && e.target.closest('#memory-player')){ e.preventDefault(); hideOverture(); return; }
        if(e.target.closest('#memory-prev')){ e.preventDefault(); if(player.open){ if(player.status==='ended') player.status='paused'; showPlayerFrame(player.index-1, true);} return; }
        if(e.target.closest('#memory-next')){ e.preventDefault(); if(player.open){ if(player.status==='ended') player.status='paused'; showPlayerFrame(player.index+1, true);} return; }
        if(e.target.closest('#memory-toggle')){ e.preventDefault(); toggleMemoryPlayer(); return; }
        if(e.target.closest('#memory-again')){ e.preventDefault(); if(player.open){ player.status='playing'; setMemoryStatus(''); showPlayerFrame(0, false);} return; }
        if(e.target.closest('#memory-all')){ e.preventDefault(); await openMemorySource(); return; }
        if(e.target.closest('#memory-home')){ e.preventDefault(); closeMemoryPlayer(); return; }
        if(e.target.closest('#memory-open-photo')){ e.preventDefault(); if(player.open) await openCurrentMemoryPhoto(); return; }
        const dot=e.target.closest('#memory-dots [data-memory-index]');
        if(dot){ e.preventDefault(); if(player.status==='ended') player.status='paused'; showPlayerFrame(Number(dot.dataset.memoryIndex), true); return; }
        if(actionToggle){
          e.preventDefault(); e.stopPropagation();
          const tools=actionToggle.closest('.home-card-tools');
          const menu=tools&&tools.querySelector('.home-card-menu');
          const opening=Boolean(menu&&menu.hidden);
          $$('.home-card-menu').forEach(item=>item.hidden=true);
          $$('[data-home-actions-toggle]').forEach(item=>item.setAttribute('aria-expanded','false'));
          if(menu){ menu.hidden=!opening; actionToggle.setAttribute('aria-expanded',String(opening)); if(opening) menu.querySelector('button')?.focus(); }
          return;
        }
        if(swap){ e.preventDefault(); e.stopPropagation(); const cat=swap.dataset.homeSwap; setCursor(cat, nextCursor(cat)+1); replaceSlot(cat); requestAnimationFrame(()=>document.querySelector('[data-home-slot="'+CSS.escape(cat)+'"] [data-home-actions-toggle]')?.focus()); return; }
        if(hide){ e.preventDefault(); e.stopPropagation(); const slotEl=hide.closest('[data-home-slot]'); const slot=slotEl&&slotEl.dataset.homeSlot; hideGroup(hide.dataset.homeHide); if(slot && (state.homeCategory||'all')==='all') replaceSlot(slot); else render(); toast('已暂时隐藏，30 天内不再出现'); return; }
        if(e.target.closest('#home-restore-hidden')){ restoreHidden(); render(); return; }
        if(e.target.closest('#home-retry')){ await loadHomeDiscovery({category:state.homeCategory}); return; }
        if(e.target.closest('#home-refresh-catalog')){ e.preventDefault(); await refreshCatalog(); return; }
        if(e.target.closest('#home-more')){ await loadHomeDiscovery({category:state.homeCategory, cursor:payload&&payload.next_cursor, append:true}); return; }
        if(e.target.closest('[data-view-all]')){ await setView('timeline'); return; }
        if(!e.target.closest('.home-card-tools')){
          $$('.home-card-menu').forEach(item=>item.hidden=true);
          $$('[data-home-actions-toggle]').forEach(item=>item.setAttribute('aria-expanded','false'));
        }
        if(open){
          e.preventDefault();
          if(!open.hasAttribute('tabindex')) open.setAttribute('tabindex','-1');
          open.focus({preventScroll:true});
          await openGroup(open.dataset.homeOpen, {returnFocus:open});
        }
      };
      document.addEventListener('click', typeof action==='function' ? action(onHomeClick) : onHomeClick);
      document.addEventListener('keydown', e=>{
        if(e.key!=='Escape') return;
        const openMenu=document.querySelector('.home-card-menu:not([hidden])');
        if(!openMenu) return;
        e.preventDefault(); e.stopPropagation();
        openMenu.hidden=true;
        const toggle=openMenu.closest('.home-card-tools')?.querySelector('[data-home-actions-toggle]');
        if(toggle){ toggle.setAttribute('aria-expanded','false'); toggle.focus(); }
      });
    }
    const back=$('#home-group-back');
    if(back && !back.dataset.homeReady){
      back.dataset.homeReady='1';
      back.addEventListener('click', async ()=>setView('home'));
    }
  }

  bind();
  global.loadHomeDiscovery=loadHomeDiscovery;
  global.OurTimeHome={load:loadHomeDiscovery, openGroup, closePlayer:closeMemoryPlayer};
  if(document.body.classList.contains('is-home-discovery')){
    loadHomeDiscovery({category:'all'});
  }
})(window);
