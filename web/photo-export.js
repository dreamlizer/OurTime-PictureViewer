/* One-photo, frozen-viewer export. It never reads a disk path or changes state. */
(()=>{
  const $=window.$||((selector)=>document.querySelector(selector));
  const exportButton=()=>document.querySelector('#export-annotated-photo');
  const ready=()=>{const dialog=$('#detail-dialog'),image=$('#detail-img'),detail=window.__ourTimeApp?.state?.detail;return Boolean(dialog?.open&&!dialog.classList.contains('is-loading')&&detail?.id&&image?.complete&&image.naturalWidth);};
  function freeze(){
    const dialog=$('#detail-dialog'),mat=$('#photo-mat'),image=$('#detail-img');
    if(!ready())throw new Error('照片仍在准备中，请稍候再导出');
    const clone=mat.cloneNode(true);clone.querySelectorAll('.viewer-photo-close,.photo-favorite,.photo-place-map,.face-hover-guide,.face-hover-box').forEach(node=>node.remove());
    const ir=image.getBoundingClientRect(),mr=mat.getBoundingClientRect(),attrs={};
    [...dialog.attributes].forEach(attr=>{if(attr.name.startsWith('data-'))attrs[attr.name]=attr.value;});
    return {id:Number(window.__ourTimeApp.state.detail.id),snapshot:{mat_html:clone.outerHTML,dialog_attrs:attrs,dialog_style:dialog.style.cssText,geometry:{image_width:ir.width,image_height:ir.height,mat_width:mr.width}}};
  }
  async function run(){
    const button=exportButton();let frozen;try{frozen=freeze();}catch(error){window.viewerMessage?.(error.message);return;}
    button.disabled=true;button.dataset.exporting='true';
    try{const response=await fetch(`/api/photos/${frozen.id}/export-annotated`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({snapshot:frozen.snapshot})});if(!response.ok){const body=await response.json().catch(()=>({}));throw new Error(body.detail||'导出失败');}const blob=await response.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='拾光标签照片.png';document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);window.viewerMessage?.('已生成新的 PNG 照片。');}
    catch(error){window.viewerMessage?.(error.message||'导出失败');}finally{button.disabled=false;delete button.dataset.exporting;}
  }
  function install(){const actions=document.querySelector('.viewer-tool-actions');if(!actions||exportButton())return;const button=document.createElement('button');button.type='button';button.id='export-annotated-photo';button.title='导出带标签照片';button.setAttribute('aria-label','导出带标签照片');button.textContent='导出';button.addEventListener('click',run);actions.appendChild(button);}
  install();window.__ourTimeAnnotatedExport={ready,freeze};
})();
