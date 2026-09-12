// Browsing order is an immutable list of IDs from the current collection.
// UI redesign: lightweight viewer toolbar, persistent face-label preferences,
// adaptive photo metadata strip. 2026-09-11.

const VIEWER_PREFS_KEY='ourtime.viewer.preferences.v2';
const FACE_LABEL_IMAGES={
  ivory:{
    s:'/api/face-label-bg/1.png',
    m:'/api/face-label-bg/2.png',
    l:'/api/face-label-bg/3.png'
  }
};
const faceLabelThemeReadiness=new Map();
const FACE_STYLE_PRESETS={
  classic:{
    theme:'classic',
    fontSize:13,
    fontFamily:'serif',
    textColor:'#f6f1e6',
    backgroundColor:'#141812',
    backgroundOpacity:.38,
    radius:4,
    paddingX:7,
    paddingY:5,
    shadow:true
  },
  ivory:{
    theme:'ivory',
    fontSize:15,
    fontFamily:'kai',
    textColor:'#4b433a',
    backgroundColor:'#ffffff',
    backgroundOpacity:0,
    radius:0,
    paddingX:0,
    paddingY:0,
    shadow:false
  },
  outline:{
    theme:'outline',
    fontSize:13,
    fontFamily:'serif',
    textColor:'#fbf8f0',
    backgroundColor:'#111410',
    backgroundOpacity:.18,
    radius:4,
    paddingX:8,
    paddingY:6,
    shadow:true
  },
  accent:{
    theme:'accent',
    fontSize:13,
    fontFamily:'serif',
    textColor:'#fbf1e7',
    backgroundColor:'#654740',
    backgroundOpacity:.76,
    radius:5,
    paddingX:8,
    paddingY:6,
    shadow:false
  }
};
const DEFAULT_FACE_STYLE={
  ...FACE_STYLE_PRESETS.classic
};
function readViewerPrefs(){
  try{
    const raw=localStorage.getItem(VIEWER_PREFS_KEY);
    return raw?JSON.parse(raw):{};
  }catch(e){return {};}
}
const storedViewerPrefs=readViewerPrefs();
const LEGACY_FACE_THEMES=new Set(['ink','paper','tea','cinnabar']);
function initialFaceStyle(){
  const stored=storedViewerPrefs.faceStyle;
  if(!stored)return {...DEFAULT_FACE_STYLE};
  if(LEGACY_FACE_THEMES.has(stored.theme))return {...DEFAULT_FACE_STYLE};
  const theme=Object.prototype.hasOwnProperty.call(FACE_STYLE_PRESETS,stored.theme)?stored.theme:'classic';
  return {...FACE_STYLE_PRESETS[theme],...stored,theme};
}
const viewer={
  ids:[],index:0,target:0,offset:0,total:0,context:null,generation:0,busy:false,
  timer:null,playing:false,scale:1,fit:true,loader:null,drafts:new Map(),window:200,
  returnScroll:undefined,openedPhotoId:null,openedAbsolute:null,openedContext:null,openedWaterfallGeneration:null,exit:null,
  faceNames:storedViewerPrefs.faceNames!==false,
  faceAlias:storedViewerPrefs.faceAlias===true,
  faceVertical:storedViewerPrefs.faceVertical!==false,
  faceLabelPosition:['auto','left','right'].includes(storedViewerPrefs.faceLabelPosition)?storedViewerPrefs.faceLabelPosition:'auto',
  faceStyle:initialFaceStyle()
};
function saveViewerPrefs(){
  try{
    localStorage.setItem(VIEWER_PREFS_KEY,JSON.stringify({
      faceNames:viewer.faceNames!==false,
      faceAlias:viewer.faceAlias===true,
      faceVertical:viewer.faceVertical!==false,
      faceLabelPosition:viewer.faceLabelPosition,
      faceStyle:viewer.faceStyle,
      slideDelay:Number($('#slide-delay')?.value||storedViewerPrefs.slideDelay||5)
    }));
  }catch(e){}
}
function injectViewerOverrideCss(){
  if(document.querySelector('link[data-viewer-overrides]'))return;
  const link=document.createElement('link');
  link.rel='stylesheet';
  link.href='/viewer-overrides.css';
  link.dataset.viewerOverrides='true';
  document.head.appendChild(link);
}
injectViewerOverrideCss();

