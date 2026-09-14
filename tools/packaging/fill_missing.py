import importlib
import shutil
import subprocess
from pathlib import Path

SRC = Path(r"C:\Users\A\PycharmProjects\ImageBrowser\.venv\Lib\site-packages")
DEST = Path(__file__).resolve().parents[2] / "dist" / "拾光相册" / "App" / "python" / "Lib" / "site-packages"
PY = DEST.parents[1] / "python.exe"
CHECKS = "import fastapi, uvicorn, insightface, onnxruntime, cv2, PIL, playwright"


def missing_from(err: str) -> str | None:
    marker = "No module named "
    if marker not in err:
        return None
    name = err.split(marker, 1)[1].strip().strip("'\"").split(".")[0]
    return name


def copy_mod(name: str) -> bool:
    folder = SRC / name
    file_py = SRC / (name + ".py")
    if folder.is_dir():
        shutil.copytree(folder, DEST / name, dirs_exist_ok=True)
        print("copied dir", name)
        return True
    if file_py.is_file():
        shutil.copy2(file_py, DEST / file_py.name)
        print("copied py", name)
        return True
    pyds = list(SRC.glob(name + "*.pyd"))
    if pyds:
        for pyd in pyds:
            shutil.copy2(pyd, DEST / pyd.name)
        print("copied pyd", name)
        return True
    print("not found", name)
    return False


def main() -> int:
    for _ in range(30):
        proc = subprocess.run([str(PY), "-c", CHECKS], capture_output=True, text=True)
        if proc.returncode == 0:
            print(proc.stdout)
            print("FILL_OK")
            return 0
        err = proc.stderr or proc.stdout
        name = missing_from(err)
        print(err[-400:])
        if not name or not copy_mod(name):
            return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
