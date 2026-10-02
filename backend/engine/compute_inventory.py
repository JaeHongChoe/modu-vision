"""Read physical CUDA UUIDs/capacity and optional NVML MIG child handles."""
import csv
import io
import locale
import subprocess


def device_inventory():
    devices=[];error=None;mig_supported=False
    try:
        result=subprocess.run(['nvidia-smi','--query-gpu=index,uuid,memory.total','--format=csv,noheader,nounits'],capture_output=True,text=True,encoding=locale.getpreferredencoding(False),errors='replace',timeout=5,check=True)
        for values in csv.reader(io.StringIO(result.stdout)):
            index,identifier,memory=(part.strip() for part in values)
            devices.append({'selector':index,'uuid':identifier,'memory_mb':int(float(memory)),'parent_uuid':None,'kind':'cuda'})
        try:
            import pynvml
            pynvml.nvmlInit()
            try:
                for index in range(pynvml.nvmlDeviceGetCount()):
                    parent=pynvml.nvmlDeviceGetHandleByIndex(index)
                    parent_uuid=pynvml.nvmlDeviceGetUUID(parent)
                    parent_uuid=parent_uuid.decode() if isinstance(parent_uuid,bytes) else parent_uuid
                    try:enabled=pynvml.nvmlDeviceGetMigMode(parent)[0]
                    except pynvml.NVMLError:continue
                    if not enabled:continue
                    mig_supported=True
                    for child_index in range(pynvml.nvmlDeviceGetMaxMigDeviceCount(parent)):
                        try:
                            child=pynvml.nvmlDeviceGetMigDeviceHandleByIndex(parent,child_index)
                            identifier=pynvml.nvmlDeviceGetUUID(child)
                            identifier=identifier.decode() if isinstance(identifier,bytes) else identifier
                            memory=pynvml.nvmlDeviceGetMemoryInfo(child).total//(1024*1024)
                            devices.append({'selector':identifier,'uuid':identifier,'parent_uuid':parent_uuid,'memory_mb':memory,'kind':'mig'})
                        except pynvml.NVMLError:continue
            finally:pynvml.nvmlShutdown()
        except ImportError:error='MIG inventory requires the optional nvidia-ml-py dependency'
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        error='CUDA physical UUID inventory is unavailable: '+type(exc).__name__
    return {'devices':devices,'cpu':{'available':True},'mig_supported':mig_supported,'prerequisite':error}
