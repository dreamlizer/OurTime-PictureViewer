"""Read-only real face-box layout experiment. Writes only its report directory."""
import base64
import html
import json
import math
import sqlite3
import statistics
import time
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'validation/reports/label-layout-example-20260919'
OUT.mkdir(parents=True, exist_ok=True)
c = sqlite3.connect((ROOT / 'data/library.sqlite3').as_uri() + '?mode=ro', uri=True)
c.row_factory = sqlite3.Row
asset = dict(c.execute('SELECT id,width,height,sha256 FROM assets WHERE id=12294').fetchone())
rows = c.execute('SELECT f.id,f.bbox,p.name FROM faces f JOIN people p ON p.id=f.person_id WHERE asset_id=?', (asset['id'],)).fetchall()
c.close()
items = []
for r in rows:
    x1,y1,x2,y2,bw,bh = json.loads(r['bbox'])
    count = len(''.join(r['name'].split()))
    items.append(dict(face_id=r['id'], box=[x1/bw*1200,y1/bh*900,(x2-x1)/bw*1200,(y2-y1)/bh*900], label=[38,56 if count<=2 else 72 if count==3 else 86]))
items.sort(key=lambda x:x['box'][0])
for i,it in enumerate(items): it['id'] = chr(65+i)

def overlap(a,b):
    return max(0,min(a[0]+a[2],b[0]+b[2])-max(a[0],b[0])) * max(0,min(a[1]+a[3],b[1]+b[3])-max(a[1],b[1]))

def gap(a,b):
    return math.hypot(max(a[0]-b[0]-b[2],b[0]-a[0]-a[2],0),max(a[1]-b[1]-b[3],b[1]-a[1]-a[3],0))

faces=[it['box'] for it in items]
protected=[[x-4,y-4,w+8,h+8] for x,y,w,h in faces]
choices=[]
for it in items:
    x,y,w,h=it['box'];lw,lh=it['label'];opts=[]
    for side in ['left','right','bottom','top']:
        for shift in [0,-.4,.4]:
            if side in ('left','right'):
                px=x-lw-8 if side=='left' else x+w+8;py=y+h/2-lh/2+shift*h
            else:
                px=x+w/2-lw/2+shift*w;py=y+h+8 if side=='bottom' else y-lh-8
            rect=[px,py,lw,lh]
            if px>=4 and py>=4 and px+lw<=1196 and py+lh<=896:
                opts.append(dict(rect=rect,side=side,shift=shift))
    choices.append(opts)

def prefer(mode,i):
    if mode=='outward': return 'left' if faces[i][0]+faces[i][2]/2<600 else 'right'
    return mode

def solve(mode):
    # Every template can use a local alternative. No unconstrained far-away search.
    assigned={}
    def cost(i,option):
        r=option['rect']
        face_area=sum(overlap(r,b) for b in protected)
        label_area=sum(overlap(r,choices[j][k]['rect']) for j,k in assigned.items() if j!=i)
        own=gap(r,faces[i]);other=min(gap(r,b) for j,b in enumerate(faces) if j!=i)
        ambiguity=max(0,own-other)
        return (face_area*10000+label_area*10000+ambiguity*100+own*2+
                (0 if option['side']==prefer(mode,i) else 30)+abs(option['shift'])*8)
    order=sorted(range(len(items)),key=lambda i:sum(overlap(opt['rect'],b)==0 for opt in choices[i] for b in protected))
    for i in order: assigned[i]=min(range(len(choices[i])),key=lambda k:cost(i,choices[i][k]))
    for _ in range(3):
        changed=False
        for i in order:
            k=min(range(len(choices[i])),key=lambda k:cost(i,choices[i][k]))
            changed|=k!=assigned[i];assigned[i]=k
        if not changed:break
    labels=[choices[i][assigned[i]] for i in range(len(items))]
    rects=[v['rect'] for v in labels]
    metrics=dict(face_overlap_px2=round(sum(overlap(r,b) for r in rects for b in protected),2),
                 label_overlap_px2=round(sum(overlap(rects[i],rects[j]) for i in range(len(items)) for j in range(i)),2),
                 max_face_gap_px=round(max(gap(r,faces[i]) for i,r in enumerate(rects)),2),
                 mean_face_gap_px=round(statistics.mean(gap(r,faces[i]) for i,r in enumerate(rects)),2),
                 nearer_other_face=sum(gap(r,faces[i])>min(gap(r,b) for j,b in enumerate(faces) if i!=j)+.01 for i,r in enumerate(rects)),
                 template_exceptions=sum(v['side']!=prefer(mode,i) for i,v in enumerate(labels)))
    return dict(mode=mode,labels=labels,metrics=metrics)

modes=['left','right','outward','bottom']
start=time.perf_counter();results=[solve(m) for m in modes];elapsed=(time.perf_counter()-start)*1000
for result in results:
    print(result['mode'],json.dumps(result['metrics']))
print('four_layouts_ms',round(elapsed,2))
report=dict(asset_id=asset['id'],original_size=[asset['width'],asset['height']],face_coordinate_size=[2400,1800],display_size=[1200,900],
            assumptions=['Label boxes use current decorative plate nominal sizes, not live DOM measurements.','A-G map to existing face records; no identity recognition performed.','This is a geometric prototype, not a production optimizer or aesthetic validation.','Bounding boxes expanded 4 display pixels are protected, not anatomical eye/mouth masks.'],
            items=items,layouts=results,compute_ms=elapsed)
(OUT/'calculation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')