const iconSvg={
  minus:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 12h12"/></svg>',
  plus:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 6v12M6 12h12"/></svg>',
  fit:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3H3v5M16 3h5v5M3 16v5h5M21 16v5h-5"/></svg>',
  actual:'<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 8h8v8H8z"/></svg>',
  person:'<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="3.5"/><path d="M5 20v-1.5a7 7 0 0 1 14 0V20"/></svg>',
  alias:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12 12 4h6l2 2v6l-8 8-8-8Z"/><circle cx="16.5" cy="7.5" r="1"/></svg>',
  horizontal:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 7h14M5 12h10M5 17h14"/></svg>',
  vertical:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 5v14M12 5v10M17 5v14"/></svg>',
  style:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 19 10.5 5h3L19 19M7 14h10"/><circle cx="18.5" cy="6" r="2"/></svg>',
  info:'<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 10v7M12 7h.01"/></svg>',
  play:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 7 8 5-8 5V7Z"/></svg>',
  pause:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 7v10M15 7v10"/></svg>',
  locate:'<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="2"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/></svg>'
};
function setToolIcon(selector,svg,label){
  const el=$(selector); if(!el)return;
  el.innerHTML=svg;
  el.title=label;
  el.setAttribute('aria-label',label);
}
function buildViewerToolbar(){
  const tools=document.querySelector('.viewer-tools');
  if(!tools||tools.dataset.redesigned)return;
  tools.dataset.redesigned='true';

  const zoomGroup=document.createElement('div');zoomGroup.className='viewer-tool-group viewer-tool-zoom';
  const peopleGroup=document.createElement('div');peopleGroup.className='viewer-tool-group viewer-tool-people';
  const actionGroup=document.createElement('div');actionGroup.className='viewer-tool-group viewer-tool-actions';

  ['#zoom-out','#zoom-fit','#zoom-level','#zoom-actual','#zoom-in'].forEach(sel=>{const el=$(sel);if(el)zoomGroup.appendChild(el);});
  ['#toggle-face-names','#toggle-face-alias','#toggle-face-dir'].forEach(sel=>{const el=$(sel);if(el)peopleGroup.appendChild(el);});

  const styleBtn=document.createElement('button');
  styleBtn.type='button';styleBtn.id='face-style-button';styleBtn.setAttribute('aria-expanded','false');
  styleBtn.innerHTML=iconSvg.style;styleBtn.title='人名标签样式';styleBtn.setAttribute('aria-label','人名标签样式');
  peopleGroup.appendChild(styleBtn);

  ['#viewer-info','#viewer-play','#slide-delay','#reveal-button'].forEach(sel=>{const el=$(sel);if(el)actionGroup.appendChild(el);});
  tools.replaceChildren(zoomGroup,peopleGroup,actionGroup);

  setToolIcon('#zoom-out',iconSvg.minus,'缩小');
  setToolIcon('#zoom-fit',iconSvg.fit,'适应窗口');
  setToolIcon('#zoom-actual',iconSvg.actual,'原尺寸 1:1');
  setToolIcon('#zoom-in',iconSvg.plus,'放大');
  setToolIcon('#toggle-face-names',iconSvg.person,'显示/隐藏人名');
  setToolIcon('#toggle-face-alias',iconSvg.alias,'显示/隐藏别名');
  setToolIcon('#viewer-info',iconSvg.info,'详细资料');
  setToolIcon('#viewer-play',iconSvg.play,'播放幻灯片');
  setToolIcon('#reveal-button',iconSvg.locate,'在资源管理器中定位');

  const delay=$('#slide-delay');
  if(delay){
    const saved=Number(storedViewerPrefs.slideDelay);
    if([3,5,10].includes(saved))delay.value=String(saved);
    delay.title='幻灯片切换间隔';
  }
  buildFaceStylePopover();
}
function buildFaceStylePopover(){
  if($('#face-style-popover'))return;
  const body=document.querySelector('.viewer-body'); if(!body)return;
  const pop=document.createElement('section');
  pop.id='face-style-popover';pop.className='face-style-popover';pop.hidden=true;
  pop.setAttribute('aria-label','人名标签样式设置');
  pop.innerHTML=`
    <div class="face-style-head"><b>人名标签</b><button type="button" id="face-style-close" aria-label="关闭">×</button></div>
    <div class="face-style-preview"><span id="face-style-preview-label">示例姓名</span></div>
    <label>风格 <select id="face-theme"><option value="classic">原始</option><option value="ivory">素笺</option><option value="outline">线框</option><option value="accent">暗朱</option></select></label>
    <label class="face-custom-control">字号 <output id="face-font-size-value"></output><input id="face-font-size" type="range" min="10" max="22" step="1"></label>
    <label class="face-custom-control">字体 <select id="face-font-family"><option value="sans">黑体 / 无衬线</option><option value="serif">宋体 / 衬线</option><option value="kai">楷体</option><option value="fangsong">仿宋</option></select></label>
    <label>标签位置 <select id="face-label-position"><option value="auto">自动</option><option value="left">优先左侧</option><option value="right">优先右侧</option></select></label>
    <div class="face-style-colors"><label>文字<input id="face-text-color" type="color"></label><label>底色<input id="face-bg-color" type="color"></label></div>
    <label class="face-custom-control">底色透明度 <output id="face-bg-opacity-value"></output><input id="face-bg-opacity" type="range" min="0" max="90" step="1"></label>
    <label class="face-custom-control">圆角 <select id="face-radius"><option value="0">直角</option><option value="4">微圆角</option><option value="10">圆角</option><option value="999">胶囊</option></select></label>
    <label class="face-shadow-row"><input id="face-shadow" type="checkbox"> 文字阴影（亮背景更清楚）</label>
    <button type="button" id="face-style-reset" class="face-style-reset">恢复默认</button>`;
  body.appendChild(pop);

  const bind=(id,event,fn)=>{const el=$(id);if(el)el.addEventListener(event,fn);};
  bind('#face-style-close','click',()=>toggleFaceStylePopover(false));
  bind('#face-theme','change',e=>applyFacePreset(e.target.value));
  bind('#face-font-size','input',e=>updateFaceStyle({fontSize:Number(e.target.value)}));
  bind('#face-font-family','change',e=>updateFaceStyle({fontFamily:e.target.value}));
  bind('#face-label-position','change',e=>{viewer.faceLabelPosition=e.target.value;saveViewerPrefs();if(state.detail)renderFaceNames(state.detail);});
  bind('#face-text-color','input',e=>updateFaceStyle({textColor:e.target.value}));
  bind('#face-bg-color','input',e=>updateFaceStyle({backgroundColor:e.target.value}));
  bind('#face-bg-opacity','input',e=>updateFaceStyle({backgroundOpacity:Number(e.target.value)/100}));
  bind('#face-radius','change',e=>updateFaceStyle({radius:Number(e.target.value)}));
  bind('#face-shadow','change',e=>updateFaceStyle({shadow:e.target.checked}));
  bind('#face-style-reset','click',()=>{viewer.faceStyle={...DEFAULT_FACE_STYLE};viewer.faceLabelPosition='auto';applyFaceStyle();saveViewerPrefs();if(state.detail)renderFaceNames(state.detail);});
  applyFaceStyle();
}
function applyFacePreset(name){
  const valid=Object.prototype.hasOwnProperty.call(FACE_STYLE_PRESETS,name);
  const theme=valid?name:'classic';
  viewer.faceStyle={...FACE_STYLE_PRESETS[theme]};
  if(FACE_LABEL_IMAGES[theme])viewer.faceVertical=true;
  applyFaceStyle();
  saveViewerPrefs();
  if(state.detail)requestAnimationFrame(()=>renderFaceNames(state.detail));
}
function faceFontStack(kind){
  if(kind==='sans')return '"Microsoft YaHei UI","Microsoft YaHei","PingFang SC","Noto Sans CJK SC",sans-serif';
  if(kind==='kai')return '"LXGW WenKai","STKaiti","Kaiti SC","KaiTi",serif';
  if(kind==='fangsong')return '"FangSong","STFangsong","FangSong_GB2312","Songti SC","STSong","SimSun",serif';
  return '"Iowan Old Style","Palatino Linotype","STSong","SimSun",serif';
}
function faceLabelProfile(name){
  const normalized=String(name||'').replace(/\s+/g,'');
  const count=[...normalized].length;
  return {count,size:count<=2?'s':count===3?'m':'l',long:count>=5};
}
function applyFaceLabelProfile(element,name){
  if(!element)return;
  const profile=faceLabelProfile(name);
  element.dataset.faceLabelSize=profile.size;
  element.classList.toggle('long-name',profile.long);
}
function faceLabelsVertical(){
  return Boolean(FACE_LABEL_IMAGES[viewer.faceStyle?.theme])||viewer.faceVertical!==false;
}
function faceLabelThemeReady(theme){
  if(!FACE_LABEL_IMAGES[theme])return Promise.resolve(true);
  if(faceLabelThemeReadiness.has(theme))return faceLabelThemeReadiness.get(theme);
  const promise=Promise.all(Object.values(FACE_LABEL_IMAGES[theme]).map(src=>new Promise(resolve=>{
    const image=new Image();
    image.onload=()=>resolve(true);
    image.onerror=()=>resolve(false);
    image.src=src;
  }))).then(results=>results.every(Boolean));
  faceLabelThemeReadiness.set(theme,promise);
  return promise;
}
function ensureFaceLabelThemeAvailable(theme){
  if(!FACE_LABEL_IMAGES[theme])return;
  faceLabelThemeReady(theme).then(ready=>{
    if(ready||viewer.faceStyle?.theme!==theme)return;
    console.warn(`人名标签主题 ${theme} 的底图缺失，已恢复原始样式`);
    viewer.faceStyle={...DEFAULT_FACE_STYLE};
    applyFaceStyle();
    saveViewerPrefs();
    if(state.detail)requestAnimationFrame(()=>renderFaceNames(state.detail));
  });
}
function applyFaceStyle(){
  const root=$('#detail-dialog')||document.documentElement;
  const s=viewer.faceStyle||DEFAULT_FACE_STYLE;
  const theme=Object.prototype.hasOwnProperty.call(FACE_STYLE_PRESETS,s.theme)?s.theme:'classic';
  root.dataset.faceTheme=theme;
  const pop=$('#face-style-popover');
  if(pop)pop.dataset.faceTheme=theme;
  root.style.setProperty('--face-font-size',`${s.fontSize}px`);
  root.style.setProperty('--face-font-family',faceFontStack(s.fontFamily));
  root.style.setProperty('--face-text-color',s.textColor);
  root.style.setProperty('--face-bg-color',s.backgroundColor);
  root.style.setProperty('--face-bg-opacity',String(s.backgroundOpacity));
  root.style.setProperty('--face-bg-rgba',hexToRgba(s.backgroundColor,s.backgroundOpacity));
  root.style.setProperty('--face-radius',s.radius>=999?'999px':`${s.radius}px`);
  root.style.setProperty('--face-padding-x',`${s.paddingX}px`);
  root.style.setProperty('--face-padding-y',`${s.paddingY}px`);
  root.style.setProperty('--face-shadow',s.shadow?'0 1px 10px rgba(0,0,0,.65)':'none');
  const themeImages=FACE_LABEL_IMAGES[theme];
  if(themeImages){
    Object.entries(themeImages).forEach(([size,url])=>root.style.setProperty(`--face-label-${size}-image`,`url("${url}")`));
  }

  const themeSelect=$('#face-theme'),fontSize=$('#face-font-size'),family=$('#face-font-family'),position=$('#face-label-position'),text=$('#face-text-color'),bg=$('#face-bg-color'),op=$('#face-bg-opacity'),radius=$('#face-radius'),shadow=$('#face-shadow');
  if(themeSelect)themeSelect.value=theme;
  if(fontSize)fontSize.value=String(s.fontSize);
  if(family)family.value=s.fontFamily;
  if(position)position.value=viewer.faceLabelPosition;
  if(text)text.value=s.textColor;
  if(bg)bg.value=s.backgroundColor;
  if(op)op.value=String(Math.round(s.backgroundOpacity*100));
  if(radius)radius.value=String(s.radius);
  if(shadow)shadow.checked=!!s.shadow;
  const themeLocksCustomStyle=Boolean(themeImages);
  [fontSize,family,text,bg,op,radius,shadow].forEach(control=>{
    if(control)control.disabled=themeLocksCustomStyle;
  });
  if(pop){
    pop.querySelectorAll('.face-custom-control,.face-style-colors,.face-shadow-row').forEach(section=>{
      section.classList.toggle('is-disabled',themeLocksCustomStyle);
    });
  }
  if($('#face-font-size-value'))$('#face-font-size-value').textContent=`${s.fontSize}px`;
  if($('#face-bg-opacity-value'))$('#face-bg-opacity-value').textContent=`${Math.round(s.backgroundOpacity*100)}%`;
  const preview=$('#face-style-preview-label');
  if(preview){
    const currentFaces=typeof state!=='undefined'?namedFaces(state.detail):[];
    preview.textContent=currentFaces[0]?.name||'示例姓名';
    applyFaceLabelProfile(preview,preview.textContent);
    preview.style.fontSize=themeImages?'':`${s.fontSize}px`;
    preview.style.fontFamily=themeImages?'':faceFontStack(s.fontFamily);
    preview.style.color=themeImages?'':s.textColor;
    preview.style.backgroundColor=themeImages?'':hexToRgba(s.backgroundColor,s.backgroundOpacity);
    preview.style.borderRadius=themeImages?'':s.radius>=999?'999px':`${s.radius}px`;
    preview.style.textShadow=themeImages?'':s.shadow?'0 1px 10px rgba(0,0,0,.65)':'none';
    preview.style.writingMode=faceLabelsVertical()?'vertical-rl':'horizontal-tb';
    preview.style.textOrientation=faceLabelsVertical()?'upright':'mixed';
  }
  ensureFaceLabelThemeAvailable(theme);
}
function hexToRgba(hex,alpha){
  const h=String(hex||'#000000').replace('#','');
  const full=h.length===3?h.split('').map(x=>x+x).join(''):h.padEnd(6,'0').slice(0,6);
  const n=parseInt(full,16);if(!Number.isFinite(n))return `rgba(0,0,0,${alpha})`;
  return `rgba(${(n>>16)&255},${(n>>8)&255},${n&255},${alpha})`;
}
function updateFaceStyle(patch){
  viewer.faceStyle={...viewer.faceStyle,...patch};applyFaceStyle();saveViewerPrefs();
  if(state.detail)requestAnimationFrame(()=>renderFaceNames(state.detail));
}
function toggleFaceStylePopover(force){
  const pop=$('#face-style-popover'),btn=$('#face-style-button');if(!pop)return;
  const open=force==null?pop.hidden:!!force;
  pop.hidden=!open;if(btn)btn.setAttribute('aria-expanded',String(open));
  if(open)applyFaceStyle();
}
function updateToolVisuals(){
  const dir=$('#toggle-face-dir');
  if(dir){
    const fixedVertical=Boolean(FACE_LABEL_IMAGES[viewer.faceStyle?.theme]);
    const vertical=faceLabelsVertical();
    dir.innerHTML=vertical?iconSvg.vertical:iconSvg.horizontal;
    const next=fixedVertical?'素笺使用竖排':vertical?'切换为横排':'切换为竖排';
    dir.title=next;dir.setAttribute('aria-label',next);
  }
  const play=$('#viewer-play');
  if(play){
    play.innerHTML=viewer.playing?iconSvg.pause:iconSvg.play;
    const label=viewer.playing?'暂停幻灯片':'播放幻灯片';play.title=label;play.setAttribute('aria-label',label);
  }
}

