# 独立人物模板生成器

`build_person_templates.py` 只读取已有 SQLite 人脸特征，生成一个新的紧凑人物模板数据库。
它不导入应用后台、不修改正式资料库，也不接入扫描、命名、合并或现有人脸匹配流程。

输入支持两种现有结构：

- `人物特征与姓名-*.sqlite3`：`source_person_id` / `source_face_id`
- 正式资料库：`people.id` / `faces.id`

只处理 `confirmed=1 AND ignored=0`、姓名非空且人脸未忽略的人物。路人和待确认人物不会进入输出。

算法步骤：

1. 校验并 L2 归一化 InsightFace `float32` 特征。
2. 使用 scikit-learn 的 KMeans / MiniBatchKMeans 在单位向量上聚类。
3. 每簇裁掉距离中心最远的少量样本，再重新拟合中心。
4. 对至少三条有效特征的人物，在 3–5 个候选模板中选择达到接近最佳覆盖率的最小数量。
5. 每个最终中心再次归一化，并记录最接近中心的来源人脸编号。
6. 使用未参与候选模板拟合的留出特征做跨人物识别验证。

运行示例：

```powershell
& .venv\Scripts\python.exe tools\build_person_templates.py `
  --source "人物特征与姓名-20260914.sqlite3" `
  --output "人物紧凑模板-20260914.sqlite3" `
  --report "validation\reports\person-templates-20260914.json"
```

生成器拒绝覆盖已有输出。将来姓名数据更新后，使用新的源库和新的输出文件名重新运行即可。