thumb=ROOT/'data/thumbs'/f"{asset['sha256']}.jpg"
datauri='data:image/jpeg;base64,'+base64.b64encode(thumb.read_bytes()).decode()
colors=['#196eaa','#6e44ab','#007e73','#a4440b','#8e304d','#5c681c','#5b4a36']
panels=[]
names={'left':'统一靠左起步','right':'统一靠右起步','outward':'左右向外起步','bottom':'脸下方起步'}
for n,result in enumerate(results):
    shapes=[]
    for i,it in enumerate(items):
        x,y,w,h=it['box'];lx,ly,lw,lh=result['labels'][i]['rect'];color=colors[i]
        shapes.append(f'<g><rect class="face" x="{x}" y="{y}" width="{w}" height="{h}" stroke="{color}"/><line class="guide" x1="{x+w/2}" y1="{y+h/2}" x2="{lx+lw/2}" y2="{ly+lh/2}" stroke="{color}"/><rect x="{lx}" y="{ly}" width="{lw}" height="{lh}" rx="5" fill="#fff9e9" stroke="{color}"/><text x="{lx+lw/2}" y="{ly+lh/2}" fill="{color}" text-anchor="middle" dominant-baseline="middle" font-size="23">{it["id"]}</text></g>')
    panels.append(f'<section id="p{n}" {"hidden" if n else ""}><h2>{names[result["mode"]]}</h2><p>局部冲突时允许换边；不是强制所有标签放同侧。</p><svg viewBox="0 0 1200 900"><image href="{datauri}" width="1200" height="900"/>'+''.join(shapes)+f'</svg><p>脸框保护区重叠 {result["metrics"]["face_overlap_px2"]} px² · 标签互相重叠 {result["metrics"]["label_overlap_px2"]} px² · 最远离脸 {result["metrics"]["max_face_gap_px"]} px</p></section>')
buttons=''.join(f'<button onclick="document.querySelectorAll(\'section\').forEach((p,j)=>p.hidden=j!=={i})">{names[m]}</button>' for i,m in enumerate(modes))
table=''.join(f'<tr><td>{it["id"]}</td><td>{it["face_id"]}</td><td>{", ".join(str(round(v,1)) for v in it["box"])}</td><td>{it["label"][0]} × {it["label"][1]}</td></tr>' for it in items)
page='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>七人合影标签计算示例</title><style>body{background:#f5f6f2;color:#25342f;font:16px/1.7 system-ui;margin:24px auto;max-width:1100px;padding:0 20px}button{padding:10px 15px;margin:4px;border:1px solid #bbc9bf;background:white;border-radius:8px;cursor:pointer}svg{width:100%;display:block}.face{fill:none;stroke-width:2}.guide{stroke-width:1.5;stroke-dasharray:4 3;opacity:.7}body.clean .face,body.clean .guide{display:none}table{border-collapse:collapse}td,th{padding:7px 15px;border-bottom:1px solid #ccc}small{color:#65736a}</style><h1>一张七人合影，四种排布试算</h1><p>已有脸框直接换算；原图不改、数据库只读、没有重新识别人脸。A–G 是本次位置代号，底牌尺寸按实际姓名字数估算，尚未测量浏览器字体。低分辨率预览仅用于展示。</p>'+buttons+'<button onclick="document.body.classList.toggle(\'clean\')">显示 / 隐藏计算辅助线</button>'+''.join(panels)+'<h2>实际输入</h2><p>原图 2592×1944；脸框坐标系 2400×1800；本次展示坐标系 1200×900。下表为 x、y、宽、高。</p><table><tr><th>位置代号</th><th>已有 face ID</th><th>换算脸框 px</th><th>估算标签 px</th></tr>'+table+f'</table><p>四方案计算合计 {elapsed:.1f} ms（本机 Python 原型一次计时，不是浏览器性能承诺）。几何无重叠不代表审美最优；胸前这里只是脸下方候选，没有人体识别。</p></html>'
(OUT/'example.html').write_text(page,encoding='utf-8')

# Standalone coordinate plot; no source-photo pixels are modified or rendered into it.
result=results[2]
canvas=Image.new('RGB',(1240,760),'#f5f6f2');draw=ImageDraw.Draw(canvas)
font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',20)
small=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',16)
draw.text((25,18),'7 real face boxes / label placement calculation (1200 x 900 display)',font=font,fill='#25342f')
draw.text((25,48),'Cropped coordinate view of the face band; A-G are positional IDs, not names.',font=small,fill='#65736a')
dy=-150
for i,it in enumerate(items):
    x,y,w,h=it['box'];lx,ly,lw,lh=result['labels'][i]['rect'];color=colors[i]
    draw.rectangle((x+20,y+dy,x+w+20,y+h+dy),outline=color,width=3)
    draw.text((x+22,y+dy+3),it['id'],font=small,fill=color)
    draw.line((x+w/2+20,y+h/2+dy,lx+lw/2+20,ly+lh/2+dy),fill=color,width=1)
    draw.rounded_rectangle((lx+20,ly+dy,lx+lw+20,ly+lh+dy),radius=4,fill='#fff9e9',outline=color,width=2)
    draw.text((lx+lw/2+20,ly+lh/2+dy),it['id'],font=font,fill=color,anchor='mm')
draw.text((25,430),'Outward template + local adjustments. Protected-face overlap: 0; label overlap: 0.',font=font,fill='#25342f')
for i,it in enumerate(items):
    draw.text((25,475+i*30),f'{it["id"]}: face {tuple(round(v,1) for v in it["box"])}   label {tuple(round(v,1) for v in result["labels"][i]["rect"])}   {result["labels"][i]["side"]}',font=small,fill=colors[i])
canvas.save(OUT/'coordinates.png')
print('report',str(OUT/'example.html'))