function currentBrowseContext(){return {q:state.q,filter:state.view,person:state.person,directory:state.directory,sort:state.sort,date_from:state.dateFrom||'',date_to:state.dateTo||'',place:state.place||'',max_id:state.maxId};}
function browseContextKey(context={}){
 const filter=String(context.filter||'all');
 return JSON.stringify({q:String(context.q||''),filter:filter==='all'?'timeline':filter,person:String(context.person||''),directory:String(context.directory||''),sort:String(context.sort||'date_desc'),date_from:String(context.date_from||''),date_to:String(context.date_to||''),place:String(context.place||''),max_id:Number(context.max_id||0)});
}
function sameBrowseContext(a,b){return browseContextKey(a)===browseContextKey(b);}
function contextLabel(c){const person=state.people.find(p=>String(p.id)===c.person);const filter=c.filter||'';const heading=c.person?(person?.name||'人物 '+c.person):filter.startsWith('year:')?(filter.slice(5)==='unknown'?'时间未记录':filter.slice(5)+' 年'):filter.startsWith('group:')?(filter.slice(6)==='10plus'?'10人及以上':Number(filter.slice(6))+'人合影'):filter.startsWith('place:')?(filter.slice(6)==='unknown'?'地点未记录':filter.slice(6)):titles[filter]?.[0]||'照片';return [heading,c.directory?basename(c.directory):'',c.place?prettyPlace(c.place):'',c.date_from||c.date_to?((c.date_from||'')+(c.date_to&&c.date_to!==c.date_from?' ~ '+c.date_to:'')):'',c.q?'搜索：'+c.q:''].filter(Boolean).join(' · ');}
function viewerMessage(text){$('#viewer-message').textContent=text;}
function setViewerLoading(loading){
 const dialog=$('#detail-dialog'),img=$('#detail-img'),signature=$('#photo-signature'),faces=$('#face-name-layer'),stage=document.querySelector('.viewer-stage');
 if(!dialog)return;
 let indicator=$('#viewer-loading');
 if(!indicator&&stage){indicator=document.createElement('div');indicator.id='viewer-loading';indicator.textContent='加载中…';indicator.setAttribute('aria-live','polite');stage.appendChild(indicator);}
 dialog.classList.toggle('is-loading',Boolean(loading));
 if(indicator)indicator.hidden=!loading;
 if(loading){
  if(img && !img.getAttribute('src')) img.hidden=true;
  if(signature){signature.hidden=true;signature.replaceChildren();}
  if(faces){faces.hidden=true;faces.replaceChildren();}
  viewerMessage('');
 }else{
  if(img)img.hidden=false;
  if(signature)signature.hidden=false;
 }
}
function waitViewerFrames(count=2){return new Promise(resolve=>{const next=()=>count--<=0?resolve():requestAnimationFrame(next);next();});}
function stopSlides(){viewer.playing=false;clearTimeout(viewer.timer);viewer.timer=null;syncViewerTools();}
function pressTool(id, on){const el=$(id); if(!el)return; el.setAttribute('aria-pressed', String(!!on));}
function syncViewerTools(){
 pressTool('#toggle-face-names', viewer.faceNames!==false);
 pressTool('#toggle-face-alias', viewer.faceAlias===true);
 pressTool('#toggle-face-dir', viewer.faceVertical!==false);
 pressTool('#viewer-info', !$('#detail-dialog').classList.contains('hide-info'));
 pressTool('#viewer-play', viewer.playing);
 const aliasBtn=$('#toggle-face-alias'); if(aliasBtn) aliasBtn.disabled=viewer.faceNames===false;
 const dirBtn=$('#toggle-face-dir'); if(dirBtn) dirBtn.disabled=viewer.faceNames===false||Boolean(FACE_LABEL_IMAGES[viewer.faceStyle?.theme]);
 const styleBtn=$('#face-style-button');if(styleBtn)styleBtn.disabled=viewer.faceNames===false;
 updateToolVisuals();
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
 const signature=$('#photo-signature');
 const signatureHeight=signature&&!signature.hidden?Number(getComputedStyle(document.documentElement).getPropertyValue('--viewer-signature-h').replace('px',''))||68:0;
 if(viewer.fit){
  const width=Math.max(40, area.clientWidth);
  const height=Math.max(40, area.clientHeight-signatureHeight);
  viewer.scale=Math.max(.01, Math.min(width/img.naturalWidth, height/img.naturalHeight));
 }
 const w=Math.max(1,Math.round(img.naturalWidth*viewer.scale));
 const h=Math.max(1,Math.round(img.naturalHeight*viewer.scale));
 mat.classList.toggle('compact-signature',w<560);
 area.classList.toggle('zoomed', !viewer.fit);
 mat.style.width=w+'px';
 img.style.width=w+'px';
 img.style.height=h+'px';
 $('#zoom-level').textContent=Math.round(viewer.scale*100)+'%';
 if(reset){area.scrollTop=0;area.scrollLeft=0;}
 if(state.detail && viewer.faceNames!==false) requestAnimationFrame(()=>renderFaceNames(state.detail));
}
function renderSignature(a,file){
 const signature=$('#photo-signature');if(!signature)return;
 const tags=a.metadata?.ExifTool||{};
 const tag=name=>Object.entries(tags).find(([key])=>key.split(':').at(-1)===name)?.[1];
 const clean=value=>String(value||'').replace(/\0/g,'').trim();
 const number=name=>{const raw=tag(name);const n=Number(raw);return Number.isFinite(n)&&n>0?n:null;};
 const make=clean(tag('Make')),model=clean(tag('Model'));
 const camera=model?(make&&model.toLowerCase().startsWith(make.toLowerCase())?model:[make,model].filter(Boolean).join(' ')):clean(a.camera);
 const source=a.effective_source||'';
 const timeKind=source.includes('修改')?'文件时间参考':source.includes('推测')?'推测时间':source==='人工确认'?'补录时间':'拍摄时间';
 const rawDate=clean(a.effective_date);
 const shownDate=/^\d{4}-\d\d-\d\dT/.test(rawDate)?rawDate.slice(0,16).replaceAll('-','.').replace('T','  '):rawDate;
 const place=prettyPlace(a.effective_place)||'';
 const focal=number('FocalLengthIn35mmFormat')||number('FocalLength');
 const aperture=number('FNumber'),shutter=number('ExposureTime'),iso=number('ISO');
 const exposure=[];
 if(focal)exposure.push(`${Number(focal.toFixed(1))}mm`);
 if(aperture)exposure.push(`ƒ/${Number(aperture.toFixed(1))}`);
 if(shutter)exposure.push(shutter<1?`1/${Math.round(1/shutter)}s`:`${Number(shutter.toFixed(2))}s`);
 if(iso)exposure.push(`ISO ${Math.round(iso)}`);
 const size=Number(file?.size);
 const fileSize=Number.isFinite(size)&&size>0?(size>=1024*1024?(size/1024/1024).toFixed(1)+' MB':(size/1024).toFixed(0)+' KB'):'';
 const dimensions=(Number(a.width)>0&&Number(a.height)>0)?`${a.width} × ${a.height}`:'';
 const format=clean(a.format)||clean(file?.path?.split('.').pop()?.toUpperCase());
 const basics=[dimensions,format,fileSize].filter(Boolean);
 const filename=file?.path?basename(file.path):'';
 const hasMemory=Boolean(shownDate||place);
 const hasCapture=Boolean(camera||exposure.length);

 const mainValue=shownDate||place||(hasCapture?(camera||'拍摄信息'):(filename||'图片'));
 const mainKind=shownDate?timeKind:(!hasMemory&&!hasCapture?'基础文件信息':(!shownDate&&place?'地点':''));
 const placeMarkup=shownDate&&place?`<span id="signature-place" class="signature-place">${esc(place)}</span>`:'';
 const inlineMarkup=!hasMemory&&hasCapture&&exposure.length?`<span id="signature-primary-inline" class="signature-inline">${exposure.map(esc).join(' · ')}</span>`:'';
 const secondaryBits=hasMemory?[camera,...exposure].filter(Boolean):basics;
 const rightBits=hasMemory?basics:[];
 const secondaryMarkup=secondaryBits.map(v=>`<span>${esc(v)}</span>`).join('');
 const rightMarkup=rightBits.join(' · ');

 signature.innerHTML=`
  <div class="signature-v2">
   <span class="signature-seal-v2" aria-hidden="true">拾</span>
   <div id="signature-primary" class="signature-primary">
    <strong id="signature-primary-value">${esc(mainValue)}</strong>
    <small id="signature-primary-kind"${mainKind?'':' hidden'}>${esc(mainKind)}</small>
    ${placeMarkup}
    ${inlineMarkup}
   </div>
   <div id="signature-settings" class="signature-secondary"${secondaryBits.length?'':' hidden'}>${secondaryMarkup}</div>
   <div id="signature-format" class="signature-file"${rightBits.length?'':' hidden'}>${esc(rightMarkup)}</div>
  </div>`;
 signature.hidden=false;
 signature.title=[shownDate&&`${timeKind}：${rawDate}`,place&&`地点：${place}`,camera&&`设备：${camera}`,exposure.join(' · '),basics.join(' · ')].filter(Boolean).join('\n');
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
  return '+';
}
function rectsOverlap(a,b,gap=6){
  return a.x<b.x+b.w+gap && a.x+a.w+gap>b.x && a.y<b.y+b.h+gap && a.y+a.h+gap>b.y;
}
function rectOverlapArea(a,b){
 const w=Math.max(0,Math.min(a.x+a.w,b.x+b.w)-Math.max(a.x,b.x));
 const h=Math.max(0,Math.min(a.y+a.h,b.y+b.h)-Math.max(a.y,b.y));
 return w*h;
}
function rectOverflow(rect,layerW,layerH,pad=4){
 return Math.max(0,pad-rect.x)+Math.max(0,pad-rect.y)+Math.max(0,rect.x+rect.w-(layerW-pad))+Math.max(0,rect.y+rect.h-(layerH-pad));
}
function faceLabelDistance(rect,item){
 const dx=Math.max(item.fx-(rect.x+rect.w),rect.x-(item.fx+item.fw),0);
 const dy=Math.max(item.fy-(rect.y+rect.h),rect.y-(item.fy+item.fh),0);
 return dx+dy;
}
function compareFaceLabelScore(a,b){
 for(let i=0;i<a.length;i++)if(a[i]!==b[i])return a[i]-b[i];
 return 0;
}
function clampFaceLabel(rect, layerW, layerH, pad=6){
  rect.x=Math.min(Math.max(pad, rect.x), Math.max(pad, layerW-rect.w-pad));
  rect.y=Math.min(Math.max(pad, rect.y), Math.max(pad, layerH-rect.h-pad));
  return rect;
}
function faceLabelFits(rect, layerW, layerH, pad=4){
  return rect.x>=pad && rect.y>=pad && rect.x+rect.w<=layerW-pad && rect.y+rect.h<=layerH-pad;
}
function faceLabelCandidate(item, side, vertical, width, height, offset, gap=6){
  if(vertical){
    return {
      x:side==='left'?item.fx-width-gap:item.fx+item.fw+gap,
      y:item.fy+item.fh/2-height/2+offset,
      w:width,h:height,side,btn:item.btn
    };
  }
  const y=side==='top'?item.fy-height-gap:item.fy+item.fh+gap;
  return {x:item.cx-width/2,y:y+offset,w:width,h:height,side,btn:item.btn};
}
function faceLabelSide(items, ox, imgW){
  const forced=viewer.faceLabelPosition;
  if(forced==='left'||forced==='right')return forced;
  const groupMin=Math.min(...items.map(item=>item.fx));
  const groupMax=Math.max(...items.map(item=>item.fx+item.fw));
  const leftSpace=Math.max(0,groupMin-ox);
  const rightSpace=Math.max(0,ox+imgW-groupMax);
  if(leftSpace!==rightSpace)return leftSpace>rightSpace?'left':'right';
  const leftMin=Math.min(...items.map(item=>item.fx-ox));
  const rightMin=Math.min(...items.map(item=>ox+imgW-item.fx-item.fw));
  return leftMin>=rightMin?'left':'right';
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
  items.sort((a,b)=>a.cy-b.cy||a.cx-b.cx);
  layer.innerHTML=items.map(it=>`<button type="button" class="face-name${it.named?'':' unnamed'}" data-face-person="${it.face.person_id}"${it.named?'':' title="命名人物" aria-label="命名人物"'}>${esc(it.label)}</button>`).join('');
  const buttons=[...layer.querySelectorAll('.face-name')];
  const vertical=faceLabelsVertical();
  const placed=[];
  const gap=6;
  const preferredSide=vertical?faceLabelSide(items,ox,imgW):'bottom';
  const offsets=[0,-12,12,-24,24,-36,36];
  const faceRects=items.map(item=>({x:item.fx,y:item.fy,w:item.fw,h:item.fh}));
  buttons.forEach((btn,i)=>{
    const it=items[i];
    it.btn=btn;
    if(it.named)applyFaceLabelProfile(btn,it.label);
    const w=Math.max(18, btn.offsetWidth);
    const h=Math.max(18, btn.offsetHeight);
    const sides=vertical?[preferredSide,preferredSide==='left'?'right':'left']:[preferredSide,'top'];
    let chosen=null;
    for(const side of sides){
      for(const offset of offsets){
        const candidate=faceLabelCandidate(it,side,vertical,w,h,offset,gap);
        const hitsLabel=placed.some(other=>rectsOverlap(candidate,other,3));
        const hitsFace=faceRects.some((faceRect,faceIndex)=>faceIndex!==i&&rectsOverlap(candidate,faceRect,3));
        if(faceLabelFits(candidate,layerW,layerH,4)&&!hitsLabel&&!hitsFace){chosen=candidate;break;}
      }
      if(chosen)break;
    }
    if(!chosen){
      let best=null;let bestScore=null;
      for(const side of sides){
        for(const offset of offsets){
          const candidate=faceLabelCandidate(it,side,vertical,w,h,offset,gap);
          const otherFaceOverlap=faceRects.reduce((sum,faceRect,faceIndex)=>sum+(faceIndex===i?0:rectOverlapArea(candidate,faceRect)),0);
          const placedOverlap=placed.reduce((sum,other)=>sum+rectOverlapArea(candidate,other),0);
          const score=[rectOverflow(candidate,layerW,layerH,4),otherFaceOverlap,placedOverlap,side===preferredSide?0:1,faceLabelDistance(candidate,it)];
          if(!bestScore||compareFaceLabelScore(score,bestScore)<0){best=candidate;bestScore=score;}
        }
      }
      chosen=best||faceLabelCandidate(it,sides[0],vertical,w,h,0,gap);
      clampFaceLabel(chosen,layerW,layerH,4);
    }
    placed.push(chosen);
    btn.classList.add(chosen.side);
    if(!it.named)btn.setAttribute('title','命名人物');
  });
  placed.forEach(rect=>{
    rect.btn.style.left=Math.round(rect.x)+'px';
    rect.btn.style.top=Math.round(rect.y)+'px';
  });
}
function renderFaceNames(photo){
 const layer=$('#face-name-layer'); if(!layer)return;
 const show=viewer.faceNames!==false;
 const alias=viewer.faceAlias===true;
  const vertical=faceLabelsVertical();
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
 const ticket=renderPhoto.ticket+1;
 const keep=Boolean($('#detail-img')&&$('#detail-img').getAttribute('src')); setViewerLoading(!keep);
 if(viewer.loader){viewer.loader.onload=null;viewer.loader.onerror=null;viewer.loader.src='';}
 try{
  if(!await renderPhoto(id))return false;
 }catch(err){
  if(ticket===renderPhoto.ticket){viewerMessage(err.message||'照片资料读取失败');setViewerLoading(false);}
  throw err;
 }
 if(ticket!==renderPhoto.ticket)return false;
 const root=(viewer.context?.directory||'').replace(/[\/]+$/,'').toLowerCase();
 const files=[...state.detail.files].sort((a,b)=>a.excluded-b.excluded||b.exists_now-a.exists_now||a.id-b.id);
 const scoped=files.find(f=>!root||f.path.toLowerCase().startsWith(root+String.fromCharCode(92))||f.path.toLowerCase()===root);
 if(scoped)$('#detail-name').textContent=basename(scoped.path);
 $('#detail-dialog').dataset.photoId=String(id);
 const draft=viewer.drafts.get(id);if(draft)for(const [key,value] of Object.entries(draft))$(key).value=value;
 const file=scoped||files[0];
 const src=viewerImageSrc(state.detail, file, id);
 const applySrc=async(url)=>{
  if(!$('#detail-dialog').open||state.detail?.id!==id||ticket!==renderPhoto.ticket)return false;
  viewer.fit=true;
  $('#detail-img').src=url;
  $('#detail-img').hidden=false;
  $('#photo-signature').hidden=false;
  $('#face-name-layer').hidden=false;
  renderSignature(state.detail,scoped||files[0]);
  updateZoom(true);
  renderFaceNames(state.detail);
  viewerMessage(draft?'这张照片有尚未保存的补录，已暂存在本页。':!state.detail.in_library?'已排除 · 缓存已清理':'');
  await waitViewerFrames(2);
  if(ticket===renderPhoto.ticket&&$('#detail-dialog').open&&state.detail?.id===id)setViewerLoading(false);
  return true;
 };
 updatePosition();
 const image=new Image();viewer.loader=image;
 await new Promise(resolve=>{
  image.onload=async()=>{try{if(typeof image.decode==='function')await image.decode();}catch(e){}await applySrc(image.src);resolve();};
  image.onerror=()=>{
   if(!src.includes('/api/preview/')){
    const fallback=new Image(); viewer.loader=fallback;
    fallback.onload=async()=>{try{if(typeof fallback.decode==='function')await fallback.decode();}catch(e){}await applySrc(fallback.src);resolve();};
    fallback.onerror=()=>{if(ticket===renderPhoto.ticket){viewerMessage('这张图暂时无法显示');setViewerLoading(false);}resolve();};
    fallback.src='/api/preview/'+id+'?v='+state.thumbRevision;
    return;
   }
   if(ticket===renderPhoto.ticket){viewerMessage('这张图暂时无法显示');setViewerLoading(false);}
   resolve();
  };
  image.src=src;
 });
 return ticket===renderPhoto.ticket;
}
async function openPhoto(id,context=null){
 const wasOpen=$('#detail-dialog').open;
 if(!wasOpen){viewer.returnScroll=scrollY;viewer.openedPhotoId=null;viewer.openedAbsolute=null;viewer.openedContext=null;viewer.openedWaterfallGeneration=null;viewer.exit=null;}
 stopSlides();
 const alreadyOpen=wasOpen;
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
  if(!alreadyOpen){
   viewer.openedPhotoId=Number(id);
   viewer.openedAbsolute=viewer.absolute;
   viewer.openedContext={...viewer.context};
   viewer.openedWaterfallGeneration=typeof waterfall==='object'?waterfall.generation:null;
  }
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
   if(generation!==viewer.generation){continue;}
   if(local==null){if(generation!==viewer.generation) continue;return;}
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
  if($('#detail-dialog').open && Number.isFinite(viewer.goal) && viewer.goal!==viewer.absolute){consumeViewerGoal(automatic);}
 }
}

