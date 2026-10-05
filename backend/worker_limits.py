"""Resource limits in the parser child, not a filesystem/network sandbox."""
import os
import sys

CPU_SECONDS = 120
FILE_BYTES = 64 * 1024 * 1024
MEMORY_BYTES = 1024 * 1024 * 1024


def apply_limits():
    if os.name == 'nt':
        return windows_job()
    import resource
    def cap(name, value):
        if not hasattr(resource, name):
            return
        kind = getattr(resource, name)
        _, maximum = resource.getrlimit(kind)
        if maximum != resource.RLIM_INFINITY:
            value = min(value, maximum)
        resource.setrlimit(kind, (value, maximum))
    cap('RLIMIT_CPU', CPU_SECONDS)
    cap('RLIMIT_FSIZE', FILE_BYTES)
    cap('RLIMIT_NOFILE', 128)
    cap('RLIMIT_CORE', 0)
    if sys.platform.startswith('linux'):
        cap('RLIMIT_AS', MEMORY_BYTES)
    return None


def windows_job():
    import ctypes as c
    class Basic(c.Structure):
        _fields_ = [('process_time', c.c_int64), ('job_time', c.c_int64), ('flags', c.c_uint32),
                    ('minimum', c.c_size_t), ('maximum', c.c_size_t), ('active', c.c_uint32),
                    ('affinity', c.c_size_t), ('priority', c.c_uint32), ('scheduling', c.c_uint32)]
    class IO(c.Structure):
        _fields_ = [(name, c.c_uint64) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
    class Extended(c.Structure):
        _fields_ = [('basic', Basic), ('io', IO), ('process_memory', c.c_size_t),
                    ('job_memory', c.c_size_t), ('peak_process', c.c_size_t), ('peak_job', c.c_size_t)]
    kernel = c.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [c.c_void_p, c.c_wchar_p]; kernel.CreateJobObjectW.restype = c.c_void_p
    kernel.SetInformationJobObject.argtypes = [c.c_void_p, c.c_int, c.c_void_p, c.c_uint32]
    kernel.SetInformationJobObject.restype = c.c_int
    kernel.GetCurrentProcess.argtypes = []; kernel.GetCurrentProcess.restype = c.c_void_p
    kernel.AssignProcessToJobObject.argtypes = [c.c_void_p, c.c_void_p]; kernel.AssignProcessToJobObject.restype = c.c_int
    kernel.CloseHandle.argtypes = [c.c_void_p]; kernel.CloseHandle.restype = c.c_int
    handle = kernel.CreateJobObjectW(None, None)
    limits = Extended()
    limits.basic.flags = 0x00000002 | 0x00000008 | 0x00000100 | 0x00000200 | 0x00002000
    limits.basic.process_time = CPU_SECONDS * 10_000_000
    limits.basic.active = 8
    limits.process_memory = MEMORY_BYTES
    limits.job_memory = MEMORY_BYTES
    if (not handle or not kernel.SetInformationJobObject(handle, 9, c.byref(limits), c.sizeof(limits))
            or not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess())):
        if handle:
            kernel.CloseHandle(handle)
        raise OSError('Не удалось установить ограничения процесса распознавания.')
    # Keep the handle alive until process exit; children are then terminated too.
    return handle
