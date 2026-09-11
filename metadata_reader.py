"""Persistent, bounded ExifTool readers. One process per scanning worker."""
import atexit
import json
import os
import queue
import subprocess
import threading
import time

class MetadataReader:
    def __init__(self, executable):
        self.executable = str(executable)
        self.process = None
        self.counter = 0
        self.lock = threading.Lock()

    @staticmethod
    def drain(stream, output):
        try:
            for line in iter(stream.readline, b''):
                output.put(line)
        finally:
            output.put(None)

    def start(self):
        self.stdout = queue.Queue()
        self.stderr = queue.Queue()
        self.process = subprocess.Popen(
            [self.executable, '-stay_open', 'True', '-@', '-'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={**os.environ, 'LC_ALL':'C', 'LC_CTYPE':'C', 'LANG':'C'},
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        for stream, output in [(self.process.stdout,self.stdout),(self.process.stderr,self.stderr)]:
            threading.Thread(target=self.drain,args=(stream,output),daemon=True).start()

    @staticmethod
    def receive(output, marker, deadline):
        lines=[]
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0:
                raise TimeoutError('ExifTool 读取超时')
            try:
                line=output.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError('ExifTool 读取超时') from None
            if line is None:
                raise RuntimeError('ExifTool 进程提前退出')
            if line.strip()==marker:
                return b''.join(lines)
            lines.append(line)

    def read(self, path, timeout=45):
        with self.lock:
            if self.process is None or self.process.poll() is not None:
                self.start()
            self.counter+=1
            token=str(self.counter)
            end=f'__PHOTO_META_END_{token}__'
            arguments=['-json','-G1','-a','-s','-n','-struct','-charset','filename=UTF8',str(path),'-echo4',end,'-execute'+token]
            try:
                self.process.stdin.write(('\n'.join(arguments)+'\n').encode('utf-8'))
                self.process.stdin.flush()
                deadline=time.monotonic()+timeout
                output=self.receive(self.stdout,('{ready'+token+'}').encode(),deadline)
                diagnostics=self.receive(self.stderr,end.encode(),deadline).decode('utf-8',errors='replace').strip()
                records=json.loads(output.decode('utf-8'))
                if len(records)!=1:
                    raise RuntimeError('ExifTool 返回记录数量不符：'+diagnostics)
                result=records[0]
                if diagnostics:
                    result['_stderr']=diagnostics
                return result
            except Exception:
                self.close()
                raise

    def close(self):
        process=self.process
        self.process=None
        if process is None:
            return
        try:
            if process.poll() is None:
                process.stdin.write(b'-stay_open\nFalse\n')
                process.stdin.flush()
                process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
            process.wait(timeout=5)
        finally:
            for stream in [process.stdin,process.stdout,process.stderr]:
                if stream:
                    stream.close()

LOCAL=threading.local()
READERS=[]
REGISTRY_LOCK=threading.Lock()

def read_metadata(executable,path):
    reader=getattr(LOCAL,'reader',None)
    if reader is None:
        reader=MetadataReader(executable)
        LOCAL.reader=reader
        with REGISTRY_LOCK:
            READERS.append(reader)
    return reader.read(path)

def close_readers():
    with REGISTRY_LOCK:
        readers=list(READERS)
        READERS.clear()
    for reader in readers:
        reader.close()

atexit.register(close_readers)