buildViewerToolbar();
applyFaceStyle();
syncViewerTools();

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
$('#toggle-face-names').addEventListener('click',()=>{viewer.faceNames=!viewer.faceNames;saveViewerPrefs();renderFaceNames(state.detail);});
$('#toggle-face-alias').addEventListener('click',()=>{viewer.faceAlias=!viewer.faceAlias;saveViewerPrefs();renderFaceNames(state.detail);});
$('#toggle-face-dir').addEventListener('click',()=>{viewer.faceVertical=!viewer.faceVertical;saveViewerPrefs();renderFaceNames(state.detail);if(!$('#face-style-popover')?.hidden)applyFaceStyle();});
$('#face-style-button')?.addEventListener('click',e=>{e.stopPropagation();toggleFaceStylePopover();});
$('#detail-img').addEventListener('dblclick',()=>{if(viewer.fit)zoomTo(1);else{viewer.fit=true;updateZoom(true);}});
function toggleInfo(){$('#detail-dialog').classList.toggle('hide-info');syncViewerTools();}
$('#viewer-info').addEventListener('click',toggleInfo);
$('#close-info').addEventListener('click',toggleInfo);
$('#viewer-play').addEventListener('click',()=>{if(viewer.playing){stopSlides();syncViewerTools();return;}const current=Number.isFinite(viewer.absolute)?viewer.absolute:(viewer.offset||0)+(viewer.target||0);if(current>=(viewer.total||viewer.ids.length)-1){viewerMessage('已经是最后一张，请先返回前面的照片。');return;}viewer.playing=true;syncViewerTools();scheduleSlide();});
$('#slide-delay').addEventListener('change',()=>{saveViewerPrefs();if(viewer.timer)scheduleSlide();});
$('#detail-dialog').addEventListener('close',()=>{viewer.exit={photoId:Number(state.detail?.id)||0,absolute:Number(viewer.absolute),context:viewer.context?{...viewer.context}:null,fallbackScroll:viewer.returnScroll,openedPhotoId:Number(viewer.openedPhotoId)||0,openedAbsolute:Number(viewer.openedAbsolute),openedContext:viewer.openedContext?{...viewer.openedContext}:null,waterfallGeneration:viewer.openedWaterfallGeneration};stopSlides();toggleFaceStylePopover(false);viewer.generation++;viewer.queued=null;viewer.goal=null;renderPhoto.ticket++;if(viewer.loader){viewer.loader.onload=null;viewer.loader.onerror=null;viewer.loader.src='';}if(document.fullscreenElement)document.exitFullscreen().catch(()=>{});});
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
const keepOpenSelector='#detail-img,button,a,input,textarea,select,.detail-info,.face-name-layer,.face-name,.viewer-tools,.viewer-arrow,.dialog-close,#photo-mat,#photo-signature,.face-style-popover';
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
