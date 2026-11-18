"""Windows service-only DPAPI reader. No subprocess, prompt, fallback or secret logs.

LocalMachine DPAPI is machine-bound, NOT administrator-proof. Protected file/parent
ACLs and the virtual WMS token provide identity isolation; admin/SYSTEM are trusted.
"""
from pathlib import Path


def read_secret(path):
    import os,ctypes as c
    from ctypes import wintypes as w
    if os.name!='nt':raise ValueError('Windows service required')
    a=c.WinDLL('advapi32',use_last_error=True);k=c.WinDLL('kernel32',use_last_error=True);crypt=c.WinDLL('crypt32',use_last_error=True)
    ptr=c.c_void_p
    k.GetCurrentProcess.restype=w.HANDLE
    k.CloseHandle.argtypes=[w.HANDLE];k.CloseHandle.restype=w.BOOL
    k.LocalFree.argtypes=[ptr];k.LocalFree.restype=ptr
    a.OpenProcessToken.argtypes=[w.HANDLE,w.DWORD,c.POINTER(w.HANDLE)];a.OpenProcessToken.restype=w.BOOL
    a.GetTokenInformation.argtypes=[w.HANDLE,c.c_int,ptr,w.DWORD,c.POINTER(w.DWORD)];a.GetTokenInformation.restype=w.BOOL
    a.LookupAccountNameW.argtypes=[w.LPCWSTR,w.LPCWSTR,ptr,c.POINTER(w.DWORD),w.LPWSTR,c.POINTER(w.DWORD),c.POINTER(w.DWORD)];a.LookupAccountNameW.restype=w.BOOL
    a.EqualSid.argtypes=[ptr,ptr];a.EqualSid.restype=w.BOOL
    a.ConvertSidToStringSidW.argtypes=[ptr,c.POINTER(ptr)];a.ConvertSidToStringSidW.restype=w.BOOL
    a.GetSecurityInfo.argtypes=[w.HANDLE,c.c_int,w.DWORD,c.POINTER(ptr),ptr,c.POINTER(ptr),ptr,c.POINTER(ptr)];a.GetSecurityInfo.restype=w.DWORD
    a.GetSecurityDescriptorControl.argtypes=[ptr,c.POINTER(w.WORD),c.POINTER(w.DWORD)];a.GetSecurityDescriptorControl.restype=w.BOOL
    a.GetAclInformation.argtypes=[ptr,ptr,w.DWORD,c.c_int];a.GetAclInformation.restype=w.BOOL
    a.GetAce.argtypes=[ptr,w.DWORD,c.POINTER(ptr)];a.GetAce.restype=w.BOOL
    k.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,ptr,w.DWORD,w.DWORD,w.HANDLE];k.CreateFileW.restype=w.HANDLE
    k.ReadFile.argtypes=[w.HANDLE,ptr,w.DWORD,c.POINTER(w.DWORD),ptr];k.ReadFile.restype=w.BOOL
    class Info(c.Structure):
        _fields_=[('attributes',w.DWORD),('created',w.FILETIME),('accessed',w.FILETIME),('written',w.FILETIME),('volume',w.DWORD),('size_high',w.DWORD),('size_low',w.DWORD),('links',w.DWORD),('index_high',w.DWORD),('index_low',w.DWORD)]
    k.GetFileInformationByHandle.argtypes=[w.HANDLE,c.POINTER(Info)];k.GetFileInformationByHandle.restype=w.BOOL
    class AclInfo(c.Structure):_fields_=[('count',w.DWORD),('used',w.DWORD),('free',w.DWORD)]
    class Blob(c.Structure):_fields_=[('size',w.DWORD),('data',ptr)]
    crypt.CryptUnprotectData.argtypes=[c.POINTER(Blob),ptr,ptr,ptr,ptr,w.DWORD,c.POINTER(Blob)];crypt.CryptUnprotectData.restype=w.BOOL
    def require(ok):
        if not ok:raise ValueError('DDN credential access refused')
    def sid_text(sid):
        out=ptr();require(a.ConvertSidToStringSidW(sid,c.byref(out)))
        try:return c.wstring_at(out)
        finally:k.LocalFree(out)
    size=w.DWORD();domain_size=w.DWORD();kind=w.DWORD()
    a.LookupAccountNameW(None,'NT SERVICE\\DDNWMS-Test-WMS',None,c.byref(size),None,c.byref(domain_size),c.byref(kind))
    require(0<size.value<=256 and domain_size.value<256)
    expected=c.create_string_buffer(size.value);domain=c.create_unicode_buffer(max(domain_size.value,1))
    require(a.LookupAccountNameW(None,'NT SERVICE\\DDNWMS-Test-WMS',expected,c.byref(size),domain,c.byref(domain_size),c.byref(kind)))
    expected_text=sid_text(expected)
    token=w.HANDLE();require(a.OpenProcessToken(k.GetCurrentProcess(),8,c.byref(token)))
    try:
        needed=w.DWORD();a.GetTokenInformation(token,1,None,0,c.byref(needed));require(needed.value>0)
        user=c.create_string_buffer(needed.value);require(a.GetTokenInformation(token,1,user,needed,c.byref(needed)))
        require(a.EqualSid(c.cast(user,c.POINTER(ptr))[0],expected))
    finally:k.CloseHandle(token)
    def check_acl(handle,directory):
        owner=ptr();acl=ptr();descriptor=ptr()
        require(a.GetSecurityInfo(handle,1,5,c.byref(owner),None,c.byref(acl),None,c.byref(descriptor))==0)
        try:
            control=w.WORD();revision=w.DWORD();require(a.GetSecurityDescriptorControl(descriptor,c.byref(control),c.byref(revision)))
            require(control.value & 0x1000 and sid_text(owner) in ('S-1-5-18','S-1-5-32-544') and acl.value)
            info=AclInfo();require(a.GetAclInformation(acl,c.byref(info),c.sizeof(info),2))
            require(1<=info.count<=6);found=False
            for i in range(info.count):
                ace=ptr();require(a.GetAce(acl,i,c.byref(ace)))
                header=c.cast(ace,c.POINTER(c.c_ubyte));mask=c.cast(ace.value+4,c.POINTER(w.DWORD))[0]
                identity=sid_text(ptr(ace.value+8))
                require(header[0]==0 and not(header[1] & 16) and identity in ('S-1-5-18','S-1-5-32-544',expected_text))
                if identity==expected_text:
                    require(mask & ~(0x1200a9 if directory else 0x120089)==0);found=True
            require(found)
        finally:k.LocalFree(descriptor)
    handles=[]
    try:
        file_path=Path(path)
        for p,directory in ((file_path.parent,True),(file_path,False)):
            handle=k.CreateFileW(str(p),0x80000000,1,None,3,0x02000000|0x00200000,None)
            require(handle not in (None,c.c_void_p(-1).value));handles.append(handle)
            information=Info();require(k.GetFileInformationByHandle(handle,c.byref(information)))
            require(not(information.attributes & 0x400))
            if not directory:require(information.links==1 and not information.size_high and 0<information.size_low<=16384)
            check_acl(handle,directory)
        cipher=c.create_string_buffer(information.size_low);count=w.DWORD()
        require(k.ReadFile(handles[-1],cipher,len(cipher),c.byref(count),None));require(count.value==len(cipher))
        incoming=Blob(len(cipher),c.cast(cipher,ptr));out=Blob()
        require(crypt.CryptUnprotectData(c.byref(incoming),None,None,None,None,1,c.byref(out)))
        try:return c.string_at(out.data,out.size)
        finally:
            if out.data:c.memset(out.data,0,out.size);k.LocalFree(out.data)
    finally:
        for handle in reversed(handles):k.CloseHandle(handle)
