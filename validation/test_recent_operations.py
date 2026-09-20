"""Transactional undo using synthetic data only; production DB is never opened."""
import unittest
import uuid
from validation import test_m1_operation_safety as fixture


class RecentOperationsTests(unittest.TestCase):
    setUpClass = classmethod(fixture.M1ApiTests.setUpClass.__func__)
    tearDownClass = classmethod(fixture.M1ApiTests.tearDownClass.__func__)
    setUp = fixture.M1ApiTests.setUp

    def seed(self):
        with self.module.db() as c:
            for aid in (1,2):
                fixture.insert_asset(c,aid,str(aid)*64)
                c.execute('UPDATE assets SET manual_place=?,notes=?,latitude=40,longitude=116 WHERE id=?',
                          (f'旧地点{aid}',f'原备注{aid}',aid))

    def edit(self,**changes):
        response=self.client.patch('/api/organize/photos',json={'ids':[1,2],**changes})
        self.assertEqual(200,response.status_code,response.text)
        return response.json()['operation_id']

    def undo(self,op,dry=False):
        return self.client.post(f'/api/organize/operations/{op}/undo',json={'dry_run':dry})

    def places(self):
        with self.module.db() as c:
            return [r[0] for r in c.execute('SELECT manual_place FROM assets ORDER BY id')]

    def test_batch_restores_each_original_and_is_idempotent(self):
        self.seed();op=self.edit(manual_place='统一地点')
        self.assertTrue(self.undo(op,True).json()['can_undo'])
        self.assertEqual(['统一地点']*2,self.places())
        first=self.undo(op);second=self.undo(op)
        self.assertEqual(200,first.status_code,first.text)
        self.assertEqual(first.json(),second.json())
        self.assertEqual(['旧地点1','旧地点2'],self.places())
        rows=self.client.get('/api/organize/operations').json()['items']
        self.assertEqual(op,rows[0]['operation_id']);self.assertTrue(rows[0]['undone'])

    def test_later_edit_blocks_entire_batch_without_partial_restore(self):
        self.seed();op=self.edit(manual_place='第一次')
        self.edit(ids=[2],manual_place='第二次')
        self.assertFalse(self.undo(op,True).json()['can_undo'])
        self.assertEqual(409,self.undo(op).status_code)
        self.assertEqual(['第一次','第二次'],self.places())

    def test_unrelated_later_edit_is_preserved(self):
        self.seed();op=self.edit(manual_place='地点')
        self.edit(notes='新备注')
        self.assertEqual(200,self.undo(op).status_code)
        with self.module.db() as c:
            self.assertEqual(['新备注']*2,[r[0] for r in c.execute('SELECT notes FROM assets')])

    def test_idempotency_and_missing_asset_rollback(self):
        self.seed();op=str(uuid.uuid4());body={'ids':[1,2],'manual_place':'地点','operation_id':op}
        first=self.client.patch('/api/organize/photos',json=body)
        self.assertEqual(first.json(),self.client.patch('/api/organize/photos',json=body).json())
        self.assertEqual(409,self.client.patch('/api/organize/photos',json={**body,'manual_place':'另一个地点'}).status_code)
        response=self.client.patch('/api/organize/photos',json={'ids':[1,999],'manual_place':'不该写入'})
        self.assertEqual(404,response.status_code)
        self.assertEqual(['地点']*2,self.places())

    def test_radius_edit_undo_and_retry(self):
        self.seed();body={'place':'附近地点','radius_m':100,'operation_id':str(uuid.uuid4())}
        first=self.client.post('/api/organize/photos/1/nearby-place',json=body)
        self.assertEqual(200,first.status_code,first.text)
        self.assertEqual(first.json(),self.client.post('/api/organize/photos/1/nearby-place',json=body).json())
        self.assertEqual(200,self.undo(first.json()['operation_id']).status_code)
        self.assertEqual(['旧地点1','旧地点2'],self.places())

    def test_list_does_not_claim_destructive_exclusion_is_undoable(self):
        self.seed()
        self.client.post('/api/exclusions/assets',json={'ids':[1],'excluded':True,'mode':'display_only'})
        self.assertEqual([],self.client.get('/api/organize/operations').json()['items'])


if __name__=='__main__':unittest.main(verbosity=2)
