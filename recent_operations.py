"""Atomic, reversible photo metadata operations and a shared recent-operations list.

Registered separately so navigation work does not replace the scan/map handlers.
"""
import json
import re
from datetime import datetime

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field, PositiveInt
from home_recommendations import invalidate_home_cache


class PhotoEdit(BaseModel):
    ids: list[PositiveInt] = Field(min_length=1, max_length=1000)
    operation_id: str | None = None
    manual_date: str | None = None
    manual_precision: str | None = None
    manual_place: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=5000)


class NearbyEdit(BaseModel):
    place: str = Field(min_length=1, max_length=200)
    radius_m: int = Field(default=100, ge=1, le=10000)
    operation_id: str | None = None


class Undo(BaseModel):
    dry_run: bool = True


def register(app, runtime):
    def service(name):
        return runtime[name]

    def apply(c, ids, changes):
        items=[]
        for aid in ids:
            old=c.execute('SELECT * FROM assets WHERE id=?',(aid,)).fetchone()
            if not old:raise HTTPException(404,f'照片 {aid} 不存在')
            before={key:old[key] for key in changes}
            items.append({'id':aid,'before':before,'after':dict(changes)})
            c.execute('UPDATE assets SET '+','.join(f'{key}=?' for key in changes)+' WHERE id=?',
                      [*changes.values(),aid])
            c.execute('INSERT INTO edits(created_at,target,before_json,after_json) VALUES (?,?,?,?)',
                      (service('now')(),f'asset:{aid}',json.dumps(before,ensure_ascii=False),json.dumps(changes,ensure_ascii=False)))
        return items

    @app.patch('/api/organize/photos')
    def edit_photos(body:PhotoEdit,request:Request):
        operation_id=service('operation_id_from')(body,request)
        changes=body.model_dump(exclude_unset=True)
        ids=list(dict.fromkeys(changes.pop('ids')));changes.pop('operation_id',None)
        if not changes:raise HTTPException(400,'没有修改内容')
        for key in ('manual_date','manual_place','manual_precision'):
            if key in changes:changes[key]=(changes[key] or '').strip() or None
        if 'notes' in changes:changes['notes']=changes['notes'] or ''
        if changes.get('manual_precision') not in {None,'年','月','日','范围 / 描述'}:
            raise HTTPException(400,'日期精度无效')
        date=changes.get('manual_date')
        if date:
            precision=changes.get('manual_precision')
            patterns={'年':r'\d{4}','月':r'\d{4}-\d{2}','日':r'\d{4}-\d{2}-\d{2}','范围 / 描述':r'.{1,100}'}
            if precision not in patterns or not re.fullmatch(patterns[precision],date):
                raise HTTPException(400,'日期格式应与精度一致：年 2012、月 2012-07、日 2012-07-21；大致范围请选择“范围 / 描述”')
            try:
                if precision!='范围 / 描述':datetime.strptime(date,{'年':'%Y','月':'%Y-%m','日':'%Y-%m-%d'}[precision])
            except ValueError:raise HTTPException(400,'日期不存在，请检查年月日')
        with service('db')() as c:
            op,existing=service('prepare_operation')(c,'photo_edit',operation_id,{'ids':ids,'changes':changes})
            if existing:return existing
            items=apply(c,ids,changes)
            invalidate_home_cache(c)
            return service('finish_operation')(c,op,{'updated':len(items)},undo={'assets':items})

    @app.post('/api/organize/photos/{aid}/nearby-place')
    def nearby_edit(aid:int,body:NearbyEdit,request:Request):
        place=body.place.strip()
        if not place:raise HTTPException(400,'请填写地点名称')
        op=service('operation_id_from')(body,request)
        try:
            with service('db')() as c:
                op,existing=service('prepare_operation')(c,'nearby_place_edit',op,{'id':aid,'place':place,'radius_m':body.radius_m})
                if existing:return existing
                clause,values,anchor=service('nearby_photo_spec')(c,aid,body.radius_m)
                rows=c.execute('SELECT a.id,a.manual_place FROM assets a WHERE '+service('ACTIVE_ASSET')+' AND '+clause,values).fetchall()
                ids=[row['id'] for row in rows if (row['manual_place'] or '')!=place]
                items=apply(c,ids,{'manual_place':place})
                invalidate_home_cache(c)
                return service('finish_operation')(c,op,{'name':place,'radius_m':body.radius_m,
                    'matched':len(rows),'updated':len(items),'anchor':anchor,
                    'message':f'已把 {len(rows)} 张照片标为“{place}”，可在最近操作中撤销'},undo={'assets':items} if items else None)
        except ValueError as error:raise HTTPException(400,str(error)) from error

    @app.get('/api/organize/operations')
    def recent(limit:int=30):
        with service('db')() as c:
            rows=c.execute('''SELECT * FROM operations WHERE state_committed=1 AND undo_json IS NOT NULL
                AND kind IN ('person_merge','photo_edit','nearby_place_edit')
                ORDER BY created_at DESC,rowid DESC LIMIT ?''',(min(max(limit,1),50),)).fetchall()
        items=[]
        labels={'manual_date':'时间','manual_precision':'时间精度','manual_place':'地点','notes':'备注'}
        for row in rows:
            undo=json.loads(row['undo_json'])
            if row['kind']=='person_merge':
                title='合并人物：'+(undo['source'].get('name') or '未命名人物')
                detail=f"移动了 {len(undo['faces'])} 张人脸；撤销后恢复原分组"
            else:
                assets=undo.get('assets',[])
                fields=list(dict.fromkeys(k for item in assets for k in item['after']))
                if 'manual_date' in fields and 'manual_precision' in fields:fields.remove('manual_precision')
                title=f"修改 {len(assets)} 张照片的"+'、'.join(labels[k] for k in fields)
                detail='仅恢复这次修改的人工资料，原照片不变'
            items.append({'operation_id':row['operation_id'],'kind':row['kind'],'created_at':row['created_at'],
                          'status':row['status'],'title':title,'detail':detail,'undone':row['status']=='undone'})
        return {'items':items}

    @app.post('/api/organize/operations/{operation_id}/undo')
    def undo(operation_id:str,body:Undo):
        with service('db')() as c:
            row=c.execute('SELECT kind FROM operations WHERE operation_id=?',(operation_id,)).fetchone()
        if not row:raise HTTPException(404,'操作不存在')
        if row['kind']=='person_merge':
            return service('undo_operation')(operation_id,service('UndoRequest')(dry_run=body.dry_run))
        if row['kind'] not in {'photo_edit','nearby_place_edit'}:raise HTTPException(409,'这个操作不能撤销')
        with service('operation_lock')(operation_id),service('db')() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT * FROM operations WHERE operation_id=?',(operation_id,)).fetchone()
            if row['status']=='undone':return service('operation_receipt')(row)
            if not row['state_committed'] or not row['undo_json']:raise HTTPException(409,'没有完整撤销记录')
            record=json.loads(row['undo_json']);items=record['assets'];conflicts=[]
            for item in items:
                if not item['after'] or not set(item['after'])<={'manual_date','manual_precision','manual_place','notes'} or set(item['before'])!=set(item['after']):
                    raise HTTPException(409,'撤销记录不完整')
                current=c.execute('SELECT * FROM assets WHERE id=?',(item['id'],)).fetchone()
                if not current or any(current[k]!=v for k,v in item['after'].items()):
                    conflicts.append(f"照片 {item['id']} 的相关资料已在此操作后改变")
            if body.dry_run:return {'can_undo':not conflicts,'conflicts':conflicts,'photos':len(items)}
            if conflicts:raise service('ApiProblem')(409,'相关资料已有后续修改，未执行撤销','undo_conflict',conflicts=conflicts)
            for item in items:apply(c,[item['id']],item['before'])
            invalidate_home_cache(c)
            return service('finish_operation')(c,operation_id,{**json.loads(row['result_json']),'undone':True,'undo_photos':len(items)},status='undone',undo=record)
