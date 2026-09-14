/* One-photo export from the viewer's already rendered layout. */
(()=>{
  'use strict';
  const $=window.$||((selector)=>document.querySelector(selector));
  const exportButton=()=>document.querySelector('#export-annotated-photo');
  const EXPORT_STYLE_VARS=['--face-font-size','--face-font-family','--face-text-color','--face-bg-color','--face-bg-opacity','--face-bg-rgba','--face-radius','--face-padding-x','--face-padding-y','--face-shadow','--face-label-s-image','--face-label-m-image','--face-label-l-image','--viewer-signature-h','--viewer-image-inset','--signature-tone'];
  const FROZEN_STYLE_PROPS=[
    'display','visibility','position','inset','left','top','right','bottom','z-index',
    'width','height','min-width','min-height','max-width','max-height','box-sizing',
    'margin','margin-top','margin-right','margin-bottom','margin-left',
    'padding','padding-top','padding-right','padding-bottom','padding-left',
    'border','border-width','border-style','border-color','border-radius',
    'border-top','border-right','border-bottom','border-left',
    'background','background-color','background-image','background-size',
    'background-position','background-repeat','box-shadow','opacity',
    'color','font','font-family','font-size','font-style','font-weight',
    'font-variant','font-stretch','line-height','letter-spacing','word-spacing',
    'text-align','text-decoration','text-transform','text-shadow',
    'white-space','word-break','overflow-wrap','writing-mode','text-orientation',
    'vertical-align','overflow','overflow-x','overflow-y',
    'flex','flex-basis','flex-direction','flex-grow','flex-shrink','flex-wrap',
    'grid','grid-template-columns','grid-template-rows','grid-column','grid-row',
    'gap','column-gap','row-gap','align-content','align-items','align-self',
    'justify-content','justify-items','justify-self','place-content','place-items',
    'transform','transform-origin','object-fit','object-position',
    'fill','fill-opacity','stroke','stroke-width','stroke-linecap','stroke-linejoin',
    'stroke-dasharray','stroke-opacity','clip-path','filter'
  ];
  const ready=()=>{
    const dialog=$('#detail-dialog'),image=$('#detail-img'),detail=window.__ourTimeApp?.state?.detail;
    return Boolean(
      dialog?.open
      && !dialog.classList.contains('is-loading')
      && detail?.id
      && Number(dialog.dataset.photoId)===Number(detail.id)
      && image?.complete
      && image.naturalWidth
      && !image.hidden
    );
  };
  function localizeCssUrl(value){
    const origin=location.origin.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
    return String(value||'').replace(new RegExp(`url\\((['"]?)${origin}(/api/face-label-bg/[1-9]\\.png)\\1\\)`,'gi'),'url("$2")');
  }
  function serializeFrozenMat(node){
    return node.outerHTML.replace(
      /url\(&quot;(\/api\/face-label-bg\/[1-9]\.png)&quot;\)/gi,
      "url('$1')"
    );
  }
  function copyComputedStyle(source,target,pseudo=null){
    const computed=getComputedStyle(source,pseudo);
    FROZEN_STYLE_PROPS.forEach(name=>{
      const value=localizeCssUrl(computed.getPropertyValue(name));
      if(value)target.style.setProperty(name,value,'important');
    });
    return computed;
  }
  function pseudoContent(value){
    if(!value||value==='none'||value==='normal')return '';
    if(/^["'].*["']$/.test(value)){
      try{return JSON.parse(value);}catch(error){return value.slice(1,-1);}
    }
    return value;
  }
  function materializePseudo(source,target,kind){
    const computed=getComputedStyle(source,`::${kind}`);
    if(computed.display==='none'||computed.content==='none'||computed.content==='normal')return;
    const pseudo=document.createElement('span');
    pseudo.dataset.frozenPseudo=kind;
    pseudo.setAttribute('aria-hidden','true');
    pseudo.textContent=pseudoContent(computed.content);
    copyComputedStyle(source,pseudo,`::${kind}`);
    if(kind==='before')target.insertBefore(pseudo,target.firstChild);
    else target.appendChild(pseudo);
  }
  function freezeTree(source,target){
    copyComputedStyle(source,target);
    const sourceChildren=[...source.children];
    const targetChildren=[...target.children];
    sourceChildren.forEach((child,index)=>freezeTree(child,targetChildren[index]));
    materializePseudo(source,target,'before');
    materializePseudo(source,target,'after');
  }
  function freeze(){
    const dialog=$('#detail-dialog'),mat=$('#photo-mat'),image=$('#detail-img');
    if(!ready())throw new Error('照片仍在准备中，请稍候再导出');
    const id=Number(window.__ourTimeApp.state.detail.id);
    const clone=mat.cloneNode(true);
    freezeTree(mat,clone);
    clone.querySelectorAll('.viewer-photo-close,.photo-favorite,.photo-place-map,.face-hover-guide,.face-hover-box,.signature-switch').forEach(node=>node.remove());
    clone.querySelectorAll('.is-linked,.is-linking').forEach(node=>node.classList.remove('is-linked','is-linking'));
    clone.querySelector('#detail-img')?.classList.remove('viewer-photo-arriving','viewer-photo-forward','viewer-photo-backward');
    // The backend binds this placeholder to the same asset's original file.
    clone.querySelector('#detail-img')?.removeAttribute('src');
    const ir=image.getBoundingClientRect(),mr=mat.getBoundingClientRect(),attrs={};
    [...dialog.attributes].forEach(attr=>{if(attr.name.startsWith('data-'))attrs[attr.name]=attr.value;});
    const style=EXPORT_STYLE_VARS.map(name=>{
      const value=localizeCssUrl(dialog.style.getPropertyValue(name).trim());
      return value?`${name}:${value}`:'';
    }).filter(Boolean).join(';');
    return {
      id,
      snapshot:{
        snapshot_version:2,
        mat_html:serializeFrozenMat(clone),
        dialog_attrs:attrs,
        dialog_style:style,
        geometry:{
          image_width:ir.width,
          image_height:ir.height,
          mat_width:mr.width,
          mat_height:mr.height
        }
      }
    };
  }
  async function run(requestedFormat){
    const button=exportButton();
    const picker=document.querySelector('#export-annotated-format');
    const format=String(requestedFormat||picker?.value||'jpeg').toLowerCase();
    let frozen;
    try{
      if(document.fonts?.ready)await document.fonts.ready;
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      frozen=freeze();
    }catch(error){
      window.viewerMessage?.(error.message);
      return;
    }
    button.disabled=true;
    if(picker)picker.disabled=true;
    button.dataset.exporting='true';
    try{
      const response=await fetch(`/api/photos/${frozen.id}/export-annotated`,{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({snapshot:frozen.snapshot,format})
      });
      if(!response.ok){
        const body=await response.json().catch(()=>({}));
        throw new Error(body.detail||'导出失败');
      }
      const actual=response.headers.get('X-OurTime-Export-Format')||format;
      const extension=actual==='png'?'png':'jpg';
      const label=actual==='png'?'PNG':'JPEG';
      const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement('a');
      link.href=url;
      link.download=`拾光标签照片.${extension}`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(()=>URL.revokeObjectURL(url),1000);
      window.viewerMessage?.(`已导出带标签照片（${label}）`);
    }catch(error){
      window.viewerMessage?.(error.message||'导出失败');
    }finally{
      button.disabled=false;
      if(picker)picker.disabled=false;
      delete button.dataset.exporting;
    }
  }
  function install(){
    const actions=document.querySelector('.viewer-tool-actions');
    if(!actions||exportButton())return;
    const picker=document.createElement('select');
    picker.id='export-annotated-format';
    picker.title='带标签照片格式';
    picker.setAttribute('aria-label','带标签照片导出格式');
    picker.innerHTML='<option value="jpeg" selected>JPG</option><option value="png">PNG</option>';
    const button=document.createElement('button');
    button.type='button';
    button.id='export-annotated-photo';
    button.title='按当前版式导出带标签照片';
    button.setAttribute('aria-label','按当前版式导出带标签照片');
    button.textContent='导出';
    button.addEventListener('click',()=>run());
    actions.append(picker,button);
  }
  install();
  window.__ourTimeAnnotatedExport={ready,freeze,run};
})();
