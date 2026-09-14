
"""SigLIP2 物体标签：中文候选 + 本地零样本分类。"""
from __future__ import annotations

from pathlib import Path
import json
import threading
from PIL import Image

LABELS = [
    ("人", "a photo of a person"),
    ("多人合影", "a photo of a large group of people posing together"),
    ("孩子", "a photo of a child"),
    ("老人", "a photo of an elderly person"),
    ("宠物狗", "a photo of a dog"),
    ("宠物猫", "a photo of a cat"),
    ("火锅", "a photo of hotpot"),
    ("家常菜", "a photo of homemade Chinese food"),
    ("蛋糕", "a photo of a cake"),
    ("饮料", "a photo of drinks"),
    ("室内", "a photo taken indoors"),
    ("客厅", "a photo of a living room"),
    ("厨房", "a photo of a kitchen"),
    ("办公室", "a photo of an office"),
    ("户外", "a photo taken outdoors"),
    ("公园", "a photo of a park"),
    ("海边", "a photo of the seaside"),
    ("城市街道", "a photo of a city street"),
    ("夜景", "a photo of a night scene"),
    ("汽车", "a photo of a car"),
    ("电脑", "a photo of a computer"),
    ("手机", "a photo of a mobile phone"),
    ("相机", "a photo of a camera"),
    ("花", "a photo of flowers"),
    ("生日", "a photo of a birthday party"),
    ("婚礼", "a photo of a wedding"),
    ("毕业", "a photo of a graduation ceremony"),
    ("运动", "a photo of people playing sports"),
    ("网球", "a photo of tennis"),
    ("舞台", "a photo of a stage performance"),
    ("建筑", "a photo of a building"),
    ("截图", "a screenshot of a computer screen"),
    ("文件扫描件", "a photo of a scanned document"),
]

from ourtime_config import OBJECT_MODEL_DIR

MODEL_DIR = OBJECT_MODEL_DIR
_ONNX_DIR = MODEL_DIR / "onnx"
_LOCK = threading.Lock()
_SESSION = None
_PROCESSOR = None
_TEXT = None
_ERROR = None
_RUNTIME = None
BATCH_SIZE = 8


def model_ready():
    return (_ONNX_DIR / "vision.onnx").exists() and (_ONNX_DIR / "text_embeds.npy").exists()


def runtime_name():
    if _ERROR:
        return "未加载"
    if _SESSION is None:
        return "待加载"
    return _RUNTIME or "CPU"


def _load():
    global _SESSION, _PROCESSOR, _TEXT, _ERROR, _RUNTIME
    if _SESSION is not None:
        return _SESSION, _TEXT
    with _LOCK:
        if _SESSION is not None:
            return _SESSION, _TEXT
        try:
            import onnxruntime as ort
            options = ort.SessionOptions()
            options.intra_op_num_threads = 1
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            available = list(ort.get_available_providers())
            chain = [name for name in ("CUDAExecutionProvider", "DmlExecutionProvider") if name in available] + ["CPUExecutionProvider"]
            last = None
            session = None
            used = []
            for index, provider in enumerate(chain):
                try:
                    session = ort.InferenceSession(str(_ONNX_DIR / "vision.onnx"), sess_options=options, providers=chain[index:])
                    used = session.get_providers()
                    break
                except Exception as exc:
                    last = exc
                    session = None
            if session is None:
                raise RuntimeError(last)
            mapping = {"CUDAExecutionProvider":"GPU (CUDA)", "DmlExecutionProvider":"GPU (DirectML)", "CPUExecutionProvider":"CPU"}
            _RUNTIME = mapping.get(used[0], used[0])
            text = __import__("numpy").load(_ONNX_DIR / "text_embeds.npy")
            meta = json.loads((_ONNX_DIR / "meta.json").read_text(encoding="utf-8"))
            _SESSION, _TEXT, _ERROR = session, (text, meta), None
        except Exception as exc:
            _ERROR = str(exc)
            raise
    return _SESSION, _TEXT

def _preprocess(image):
    import numpy as np
    image = image.convert("RGB").resize((384, 384), Image.BICUBIC)
    arr = np.asarray(image).astype("float32") / 255.0
    arr = (arr - 0.5) / 0.5
    return arr.transpose(2, 0, 1)

def classify_images(images, top_k=4, min_score=0.08):
    import numpy as np
    session, packed = _load()
    text, meta = packed
    pixels = np.stack([_preprocess(image) for image in images]).astype("float32")
    name = session.get_inputs()[0].name
    embeds = session.run(None, {name: pixels})[0]
    embeds = embeds / np.clip(np.linalg.norm(embeds, axis=1, keepdims=True), 1e-6, None)
    scale = float(meta.get("scale", 4.6)); bias = float(meta.get("bias", 0.0))
    logits = scale * (embeds @ text.T) + bias
    probs = 1.0 / (1.0 + np.exp(-np.clip(logits, -30, 30)))
    labels = meta.get("labels") or [item[0] for item in LABELS]
    results = []
    for row in probs:
        ranked = sorted(((labels[i], float(row[i])) for i in range(len(labels))), key=lambda item: item[1], reverse=True)
        picked = [item for item in ranked if item[1] >= min_score][:top_k]
        results.append([{"label": name, "score": round(score, 4)} for name, score in picked])
    return results

def classify_image(image, top_k=4, min_score=0.08):
    return classify_images([image], top_k=top_k, min_score=min_score)[0]
