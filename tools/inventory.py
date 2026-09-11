"""只枚举图片文件与目录，为完整元数据扫描估算规模；不打开图片内容。"""
import os
import json
import time
from collections import Counter
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
OUT=BASE/'data'/'inventory-estimate.json'
ROOTS=['I:\\','D:\\','E:\\','F:\\','G:\\','H:\\']
EXTENSIONS={'.jpg','.jpeg','.png','.webp','.bmp','.gif','.tif','.tiff','.heic','.heif','.avif','.dng','.cr2','.cr3','.nef','.arw','.raf','.rw2'}
EXCLUDE={'$recycle.bin','system volume information','windows','program files','program files (x86)','programdata','appdata','node_modules','.git','.venv','venv','__pycache__','$windows.~bt'}
state={'started_at':time.strftime('%Y-%m-%dT%H:%M:%S'),'status':'running','roots':{},'current_root':'','current_directory':'','total':0,'errors':0}
started=time.monotonic()
last_save=0
def save(force=False):
    global last_save
    if force or time.monotonic()-last_save>3:
        state['elapsed_seconds']=round(time.monotonic()-started,1)
        state['updated_at']=time.strftime('%Y-%m-%dT%H:%M:%S')
        tmp=OUT.with_suffix('.tmp')
        tmp.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
        os.replace(tmp,OUT)
        last_save=time.monotonic()

save(True)
for root in ROOTS:
    state['current_root']=root
    result={'status':'running','images':0,'directories':0,'files_seen':0,'excluded_directories':0,'offline_or_links':0,'errors':0,'extensions':{}}
    state['roots'][root]=result
    stack=[Path(root)]
    counts=Counter()
    while stack:
        folder=stack.pop()
        state['current_directory']=str(folder)
        result['directories']+=1
        try:
            with os.scandir(folder) as entries:
                for item in entries:
                    try:
                        if item.is_symlink():
                            result['offline_or_links']+=1
                            continue
                        if item.is_dir(follow_symlinks=False):
                            child=Path(item.path)
                            if item.name.lower() in EXCLUDE or child==BASE or child==BASE/'data':
                                result['excluded_directories']+=1
                                continue
                            if getattr(item.stat(follow_symlinks=False),'st_file_attributes',0)&0x400:
                                result['offline_or_links']+=1
                                continue
                            stack.append(child)
                        elif item.is_file(follow_symlinks=False):
                            result['files_seen']+=1
                            ext=os.path.splitext(item.name)[1].lower()
                            if ext in EXTENSIONS:
                                attributes=getattr(item.stat(follow_symlinks=False),'st_file_attributes',0)
                                if attributes&(0x1000|0x400000|0x400):
                                    result['offline_or_links']+=1
                                    continue
                                result['images']+=1;state['total']+=1;counts[ext]+=1
                    except OSError:
                        result['errors']+=1;state['errors']+=1
        except OSError:
            result['errors']+=1;state['errors']+=1
        result['extensions']=dict(counts)
        save()
    result['status']='completed';save(True)
    print(json.dumps({'root':root,'images':result['images'],'directories':result['directories'],'errors':result['errors'],'elapsed_seconds':state['elapsed_seconds']},ensure_ascii=False),flush=True)
state['status']='completed';state['current_root']='';state['current_directory']='';save(True)
print(json.dumps({'total':state['total'],'elapsed_seconds':state['elapsed_seconds'],'errors':state['errors']},ensure_ascii=False),flush=True)
